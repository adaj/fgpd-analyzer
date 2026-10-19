"""Structured codebook facts shared by the schema, the post-validation and the
human codebook, plus prompt rendering and codebook export (DESIGN §8, §12).

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
ALL_INDICATORS: tuple[str, ...] = tuple(i for g in GOALS for i in GOAL_INDICATORS[g])
ANTECEDENT_REQUIRED: frozenset[str] = frozenset({"G2", "G4"})
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
    if code is None:
        return None
    return LANGUAGE_NAMES.get(code.lower(), code)


def render_context(config: AnalyzerConfig) -> dict[str, Any]:
    """Values for the template variables, derived from the configuration."""
    gap_min = config.pattern_gap.total_seconds() / 60
    return {
        "pattern_gap_units": config.pattern_gap_units,
        "pattern_gap_min": int(gap_min) if gap_min.is_integer() else round(gap_min, 2),
        "justification_language": language_name(config.justification_language),
        "chat_language": language_name(config.chat_language),
    }


def hash_relevant(config: AnalyzerConfig) -> dict[str, Any]:
    """Settings that change the rendered prompts or the schema."""
    return {**render_context(config), "unit_gap_s": config.unit_gap.total_seconds()}


def render_prompt(
    registry: PromptRegistry, name: str, config: AnalyzerConfig, goal: str | None = None
) -> str:
    ctx = render_context(config)
    if name == "task_detect":
        if goal is None:
            raise ValueError("task_detect needs a goal")
        ctx = {**ctx, "goal": goal, "goal_label": GOAL_LABELS[goal]}
    p = registry.get(name)
    return p.render(**{k: v for k, v in ctx.items() if k in p.variables})


def render_codebook_block(registry: PromptRegistry, config: AnalyzerConfig) -> str:
    """The cached user block: general rules + chat conventions + all goal sections."""
    parts = [
        render_prompt(registry, "codebook_general", config),
        render_prompt(registry, "chat_conventions", config),
    ]
    parts += [render_prompt(registry, f"goal_{g}", config) for g in GOALS]
    return "\n\n".join(p.strip("\n") for p in parts) + "\n"


def prompt_set_hash(registry: PromptRegistry, config: AnalyzerConfig, schema_version: str) -> str:
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
    """Write the human codebook. Its rule sections are the rendered prompts, verbatim."""
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
