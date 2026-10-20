"""Prompt registry (DESIGN §8.1): view, replace, reset and export the base prompts.

The source of the prompts stage of the pipeline (input → units → **prompts**
→ requests → ...). The built-in prompts are the files
``prompts/v1/<name>.md.j2``; the same texts form the human codebook.

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

#: Version of the built-in prompt set.
PROMPT_SET_VERSION = "v1"
#: Names of the base prompts, in request order.
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
    """SHA-256 hex digest of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def template_variables(text: str) -> frozenset[str]:
    """Return the undeclared variables of a Jinja2 template.

    Parameters
    ----------
    text : str
        Template source.

    Returns
    -------
    frozenset of str

    Raises
    ------
    PromptError
        If the template has a syntax error.
    """
    try:
        return frozenset(meta.find_undeclared_variables(_env.parse(text)))
    except Exception as e:  # jinja2.TemplateSyntaxError
        raise PromptError(f"invalid template: {e}") from e


@dataclass(frozen=True)
class PromptTemplate:
    """One prompt template (immutable).

    Attributes
    ----------
    name : str
        Prompt name, one of `PROMPT_NAMES`.
    text : str
        Jinja2 source (sandboxed, ``StrictUndefined``).
    variables : frozenset of str
        Template variables used by `text`.
    source : {"builtin", "custom"}
        Whether `text` is the packaged text.
    version : str
        Prompt-set version of the built-in file (``"v1"``).
    description : str
        One-line description.
    """

    name: str
    text: str
    variables: frozenset[str]
    source: Literal["builtin", "custom"]
    version: str = PROMPT_SET_VERSION
    description: str = ""

    @property
    def sha256(self) -> str:
        """SHA-256 hex digest of `text`."""
        return _sha(self.text)

    def render(self, **values: Any) -> str:
        """Render the template.

        Parameters
        ----------
        **values
            A value for every name in `variables`; extra values are ignored.

        Returns
        -------
        str

        Raises
        ------
        PromptError
            If a variable has no value.
        """
        missing = self.variables - values.keys()
        if missing:
            raise PromptError(f"prompt {self.name!r} needs {sorted(missing)}")
        return _env.from_string(self.text).render(**values)


def _load_builtin(name: str) -> PromptTemplate:
    """Load one packaged prompt of the current prompt-set version."""
    path = resources.files("fgpd_analyzer.prompts") / PROMPT_SET_VERSION / f"{name}.md.j2"
    text = path.read_text(encoding="utf-8")
    return PromptTemplate(
        name, text, template_variables(text), "builtin", description=_DESCRIPTIONS[name]
    )


class PromptRegistry:
    """The set of prompt templates used by an analyzer.

    Starts from the built-in prompts; any of them can be replaced, as long as
    the replacement uses exactly the same template variables.

    Parameters
    ----------
    overrides : dict, str or pathlib.Path, optional
        Either a mapping ``{name: template text or Path}``, or a directory
        that may contain ``<name>.md.j2``, ``<name>.md`` or ``<name>.txt`` for
        any prompt name (first match wins; other files are ignored).

    Raises
    ------
    PromptError
        If the directory does not exist, a name is unknown, or a template is
        empty or invalid.
    PromptSchemaMismatch
        If an override does not use exactly the built-in variables.

    Notes
    -----
    Prompt names: ``system``, ``codebook_general``, ``chat_conventions``,
    ``goal_G1`` … ``goal_G4`` and ``task_detect`` (see `PROMPT_NAMES`).
    A registry with any custom prompt has version ``"custom-<hash8>"``.

    Examples
    --------
    >>> reg = PromptRegistry()
    >>> reg.version()
    'v1'
    >>> sorted(reg.get("codebook_general").variables)  # doctest: +SKIP
    ['justification_language', 'pattern_gap_min', 'pattern_gap_units']
    """

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
        """Return the prompt names, in request order."""
        return PROMPT_NAMES

    def get(self, name: str) -> PromptTemplate:
        """Return the current template of one prompt.

        Raises
        ------
        PromptError
            If `name` is unknown.
        """
        try:
            return self._prompts[name]
        except KeyError:
            raise PromptError(f"unknown prompt {name!r}; available: {PROMPT_NAMES}") from None

    def __getitem__(self, name: str) -> PromptTemplate:
        """Return the current template of one prompt (same as `get`)."""
        return self.get(name)

    def set(self, name: str, template: str | Path, *, description: str = "") -> PromptTemplate:
        """Replace one prompt.

        Parameters
        ----------
        name : str
            Prompt name.
        template : str or pathlib.Path
            Template text, or a `Path` to a UTF-8 file (a ``str`` is always
            text).
        description : str, optional
            New description; defaults to the built-in one.

        Returns
        -------
        PromptTemplate
            The stored template; the built-in one if the text is identical.

        Raises
        ------
        PromptError
            If `name` is unknown or the template is empty or invalid.
        PromptSchemaMismatch
            If the variables differ from the built-in prompt's.
        """
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
        """Restore the built-in text of the given prompts (all when omitted).

        Raises
        ------
        PromptError
            If a name is unknown.
        """
        for n in names or list(PROMPT_NAMES):
            self.get(n)
            self._prompts[n] = self._builtin[n]

    @property
    def is_custom(self) -> bool:
        """True when at least one prompt differs from the built-in text."""
        return any(p.source == "custom" for p in self._prompts.values())

    def set_hash(self, extra: dict[str, Any] | None = None) -> str:
        """Hash the prompt texts plus optional settings.

        Parameters
        ----------
        extra : dict, optional
            Settings rendered into the prompts (see
            `fgpd_analyzer.codebook.hash_relevant`).

        Returns
        -------
        str
            SHA-256 hex digest over the sorted ``(name, sha256)`` pairs and
            `extra`.
        """
        payload = {
            "prompts": sorted((n, p.sha256) for n, p in self._prompts.items()),
            "extra": extra or {},
        }
        return _sha(json.dumps(payload, sort_keys=True, default=str))

    def version(self) -> str:
        """Return ``"v1"`` for the built-in set, else ``"custom-"`` + 8 hex characters."""
        if not self.is_custom:
            return PROMPT_SET_VERSION
        return f"custom-{self.set_hash()[:8]}"

    def export(self, directory: Path) -> list[Path]:
        """Write every current template to ``directory/<name>.md.j2``.

        Parameters
        ----------
        directory : pathlib.Path
            Created if needed; files are overwritten.

        Returns
        -------
        list of pathlib.Path
            The written files, in `PROMPT_NAMES` order.
        """
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        out = []
        for n, p in self._prompts.items():
            path = directory / f"{n}.md.j2"
            path.write_text(p.text, encoding="utf-8")
            out.append(path)
        return out
