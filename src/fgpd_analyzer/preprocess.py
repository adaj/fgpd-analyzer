"""Deterministic preprocessing (DESIGN §2): roles, message ids, Student
Contribution Units (SCUs), pseudonyms and eligibility. No LLM involved."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from ._logging import redactor
from .config import AnalyzerConfig
from .pseudonymize import Pseudonymizer
from .validation import ValidationReport

Eligibility = Literal["eligible", "insufficient_data", "too_long", "excluded"]

# Rough size of the cached prompt prefix (system + codebook) until M2 measures it.
PROMPT_OVERHEAD_TOKENS = 6000
CHARS_PER_TOKEN = 3.0  # conservative for PT/NL chat text


# Words that often open an agent message before a comma but are not names.
_NOT_NAMES = frozenset(
    [
        "ola",
        "olá",
        "oi",
        "ei",
        "opa",
        "ok",
        "okay",
        "certo",
        "otimo",
        "ótimo",
        "muito",
        "bem",
        "bom",
        "boa",
        "legal",
        "entao",
        "então",
        "pessoal",
        "gente",
        "galera",
        "turma",
        "sim",
        "nao",
        "não",
        "agora",
        "parabens",
        "parabéns",
        "obrigado",
        "obrigada",
        "perfeito",
        "exato",
        "interessante",
        "verdade",
        "claro",
        "beleza",
        "show",
        "vamos",
        "isso",
        "você",
        "vocês",
        "voce",
        "voces",
        "vcs",
        "todos",
        "todas",
        "ele",
        "ela",
        "eles",
        "elas",
        "alguem",
        "alguém",
        "hello",
        "hi",
        "hey",
        "great",
        "good",
        "well",
        "so",
        "yes",
        "no",
        "now",
        "thanks",
        "perfect",
        "exactly",
        "interesting",
        "true",
        "sure",
        "right",
        "everyone",
        "guys",
        "team",
        "nice",
        "also",
        "and",
        "but",
        "hmm",
        "you",
        "them",
        "him",
        "her",
        "someone",
    ]
)
_VOCATIVE_RX = (
    re.compile(r"^\s*([^\W\d_]{3,})\s*,\s", re.UNICODE),
    re.compile(r"^\s*(?:ol[aá]|oi|ei|hey|hi|hello)[\s,!]+([^\W\d_]{3,})\b", re.IGNORECASE),
    re.compile(r",\s*([^\W\d_]{3,})\s*[?!]\s*$", re.UNICODE),
    re.compile(
        r"\b(?:ajudar|ajude|perguntar\s+(?:a|ao|à)|pergunte\s+(?:a|ao|à)|help|ask)\s+"
        r"([^\W\d_]{3,})\s*[?!.,]",
        re.IGNORECASE,
    ),
)


def vocative_names(agent_texts: list[str]) -> set[str]:
    """Words that agent messages use to address someone ("Ana, what do you think?").
    In exports where usernames were replaced by letters, these are often real names."""
    found: set[str] = set()
    for t in agent_texts:
        for rx in _VOCATIVE_RX:
            for m in rx.finditer(t):
                w = m.group(1)
                if w.lower() not in _NOT_NAMES:
                    found.add(w)
    return found


def role_matcher(config: AnalyzerConfig):  # type: ignore[no-untyped-def]
    pats = (config.agent_pattern, *config.extra_agent_patterns)
    if config.agent_pattern_regex:
        rxs = [re.compile(p, re.IGNORECASE) for p in pats]
        return lambda name: any(r.search(name) for r in rxs)
    low = [p.lower() for p in pats]
    return lambda name: any(p in name.lower() for p in low)


@dataclass(frozen=True)
class Unit:
    unit_id: str
    order: int
    speaker: str
    is_agent: bool
    message_ids: tuple[str, ...]
    t_start: pd.Timestamp
    t_end: pd.Timestamp
    t_min: float  # minutes since the group's first message (unit start)
    text: str  # pseudonymized messages joined with " ⏎ "


@dataclass
class PreparedGroup:
    group: str
    messages: pd.DataFrame  # one row per message, no text column
    units: list[Unit]
    pseudonymizer: Pseudonymizer
    n_student_messages: int
    n_agent_messages: int
    n_students: int
    duration_min: float
    agent_share: float
    flagged_agent_share: bool
    est_tokens: int
    eligibility: Eligibility
    reasons: list[str] = field(default_factory=list)

    @property
    def unit_index(self) -> dict[str, Unit]:
        return {u.unit_id: u for u in self.units}

    @property
    def student_unit_ids(self) -> list[str]:
        return [u.unit_id for u in self.units if not u.is_agent]

    @property
    def agent_unit_ids(self) -> list[str]:
        return [u.unit_id for u in self.units if u.is_agent]

    @property
    def n_student_units(self) -> int:
        return sum(not u.is_agent for u in self.units)

    @property
    def speakers(self) -> list[str]:
        return list(dict.fromkeys(u.speaker for u in self.units))


def _build_units(
    msgs: pd.DataFrame, texts: list[str], gap_s: float
) -> tuple[list[Unit], list[str]]:
    units: list[Unit] = []
    unit_of_msg: list[str] = []
    t0 = msgs["timestamp"].iloc[0]
    cur: dict | None = None  # type: ignore[type-arg]
    width = max(3, len(str(len(msgs))))  # zero-padded ids keep lexicographic order

    def close() -> None:
        assert cur is not None
        k = len(units) + 1
        units.append(
            Unit(
                unit_id=f"u{k:0{width}d}",
                order=k,
                speaker=cur["speaker"],
                is_agent=cur["agent"],
                message_ids=tuple(cur["ids"]),
                t_start=cur["t0"],
                t_end=cur["t1"],
                t_min=round((cur["t0"] - t0).total_seconds() / 60, 4),
                text=" ⏎ ".join(cur["texts"]),
            )
        )

    for (_, row), text in zip(msgs.iterrows(), texts, strict=True):
        agent, spk, ts = bool(row["is_agent"]), row["speaker"], row["timestamp"]
        same = (
            cur is not None
            and not agent
            and not cur["agent"]
            and cur["speaker"] == spk
            and (ts - cur["t1"]).total_seconds() <= gap_s
        )
        if same:
            assert cur is not None
            cur["ids"].append(row["message_id"])
            cur["texts"].append(text)
            cur["t1"] = ts
        else:
            if cur is not None:
                close()
            cur = {
                "speaker": spk,
                "agent": agent,
                "ids": [row["message_id"]],
                "texts": [text],
                "t0": ts,
                "t1": ts,
            }
        unit_of_msg.append(f"u{len(units) + 1:0{width}d}")
    if cur is not None:
        close()
    return units, unit_of_msg


def prepare_group(
    group: str,
    sub: pd.DataFrame,
    config: AnalyzerConfig,
    report: ValidationReport,
    other_names: tuple[str, ...] = (),
) -> PreparedGroup:
    is_agent_fn = role_matcher(config)
    msgs = sub.reset_index(drop=True).copy()
    msgs["is_agent"] = msgs["username"].map(lambda u: bool(is_agent_fn(str(u))))
    if "message_id" not in msgs or msgs["message_id"].isna().all():
        msgs["message_id"] = [f"m{i:04d}" for i in range(1, len(msgs) + 1)]
    msgs["seq"] = range(1, len(msgs) + 1)

    ps = Pseudonymizer(min_len=config.min_name_len_in_text, extra_names=config.extra_names)
    ps.fit(msgs["username"].astype(str), msgs["is_agent"], other_names=other_names)
    redactor.register(*ps.mapping.keys(), *config.extra_names)
    if config.pseudonymize and ps.collision:
        report.add(
            "W014",
            "warning",
            f"some usernames look like pseudonyms; using prefixes {ps.prefixes}",
            group=str(group),
        )
    if config.pseudonymize:
        msgs["speaker"] = msgs["username"].map(ps.speaker)
        texts = [ps(str(t)) for t in msgs["text"]]
    else:
        msgs["speaker"] = msgs["username"].astype(str)
        texts = [str(t) for t in msgs["text"]]

    units, unit_of_msg = _build_units(msgs, texts, config.unit_gap.total_seconds())
    msgs["unit_id"] = unit_of_msg
    t0 = msgs["timestamp"].iloc[0]
    msgs["t_min"] = ((msgs["timestamp"] - t0).dt.total_seconds() / 60).round(4)

    n_agent = int(msgs["is_agent"].sum())
    n_student = len(msgs) - n_agent
    n_students = int(msgs.loc[~msgs["is_agent"], "username"].nunique())
    duration = float((msgs["timestamp"].iloc[-1] - t0).total_seconds() / 60)
    share = n_agent / len(msgs) if len(msgs) else 0.0
    chars = sum(len(u.text) + 30 for u in units)  # 30 ~ id/time/speaker prefix per line
    est_tokens = int(chars / CHARS_PER_TOKEN) + PROMPT_OVERHEAD_TOKENS

    eligibility: Eligibility = "eligible"
    reasons: list[str] = []
    g = str(group)
    if n_agent == 0:
        report.add("W004", "warning", "group has no agent messages", group=g)
    flagged = share > config.max_agent_share
    if flagged:
        report.add(
            "W009",
            "warning",
            f"agent share {share:.0%} is above max_agent_share {config.max_agent_share:.0%}",
            group=g,
            hint="set exclude_flagged=True to leave this group out",
        )
    if n_student < config.min_student_messages or n_students < config.min_students:
        eligibility = "insufficient_data"
        reasons.append(
            f"{n_student} student messages / {n_students} students "
            f"(min {config.min_student_messages} / {config.min_students})"
        )
        report.add("W003", "warning", f"insufficient data: {reasons[-1]}", group=g)
    elif est_tokens > config.max_prompt_tokens:
        eligibility = "too_long"
        reasons.append(f"estimated {est_tokens} prompt tokens > {config.max_prompt_tokens}")
        report.add("W010", "warning", reasons[-1], group=g)
    elif flagged and config.exclude_flagged:
        eligibility = "excluded"
        reasons.append("agent share above max_agent_share (exclude_flagged=True)")
    if config.pseudonymize and ps.skipped_short:
        report.add(
            "W013",
            "warning",
            f"{len(ps.skipped_short)} name(s) shorter than {config.min_name_len_in_text} "
            "characters were not replaced inside message text",
            group=g,
            hint="residual re-identification risk; use extra_names for known nicknames",
        )

    cols = [
        "group",
        "row_index",
        "message_id",
        "unit_id",
        "seq",
        "speaker",
        "is_agent",
        "timestamp",
        "t_min",
    ]
    msgs["group"] = g
    return PreparedGroup(
        group=g,
        messages=msgs[cols].copy(),
        units=units,
        pseudonymizer=ps,
        n_student_messages=n_student,
        n_agent_messages=n_agent,
        n_students=n_students,
        duration_min=round(duration, 3),
        agent_share=round(share, 4),
        flagged_agent_share=flagged,
        est_tokens=est_tokens,
        eligibility=eligibility,
        reasons=reasons,
    )


def prepare(
    clean: pd.DataFrame, config: AnalyzerConfig, report: ValidationReport
) -> dict[str, PreparedGroup]:
    groups: dict[str, PreparedGroup] = {}
    is_agent_fn = role_matcher(config)
    names = clean["username"].astype(str).unique()
    agent_names = [n for n in names if is_agent_fn(n)]
    student_names = tuple(n for n in names if not is_agent_fn(n))
    vocatives: tuple[str, ...] = ()
    if config.redact_vocatives:
        agent_rows = clean["username"].astype(str).map(lambda u: bool(is_agent_fn(u)))
        found = vocative_names(clean.loc[agent_rows, "text"].astype(str).tolist())
        vocatives = tuple(sorted(found))
        if vocatives:
            report.add(
                "W016",
                "warning",
                f"{len(vocatives)} word(s) used by agents to address someone were replaced by "
                "[NAME] in all messages",
                hint="inspect them locally with FGPDAnalyzer.name_candidates(); add other known "
                "names with extra_names",
            )
    if not agent_names:
        report.add(
            "W008",
            "warning",
            f"agent pattern {config.agent_pattern!r} matches no username",
            hint="check agent_pattern (e.g. 'Clair')",
        )
    if len(agent_names) == len(names):
        report.add(
            "E007",
            "error",
            "no student messages in the input",
            hint="agent_pattern matches every username",
        )
        return groups
    for g, sub in clean.groupby("group", sort=True):
        users = sub["username"].astype(str).unique()
        n_ag = sum(bool(is_agent_fn(u)) for u in users)
        if len(users) and n_ag / len(users) > 0.5:
            report.add(
                "W008",
                "warning",
                f"agent pattern matches {n_ag} of {len(users)} usernames",
                group=str(g),
            )
        others = tuple(n for n in student_names if n not in set(users)) + vocatives
        groups[str(g)] = prepare_group(str(g), sub, config, report, other_names=others)
    rows = [
        {
            "group": p.group,
            "n_messages": len(p.messages),
            "n_student": p.n_student_messages,
            "n_agent": p.n_agent_messages,
            "n_students": p.n_students,
            "n_student_units": p.n_student_units,
            "duration_min": p.duration_min,
            "agent_share": p.agent_share,
            "flagged_agent_share": p.flagged_agent_share,
            "est_tokens": p.est_tokens,
            "eligibility": p.eligibility,
        }
        for p in groups.values()
    ]
    report.info = pd.DataFrame(rows)
    return groups
