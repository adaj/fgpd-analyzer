"""Pseudonymization (DESIGN §2.3).

Students become S1..Sk and agents AG1..AGm, in order of first appearance in the
group. Names are replaced inside message text with word boundaries, ignoring
case and accents, but only when the name has at least `min_len` characters:
single-letter usernames such as ``A`` or ``E`` would otherwise corrupt the
Portuguese words *a*, *e*, *o*. Pseudonymized data are still personal data.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field


def _fold_char(c: str) -> str:
    base = unicodedata.normalize("NFD", c)[0]
    low = base.lower()
    return low if len(low) == 1 else base


def fold(text: str) -> str:
    """Lower-case, accent-free copy with exactly one output char per input char,
    so that match positions in the folded text are valid in the original.
    Callers pass NFC text (validation normalizes every column)."""
    return "".join(_fold_char(c) for c in text)


@dataclass
class Pseudonymizer:
    """Builds the name -> pseudonym map of one group and rewrites text.

    Subclass and override `pseudonym_for` to use other labels.
    """

    min_len: int = 3
    extra_names: tuple[str, ...] = ()
    mapping: dict[str, str] = field(default_factory=dict)
    skipped_short: set[str] = field(default_factory=set)
    _rx: re.Pattern[str] | None = None
    _targets: dict[str, str] = field(default_factory=dict)

    prefixes: tuple[str, str] = ("S", "AG")
    collision: bool = False

    def pseudonym_for(self, kind: str, index: int) -> str:
        return f"{self.prefixes[0]}{index}" if kind == "student" else f"{self.prefixes[1]}{index}"

    def fit(
        self,
        usernames: Iterable[str],
        is_agent: Iterable[bool],
        other_names: Iterable[str] = (),
    ) -> Pseudonymizer:
        """`other_names`: participants of other groups; mentions of them in this
        group's text become ``[NAME]``."""
        names = list(usernames)
        agents = list(is_agent)
        for cand in (("S", "AG"), ("ST", "AGT"), ("STU", "AGENT")):
            rx = re.compile(rf"^(?:{cand[0]}|{cand[1]}|N)\d+$", re.IGNORECASE)
            if not any(rx.match(n.strip()) for n in names):
                if cand != ("S", "AG"):
                    self.collision = True
                self.prefixes = cand
                break
        n = {"student": 0, "agent": 0}
        for name, agent in zip(names, agents, strict=True):
            if name in self.mapping:
                continue
            kind = "agent" if agent else "student"
            n[kind] += 1
            self.mapping[name] = self.pseudonym_for(kind, n[kind])
        targets: dict[str, str] = {}
        for name, pseudo in self.mapping.items():
            self._add_target(targets, name, pseudo)
        for k, name in enumerate(self.extra_names, 1):
            self._add_target(targets, name, f"N{k}")
        for name in other_names:
            if name not in self.mapping:
                self._add_target(targets, name, "[NAME]")
        self._targets = targets
        if targets:
            alts = sorted(targets, key=len, reverse=True)
            self._rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in alts) + r")(?!\w)")
        return self

    def _add_target(self, targets: dict[str, str], name: str, pseudo: str) -> None:
        name = name.strip()
        if len(name) < self.min_len:
            self.skipped_short.add(name)
            return
        targets.setdefault(fold(name), pseudo)

    def __call__(self, text: str) -> str:
        if self._rx is None or not text:
            return text
        text = unicodedata.normalize("NFC", text)
        folded = fold(text)
        out, last = [], 0
        for m in self._rx.finditer(folded):
            out.append(text[last : m.start()])
            out.append(self._targets[m.group(0)])
            last = m.end()
        out.append(text[last:])
        return "".join(out)

    def speaker(self, username: str) -> str:
        return self.mapping[username]
