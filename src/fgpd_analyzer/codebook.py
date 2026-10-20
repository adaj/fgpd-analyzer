"""Codebook facts, prompt rendering and codebook export (DESIGN §8, §12).

The prompts stage of the pipeline (input → units → **prompts** → requests →
...). It holds the structured facts shared by the output schema, the
post-validation and the human codebook (indicators per goal, which goals need
an antecedent), renders the prompt templates with the configuration, computes
the prompt-set hash and writes the human codebook.

The indicator lists here must match the tables in the goal prompts; a test
parses the prompt files and fails if they drift apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from .config import GOAL_LABELS, GOALS, AnalyzerConfig
from .prompts.registry import PromptRegistry

#: Allowed indicators per goal; must match the tables in the goal prompts.
GOAL_INDICATORS: dict[str, tuple[str, ...]] = {
    "G1": ("share_idea", "expand_on_request", "clarify_own", "fragment"),
    "G2": (
        "rephrase",
        "clarify_request",
        "fitting_answer",
        "check_understanding",
        "content_reference",
        "repair_loop",
        "generic_uptake",
    ),
    "G3": ("warrant", "extend_own", "self_revision", "answer_press", "bare_marker"),
    "G4": (
        "reasoned_agree",
        "reasoned_disagree",
        "add_on",
        "apply_or_counterexample",
        "compare_or_link",
        "defend_concede_revise",
        "co_construct",
        "bare_agree",
    ),
}
#: Every indicator of every goal, in goal order (the schema's indicator enum).
ALL_INDICATORS: tuple[str, ...] = tuple(i for g in GOALS for i in GOAL_INDICATORS[g])
#: Goals whose patterns must take up a peer's unit (antecedent).
ANTECEDENT_REQUIRED: frozenset[str] = frozenset({"G2", "G4"})
#: Maximum justification length stated in the codebook, in words.
JUSTIFICATION_MAX_WORDS = 40

LANGUAGE_NAMES = {
    "en": "English",
    "pt": "Portuguese",
    "pt-br": "Brazilian Portuguese",
    "pt-pt": "European Portuguese",
    "nl": "Dutch",
    "es": "Spanish",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "el": "Greek",
}


def language_name(code: str | None) -> str | None:
    """Return the English name of a language code (``"pt-br"`` → "Brazilian Portuguese").

    Unknown codes are returned unchanged; None stays None.
    """
    if code is None:
        return None
    return LANGUAGE_NAMES.get(code.lower(), code)


def render_context(config: AnalyzerConfig) -> dict[str, Any]:
    """Return the values of the template variables derived from the configuration.

    Parameters
    ----------
    config : AnalyzerConfig

    Returns
    -------
    dict
        ``pattern_gap_units``, ``pattern_gap_min`` (minutes),
        ``justification_language`` and ``chat_language`` (language names).
    """
    gap_min = config.pattern_gap.total_seconds() / 60
    return {
        "pattern_gap_units": config.pattern_gap_units,
        "pattern_gap_min": int(gap_min) if gap_min.is_integer() else round(gap_min, 2),
        "justification_language": language_name(config.justification_language),
        "chat_language": language_name(config.chat_language),
    }


def hash_relevant(config: AnalyzerConfig) -> dict[str, Any]:
    """Return the settings that change the rendered prompts or the units.

    `render_context` plus ``unit_gap_s`` (seconds). Enters the prompt-set hash.
    """
    return {**render_context(config), "unit_gap_s": config.unit_gap.total_seconds()}


def render_prompt(
    registry: PromptRegistry, name: str, config: AnalyzerConfig, goal: str | None = None
) -> str:
    """Render one prompt with the configuration.

    Parameters
    ----------
    registry : PromptRegistry
        Source of the template.
    name : str
        Prompt name.
    config : AnalyzerConfig
        Supplies the template variables (see `render_context`).
    goal : str, optional
        Required for ``task_detect``.

    Returns
    -------
    str

    Raises
    ------
    ValueError
        If ``task_detect`` is rendered without a goal.
    PromptError
        If `name` is unknown.
    """
    ctx = render_context(config)
    if name == "task_detect":
        if goal is None:
            raise ValueError("task_detect needs a goal")
        ctx = {**ctx, "goal": goal, "goal_label": GOAL_LABELS[goal]}
    p = registry.get(name)
    return p.render(**{k: v for k, v in ctx.items() if k in p.variables})


def render_codebook_block(registry: PromptRegistry, config: AnalyzerConfig) -> str:
    """Render the codebook block of every request.

    General rules, chat conventions and the four goal sections, in that order.
    It is identical for all calls of an analysis and is the second cached
    block of each request.
    """
    parts = [
        render_prompt(registry, "codebook_general", config),
        render_prompt(registry, "chat_conventions", config),
    ]
    parts += [render_prompt(registry, f"goal_{g}", config) for g in GOALS]
    return "\n\n".join(p.strip("\n") for p in parts) + "\n"


def prompt_set_hash(registry: PromptRegistry, config: AnalyzerConfig, schema_version: str) -> str:
    """Hash of the prompt texts, the settings rendered into them and the schema version.

    Parameters
    ----------
    registry : PromptRegistry
    config : AnalyzerConfig
    schema_version : str

    Returns
    -------
    str
        SHA-256 hex digest. Part of every cache key and of the coding sheet.
    """
    return registry.set_hash({**hash_relevant(config), "schema": schema_version})


# --------------------------------------------------------------------- export

_wrapper_env = SandboxedEnvironment(
    undefined=StrictUndefined, keep_trailing_newline=True, autoescape=False
)


def export_codebook(
    registry: PromptRegistry,
    config: AnalyzerConfig,
    path: Path,
    schema_version: str,
    package_version: str,
) -> Path:
    """Write the human codebook as Markdown.

    The rule sections are the rendered prompts, verbatim, wrapped by the
    packaged ``codebook_wrapper.md.j2`` template.

    Parameters
    ----------
    registry : PromptRegistry
    config : AnalyzerConfig
        Supplies the rendered values (pattern gap, languages, unit gap).
    path : pathlib.Path
        Output file; parent directories are created.
    schema_version : str
    package_version : str
        Both printed in the codebook header.

    Returns
    -------
    pathlib.Path
        `path`.
    """
    from importlib import resources

    wrapper = (resources.files("fgpd_analyzer.prompts") / "codebook_wrapper.md.j2").read_text(
        encoding="utf-8"
    )
    sections = {
        "system": render_prompt(registry, "system", config).strip("\n"),
        "codebook_general": render_prompt(registry, "codebook_general", config).strip("\n"),
        "chat_conventions": render_prompt(registry, "chat_conventions", config).strip("\n"),
        "goals": [render_prompt(registry, f"goal_{g}", config).strip("\n") for g in GOALS],
        "task": {
            g: render_prompt(registry, "task_detect", config, goal=g).strip("\n") for g in GOALS
        },
    }
    text = _wrapper_env.from_string(wrapper).render(
        sections=sections,
        prompt_set_version=registry.version(),
        prompt_set_hash=prompt_set_hash(registry, config, schema_version),
        schema_version=schema_version,
        package_version=package_version,
        goals=GOALS,
        goal_labels=GOAL_LABELS,
        indicators=GOAL_INDICATORS,
        justification_max_words=JUSTIFICATION_MAX_WORDS,
        unit_gap_s=int(config.unit_gap.total_seconds()),
        ctx=render_context(config),
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
