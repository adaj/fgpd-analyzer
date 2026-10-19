"""Pseudonymization (DESIGN §2.3).

Students become S1..Sk and agents AG1..AGm, in order of first appearance in the
group. Names are replaced inside message text with word boundaries, ignoring
case and accents, but only when the name has at least `min_len` characters:
single-letter usernames such as ``A`` or ``E`` would otherwise corrupt the
Portuguese words *a*, *e*, *o*. Pseudonymized data are still personal data.

With a name key (`aliases`: real name -> username, as produced when the export
replaced names by letters), every variant of a real name is replaced too: the full
name, each part of a multi-part name ("joão pedro" -> "joão", "berna_erika" ->
"erika"), and, with `fuzzy=True`, a single-word name missing or having one extra
letter ("fraier" for "fraifer"; a missing letter only for names of 7+ letters,
since "banca" is "bianca" minus one letter). Substitutions are not accepted
("branca" is not "bianca"). A name of someone outside the group becomes ``[NAME]``; a name part
shared by two people becomes ``[NAME]``.
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
    fuzzy: bool = True
    fuzzy_min_len: int = 5
    fuzzy_hits: int = 0
    _dels: dict[str, str] = field(default_factory=dict)

    def pseudonym_for(self, kind: str, index: int) -> str:
        return f"{self.prefixes[0]}{index}" if kind == "student" else f"{self.prefixes[1]}{index}"

    def fit(
        self,
        usernames: Iterable[str],
        is_agent: Iterable[bool],
        other_names: Iterable[str] = (),
        aliases: dict[str, str] | None = None,
    ) -> Pseudonymizer:
        """`other_names`: participants of other groups; mentions of them in this
        group's text become ``[NAME]``. `aliases`: real name -> username (the
        researcher's name key)."""
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
        if aliases:
            self._add_aliases(targets, aliases)
        self._targets = targets
        self._dels = {}
        if self.fuzzy:
            for t, pseudo in targets.items():
                # "missing letter" matches only for names of 7+ letters: shorter
                # ones collide with common words ("bianca" -> "banca").
                if len(t) >= max(self.fuzzy_min_len, 7) and t.isalpha():
                    for i in range(len(t)):
                        d = t[:i] + t[i + 1 :]
                        if d not in targets or targets[d] == "[NAME]":
                            prev = self._dels.setdefault(d, pseudo)
                            if prev != pseudo:
                                self._dels[d] = "[NAME]"
            # A generic [NAME] target (e.g. a vocative "fraier") that is one letter
            # away from a known name takes that person's pseudonym.
            for t, pseudo in list(targets.items()):
                if pseudo != "[NAME]" or len(t) < self.fuzzy_min_len:
                    continue
                hit = self._dels.get(t)
                if hit is None:
                    hit = next(
                        (
                            targets[c]
                            for c in (t[:i] + t[i + 1 :] for i in range(len(t)))
                            if c in targets and targets[c] != "[NAME]"
                        ),
                        None,
                    )
                if hit and hit != "[NAME]":
                    targets[t] = hit
        if targets:
            alts = sorted(targets, key=len, reverse=True)
            self._rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in alts) + r")(?!\w)")
        return self

    def _add_aliases(self, targets: dict[str, str], aliases: dict[str, str]) -> None:
        full: dict[str, str] = {}
        parts: dict[str, set[str]] = {}
        for real, username in aliases.items():
            pseudo = self.mapping.get(username)
            if pseudo is None or pseudo.startswith(self.prefixes[1]):
                pseudo = "[NAME]"  # not in this group (or an agent account)
            real = real.strip()
            full[real] = pseudo
            tokens = [t for t in re.split(r"[\s_.\-]+", real) if t]
            if len(tokens) > 1:
                for t in tokens:
                    parts.setdefault(fold(t), set()).add(pseudo)
        for real, pseudo in full.items():
            key = fold(real)
            if len(key) >= self.min_len:
                targets[key] = pseudo  # the key wins over username-derived targets
            else:
                self.skipped_short.add(real)
        for t, pseudos in parts.items():
            if len(t) >= self.min_len and targets.get(t, "[NAME]") == "[NAME]":
                targets[t] = pseudos.pop() if len(pseudos) == 1 else "[NAME]"

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
        result = "".join(out)
        if self._dels:
            result = re.sub(r"\w+", self._fuzzy, result)
        return result

    def _fuzzy(self, m: re.Match[str]) -> str:
        word = m.group(0)
        if len(word) < self.fuzzy_min_len or not word.isalpha():
            return word
        f = fold(word)
        hit = self._dels.get(f)  # the word is a name missing one letter
        if hit is None:  # the word is a name with one extra letter
            for i in range(len(f)):
                cand = f[:i] + f[i + 1 :]
                if cand in self._targets and len(cand) >= self.fuzzy_min_len:
                    hit = self._targets[cand]
                    break
        if hit is None:
            return word
        self.fuzzy_hits += 1
        return hit

    def speaker(self, username: str) -> str:
        return self.mapping[username]
