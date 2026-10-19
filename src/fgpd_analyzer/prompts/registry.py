"""Prompt registry (DESIGN §8.1): view, replace, reset and export the base prompts.

Templates are Jinja2 (sandboxed, StrictUndefined). Each template declares its
variables implicitly; a replacement must use exactly the same variables, so
that a custom prompt cannot silently drop, e.g., the pattern-gap rule.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from jinja2 import StrictUndefined, meta
from jinja2.sandbox import SandboxedEnvironment

from ..exceptions import PromptError, PromptSchemaMismatch

PROMPT_SET_VERSION = "v1"
PROMPT_NAMES: tuple[str, ...] = (
    "system",
    "codebook_general",
    "chat_conventions",
    "goal_G1",
    "goal_G2",
    "goal_G3",
    "goal_G4",
    "task_detect",
)
_DESCRIPTIONS = {
    "system": "Role, data-handling and output rules",
    "codebook_general": "Units, pattern definition, evidence, non-evidence, agent rules, levels",
    "chat_conventions": "Glossary of chat conventions",
    "goal_G1": "G1 share: definition, indicators, levels, constraints, examples",
    "goal_G2": "G2 listen: definition, indicators, levels, constraints, examples",
    "goal_G3": "G3 deepen: definition, indicators, levels, constraints, examples",
    "goal_G4": "G4 engage: definition, indicators, levels, constraints, examples",
    "task_detect": "Per-goal task instruction (the uncached tail)",
}

_env = SandboxedEnvironment(undefined=StrictUndefined, keep_trailing_newline=True, autoescape=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def template_variables(text: str) -> frozenset[str]:
    try:
        return frozenset(meta.find_undeclared_variables(_env.parse(text)))
    except Exception as e:  # jinja2.TemplateSyntaxError
        raise PromptError(f"invalid template: {e}") from e


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    text: str
    variables: frozenset[str]
    source: Literal["builtin", "custom"]
    version: str = PROMPT_SET_VERSION
    description: str = ""

    @property
    def sha256(self) -> str:
        return _sha(self.text)

    def render(self, **values: Any) -> str:
        missing = self.variables - values.keys()
        if missing:
            raise PromptError(f"prompt {self.name!r} needs {sorted(missing)}")
        return _env.from_string(self.text).render(**values)


def _load_builtin(name: str) -> PromptTemplate:
    path = resources.files("fgpd_analyzer.prompts") / PROMPT_SET_VERSION / f"{name}.md.j2"
    text = path.read_text(encoding="utf-8")
    return PromptTemplate(
        name, text, template_variables(text), "builtin", description=_DESCRIPTIONS[name]
    )


class PromptRegistry:
    """name -> PromptTemplate. Start from the built-in set and override any of them."""

    def __init__(self, overrides: dict[str, str | Path] | Path | str | None = None) -> None:
        self._builtin = {n: _load_builtin(n) for n in PROMPT_NAMES}
        self._prompts = dict(self._builtin)
        if isinstance(overrides, (str, Path)):
            d = Path(overrides)
            if not d.is_dir():
                raise PromptError(f"prompts directory not found: {d}")
            for n in PROMPT_NAMES:
                for cand in (d / f"{n}.md.j2", d / f"{n}.md", d / f"{n}.txt"):
                    if cand.exists():
                        self.set(n, cand)
                        break
        elif overrides:
            for n, t in overrides.items():
                self.set(n, t)

    # ---------------------------------------------------------------- access
    def names(self) -> tuple[str, ...]:
        return PROMPT_NAMES

    def get(self, name: str) -> PromptTemplate:
        try:
            return self._prompts[name]
        except KeyError:
            raise PromptError(f"unknown prompt {name!r}; available: {PROMPT_NAMES}") from None

    def __getitem__(self, name: str) -> PromptTemplate:
        return self.get(name)

    def set(self, name: str, template: str | Path, *, description: str = "") -> PromptTemplate:
        base = self._builtin.get(name)
        if base is None:
            raise PromptError(f"unknown prompt {name!r}; available: {PROMPT_NAMES}")
        text = (
            Path(template).read_text(encoding="utf-8") if isinstance(template, Path) else template
        )
        if not text.strip():
            raise PromptError(f"prompt {name!r} is empty")
        variables = template_variables(text)
        if variables != base.variables:
            raise PromptSchemaMismatch(
                f"prompt {name!r} must use exactly the variables {sorted(base.variables)}; "
                f"got {sorted(variables)}"
            )
        if text == base.text:
            self._prompts[name] = base
        else:
            self._prompts[name] = replace(
                base, text=text, source="custom", description=description or base.description
            )
        return self._prompts[name]

    def reset(self, names: list[str] | None = None) -> None:
        for n in names or list(PROMPT_NAMES):
            self.get(n)
            self._prompts[n] = self._builtin[n]

    @property
    def is_custom(self) -> bool:
        return any(p.source == "custom" for p in self._prompts.values())

    def set_hash(self, extra: dict[str, Any] | None = None) -> str:
        """sha256 over (name, sha256) pairs plus hash-relevant settings."""
        payload = {
            "prompts": sorted((n, p.sha256) for n, p in self._prompts.items()),
            "extra": extra or {},
        }
        return _sha(json.dumps(payload, sort_keys=True, default=str))

    def version(self) -> str:
        if not self.is_custom:
            return PROMPT_SET_VERSION
        return f"custom-{self.set_hash()[:8]}"

    def export(self, directory: Path) -> list[Path]:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        out = []
        for n, p in self._prompts.items():
            path = directory / f"{n}.md.j2"
            path.write_text(p.text, encoding="utf-8")
            out.append(path)
        return out
