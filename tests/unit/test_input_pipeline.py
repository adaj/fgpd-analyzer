from __future__ import annotations

import logging
import warnings
from datetime import timedelta

import pandas as pd
import pytest

from conftest import make_chat
from fgpd_analyzer import (
    AnalyzerConfig,
    ColumnMap,
    ConfigError,
    FGPDAnalyzer,
    InputValidationError,
    PrivacyWarning,
    parse_duration,
)
from fgpd_analyzer._logging import logger
from fgpd_analyzer.pseudonymize import Pseudonymizer, fold
from fgpd_analyzer.render import render_transcript

# ----------------------------------------------------------------- config


@pytest.mark.parametrize(
    "value,seconds",
    [
        ("60s", 60),
        ("2min", 120),
        ("1.5 h", 5400),
        (90, 90),
        (timedelta(minutes=3), 180),
        ("45", 45),
    ],
)
def test_parse_duration(value, seconds):
    assert parse_duration(value).total_seconds() == seconds


@pytest.mark.parametrize("bad", ["two minutes", -5, True, None])
def test_parse_duration_rejects(bad):
    with pytest.raises(ConfigError):
        parse_duration(bad)


def test_config_defaults_and_overrides():
    fa = FGPDAnalyzer(agent_pattern="Clair", influence_lag="90s", goals=["g3", "G1"])
    c = fa.config
    assert c.model == "claude-haiku-5-5" and c.n_runs == 3
    assert c.influence_lag == timedelta(seconds=90)
    assert c.goals == ("G1", "G3")
    with pytest.raises(ConfigError):
        FGPDAnalyzer(goals=["G5"])
    with pytest.raises(ConfigError):
        FGPDAnalyzer(not_an_option=1)


def test_fingerprint_ignores_post_hoc_fields_and_key():
    a = AnalyzerConfig()
    assert a.fingerprint() == a.replace(influence_lag="5min", api_key="sk-x").fingerprint()
    assert a.fingerprint() != a.replace(unit_gap="30s").fingerprint()
    assert a.redacted()["api_key"] is None
    assert a.replace(api_key="sk-secret").redacted()["api_key"] == "***"


# ----------------------------------------------------------------- columns


def test_column_auto_mapping_dev_headers():
    m, problems = ColumnMap().resolve(["Unnamed: 0", "Group", "Username", "Timestamp", "Message"])
    assert m == {
        "group": "Group",
        "username": "Username",
        "timestamp": "Timestamp",
        "text": "Message",
    }
    assert problems == []


def test_column_explicit_mapping(small_chat):
    df = small_chat.rename(columns={"Message": "conteudo_msg"})
    fa = FGPDAnalyzer()
    rep = fa.validate_input(df)
    assert not rep.ok and "E001" in rep.codes()
    assert fa.validate_input(df, columns={"text": "conteudo_msg"}).ok
    with pytest.raises(ConfigError):
        ColumnMap.coerce({"txt": "x"})


# ----------------------------------------------------------------- units and roles


def test_units_gap_and_agent(small_chat):
    p = FGPDAnalyzer().prepare(small_chat)
    pg = p.groups["g1"]
    ids = [(u.speaker, len(u.message_ids), u.is_agent) for u in pg.units]
    assert ids[0] == ("AG1", 1, True)
    assert ids[1] == ("S1", 2, False)  # 30 s apart -> merged
    assert ids[2] == ("S2", 1, False)  # 61 s later -> split
    assert ids[3] == ("S2", 1, False)
    assert pg.n_student_messages == 10 and pg.n_agent_messages == 2 and pg.n_students == 2
    msgs = p.messages()
    assert "text" not in msgs.columns
    assert msgs["unit_id"].tolist()[:3] == ["u001", "u002", "u002"]


def test_agent_units_never_merge():
    df = make_chat(
        [
            ("g", "Clair", "10:00:00", "a"),
            ("g", "Clair", "10:00:05", "b"),
            *[("g", f"Stu{i % 2}", f"10:0{i}:30", "texto qualquer") for i in range(1, 9)],
        ]
    )
    pg = FGPDAnalyzer(agent_pattern="clair").prepare(df).groups["g"]
    assert [u.is_agent for u in pg.units[:2]] == [True, True]


def test_unit_gap_boundary_exactly_60s():
    df = make_chat(
        [("g", "Ana", "10:00:00", "um"), ("g", "Ana", "10:01:00", "dois")]
        + [("g", f"P{i % 2}", f"10:0{i + 2}:00", "x y z") for i in range(8)]
    )
    pg = FGPDAnalyzer().prepare(df).groups["g"]
    assert len(pg.units[0].message_ids) == 2  # 60 s <= unit_gap


def test_regex_and_extra_agent_patterns():
    df = make_chat(
        [("g", "Prof. Silva", "10:00:00", "oi"), ("g", "bot-07", "10:00:10", "oi")]
        + [("g", f"Al{i % 2}", f"10:0{i + 1}:00", "x y z") for i in range(8)]
    )
    pg = (
        FGPDAnalyzer(
            agent_pattern=r"^bot-\d+$", agent_pattern_regex=True, extra_agent_patterns=[r"prof"]
        )
        .prepare(df)
        .groups["g"]
    )
    assert pg.n_agent_messages == 2


# ----------------------------------------------------------------- pseudonymization


def test_pseudonyms_and_in_text_replacement(small_chat):
    fa = FGPDAnalyzer()
    p = fa.prepare(small_chat)
    t = render_transcript(p.groups["g1"])
    assert "Mariana" not in t and "Joao" not in t
    assert "concordo S1" in t and "S2, pode explicar" in t
    assert "| AG1 [AGENT] |" in t


def test_short_names_not_replaced_and_accents():
    ps = Pseudonymizer(min_len=3).fit(["A", "José", "agente 1"], [False, False, True])
    assert ps("a casa e o jose e JOSÉ") == "a casa e o S2 e S2"
    assert ps.skipped_short == {"A"}
    assert fold("ÁÉÇ") == "aec" and len(fold("ﬁ")) == 1


def test_extra_names_replaced():
    ps = Pseudonymizer(min_len=3, extra_names=("Zezinho",)).fit(["Ana"], [False])
    assert ps("o zezinho disse") == "o N1 disse"


def test_pseudonymize_false_warns(small_chat):
    with pytest.warns(PrivacyWarning):
        p = FGPDAnalyzer(pseudonymize=False).prepare(small_chat)
    assert "Mariana" in render_transcript(p.groups["g1"])


def test_sentinel_name_never_logged(small_chat, caplog):
    df = small_chat.copy()
    df.loc[1, "Message"] = "o Sentinelius falou"
    fa = FGPDAnalyzer(extra_names=["Sentinelius"])
    p = fa.prepare(df)
    with caplog.at_level(logging.DEBUG, logger="fgpd_analyzer"):
        logger.warning("user Mariana and Sentinelius did something")
    assert "Mariana" not in caplog.text and "Sentinelius" not in caplog.text
    assert "Sentinelius" not in render_transcript(p.groups["g1"])
    assert "Sentinelius" not in p.report.to_text()


# ----------------------------------------------------------------- validation codes


def _codes(df, **kw):
    return FGPDAnalyzer(**kw).validate_input(df).codes()


def test_missing_values_and_bad_timestamps(small_chat):
    df = small_chat.copy()
    df.loc[2, "Username"] = ""
    assert "E003" in _codes(df)
    df = small_chat.copy()
    df.loc[2, "Timestamp"] = "ontem"
    assert "E004" in _codes(df)
    df = small_chat.copy()
    df.loc[2, "Timestamp"] = "2026-09-17T10:01:00+03:00"
    assert "E006" in _codes(df)


def test_duplicates_and_empty(small_chat):
    df = pd.concat([small_chat, small_chat.iloc[[3]]], ignore_index=True)
    df.loc[0, "Message"] = "   "
    codes = _codes(df)
    assert "W001" in codes and "W002" in codes


def test_message_id_duplicates(small_chat):
    df = small_chat.assign(message_id=["a"] * len(small_chat))
    assert "E008" in _codes(df)
    ok = small_chat.assign(message_id=[f"x{i}" for i in range(len(small_chat))])
    assert FGPDAnalyzer().load(ok)["message_id"].iloc[0] == "x0"


def test_out_of_order_sorted(small_chat):
    df = small_chat.iloc[::-1].reset_index(drop=True)
    assert "W005" in _codes(df)
    fa = FGPDAnalyzer(sort=False)
    assert not fa.validate_input(df).ok
    msgs = FGPDAnalyzer().load(df)
    assert msgs["timestamp"].is_monotonic_increasing


def test_ties_and_long_gap(small_chat):
    df = small_chat.copy()
    df.loc[4, "Timestamp"] = df.loc[3, "Timestamp"]
    df.loc[11, "Timestamp"] = "2026-09-17 12:00:00"
    codes = _codes(df)
    assert "W006" in codes and "W007" in codes


def test_eligibility_rules(small_chat):
    tiny = small_chat.iloc[:5]
    p = FGPDAnalyzer().prepare(tiny)
    assert p.groups["g1"].eligibility == "insufficient_data" and "W003" in p.report.codes()
    solo = small_chat[small_chat.Username != "Joao"]
    assert (
        FGPDAnalyzer(min_student_messages=3).prepare(solo).groups["g1"].eligibility
        == "insufficient_data"
    )
    p = FGPDAnalyzer(max_prompt_tokens=1000).prepare(small_chat)
    assert p.groups["g1"].eligibility == "too_long" and "W010" in p.report.codes()


def test_agent_share_flag_and_exclusion(small_chat):
    p = FGPDAnalyzer(max_agent_share=0.1).prepare(small_chat)
    assert "W009" in p.report.codes() and p.groups["g1"].eligibility == "eligible"
    p = FGPDAnalyzer(max_agent_share=0.1, exclude_flagged=True).prepare(small_chat)
    assert p.groups["g1"].eligibility == "excluded" and p.eligible == []


def test_agent_pattern_problems(small_chat):
    assert "W008" in _codes(small_chat, agent_pattern="Clair")
    codes = _codes(small_chat, agent_pattern="a")  # matches everyone
    assert "E007" in codes


def test_no_agent_warning(small_chat):
    assert "W004" in _codes(small_chat[small_chat.Username != "agente 1"])


def test_strict_escalates(small_chat):
    rep = FGPDAnalyzer(strict=True, max_agent_share=0.1).validate_input(small_chat)
    assert not rep.ok
    with pytest.raises(InputValidationError) as ei:
        rep.raise_if_errors()
    assert ei.value.report is rep


def test_validator_never_raises_on_garbage():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for df in (
            pd.DataFrame(),
            pd.DataFrame({"x": [1, 2]}),
            pd.DataFrame(
                {"group": [None], "username": [None], "timestamp": [None], "text": [None]}
            ),
        ):
            assert not FGPDAnalyzer().validate_input(df).ok


def test_file_formats(tmp_path, small_chat):
    fa = FGPDAnalyzer()
    for name, write in (
        ("c.tsv", lambda p: small_chat.to_csv(p, sep="\t")),
        ("c.csv", lambda p: small_chat.to_csv(p, sep=";", index=False)),
        ("c.xlsx", lambda p: small_chat.to_excel(p, index=False)),
        ("c.parquet", lambda p: small_chat.to_parquet(p)),
    ):
        path = tmp_path / name
        write(path)
        assert fa.validate_input(path).ok, name
    with pytest.raises(FileNotFoundError):
        fa.validate_input(tmp_path / "missing.csv")


def test_preview_unknown_group(small_chat):
    with pytest.raises(KeyError):
        FGPDAnalyzer().preview(small_chat, group="nope")


# ----------------------------------------------------------------- dev data (private, optional)


@pytest.mark.devdata
def test_dev_file_expectations(dev_data):
    fa = FGPDAnalyzer(exclude_flagged=True)
    p = fa.prepare(dev_data)
    assert p.report.ok
    elig = {g: pg.eligibility for g, pg in p.groups.items()}
    assert list(elig.values()).count("eligible") == 6
    assert list(elig.values()).count("excluded") == 1
    everyone = next(g for g in elig if g.startswith("everyone"))
    assert elig[everyone] == "insufficient_data"
    by_group = {(i.code, i.group) for i in p.report.issues}
    assert ("W003", everyone) in by_group and ("W004", everyone) in by_group
