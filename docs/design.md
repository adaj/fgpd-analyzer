# fgpd-analyzer: Design Specification (v0.1 target)

**Status:** arbitrated design. It merges `1_construct.md` (C), `2_critique.md` (K), `3_llm_measurement.md` (M) and `4_architecture.md` (A). Where an input conflicts with this document, this document wins. The user's decisions (§0) override everything else.
**Date:** 2026-10-19. **Default model:** `claude-haiku-5-5`.

---

## 0. Fixed user decisions (non-negotiable inputs)

| # | Decision | Where it lands |
|---|---|---|
| U1 | Default model `claude-haiku-5-5`, configurable in the constructor. Base prompts can be viewed and replaced. | §6, §7 |
| U2 | Unit of analysis = one group's whole chat (no phases or sessions). Input holds one or many groups. Validation runs before any API call. | §2, §9 |
| U3 | Agents are found by a case-insensitive username substring (default `"agent"`, e.g. `"Clair"`). Agent turns are context only. Each pattern reports whether and which agent message influenced it, with a configurable lag. | §2.2, §11 |
| U4 | Pattern scores lie in [0,1], partial fulfilment counts, and rubric levels are anchored. | §3 |
| U5 | Outputs: (a) per-group FGPD frequency (main metric); (b) cumulative time series every N minutes; (c) unparameterized location metadata per pattern; plus run metadata. | §10 |
| U6 | A human-executable codebook mirrors the LLM procedure exactly, so kappa can be computed later. There is no external gold standard, and Papadopoulos' coding is not used. | §12 |
| U7 | Pseudonymization is on by default. New collections include consent for LLM processing. | §2.3, §14 |
| U8 (2026-10-19) | Productivity = **raw frequency** of behaviours, not efficiency. `frequency` is the primary metric. `rate_per_100` is secondary, and `n_student_messages` and `duration_min` are always reported next to it. Comparisons between conditions must also check whether the condition changed message volume. | §10a |
| U9 (2026-10-19) | "Influenced by the agent" = **content uptake only** (students take up the content of an agent message) inside the lag. Temporal proximity alone is reported as `temporal_only` and is never counted as influence. | §11 |
| U10 (2026-10-19) | G1 level 4 needs ideas from **≥2 students**, not every member. Patterns are compared only within the same group size. | §3.3 |
| U11 (2026-10-19) | Content-loaded coordination counts as G1, capped at level 2 unless reasoned. | §3.5 |
| U12 (2026-10-19) | The codebook is canonical in English (identical to the prompts). Justifications and an auxiliary translation are in Portuguese; the English text prevails. | §12 |
| U13 (2026-10-19) | Groups whose agent share is above `max_agent_share` (0.40) are **flagged, not silently dropped**. `exclude_flagged=True` removes them; the dev analysis uses it. | §2.5, §6 |

---

## 1. Arbitration summary (what was decided and why)

| Conflict | Inputs | Decision |
|---|---|---|
| Unit of evidence: SCU (C) vs. message-level Stage A + `continues` links (M) vs. message rows (A) | C §2, M §1.2, A §5 | **The SCU is the coding unit. It is computed deterministically in code** (§2.2), not linked by the LLM. Humans and the LLM therefore code identical units, and kappa has a fixed unit. Outputs map SCUs back to message intervals. |
| Two-stage pipeline (Stage A turn coding + Stage B) vs. a single detection prompt (A) | M §1.1, A §8 | **v0.1 runs one detection call per goal (Stage B only) over the SCU transcript.** Stage A is deferred to v0.2 as an optional pass, to be kept only if an ablation shows it raises human–LLM kappa. Reasons: the user's outputs need no function codes, the human codebook shrinks to four identical-form passes, and cost falls about 40%. |
| "Episode" (C, M) vs. "pattern" (A, user) | — | The public term is **pattern**: one instance of one goal, an exchange around one idea, question or claim (§3.1). "Episode" is retired. |
| Episode length "7–20 messages or topical segment" (C) | C §3 | Replaced by the **idea-thread + gap rule** (§3.1). The source pages give no fixed windows (K §2.1). |
| Scores 0/.25/.5/.75/1 (C), float enum (M), `score` float (A) | — | The LLM and humans output an **integer level 1–4**. `score = level/4`. Level 0 = no pattern. This avoids float enums and gives an ordinal for weighted kappa (K R6). |
| `temperature=0.0` and a forced tool call (A) | A §3, §8 | **Removed.** Haiku 5.5 rejects any sampling parameter, and a forced `tool_choice` suppresses thinking (M §0). v0.1 sends no sampling params and uses `output_config.format` (json_schema). `effort` is the only decoding knob. |
| `n_runs` default 1 (A) vs. 3/5 (M) | — | **Default 3** (§5). |
| Agent influence: temporal only (A) vs. temporal + content (M) | A §2, M §1.4 | **Hybrid, and the lag is applied post hoc in code.** The LLM cites any agent unit taken up, without being told the lag. Code applies the lag window. Changing the lag needs no API call (§11). |
| Candidate agent list in the prompt (M §1.3) | — | **Dropped.** Candidates depend on the pattern span, which is unknown before the call (M's design was circular). |
| Validation failure → re-ask (M) vs. repair prompt (A) | — | Enum ids make invalid ids impossible. Structural violations are **demoted or dropped deterministically in code** (§8.3). Only parse failures, `max_tokens` and refusals trigger retries. |
| Batch API as default (M) | M §5 | **Deferred to v0.2.** v0.1 uses async calls with prompt caching. |
| `min_student_messages` 5 (A) vs. 8 (M) | — | **8**, plus **≥2 students**. |
| M's "3–4 students per group" | M §0 | The data contradict it: analyzable dev groups have **2–3** students. Prompts and tests must cover dyads. |

---

## 2. Input, units and preprocessing (deterministic, no LLM)

### 2.1 Input

Canonical columns after `ColumnMap.auto` (case-insensitive synonyms): `group`, `username`, `timestamp`, `text`, optional `message_id`. Unnamed index columns (`Unnamed: 0`) are dropped. Accepted: DataFrame, `.csv`, `.tsv`, `.xlsx` (extra), `.parquet` (extra). Dev file: `Group, Username, Timestamp, Message` plus the index column.

### 2.2 Ordering, roles, units

1. **Stable sort** by `timestamp` within each group, ties kept in file order (dev has same-second ties). If the input was out of order, warning W005 is raised.
2. `message_id` = user column if given, else `m0001…` per group in sorted order. `row_index` keeps the original row.
3. **Role:** `AGENT` if `agent_pattern.lower() in username.lower()` (or a regex if `agent_pattern_regex=True`), else `STUDENT`. Default `"agent"` matches `agente 1`, `agente2`, `agente 05`. Teacher or moderator usernames can be added through `extra_agent_patterns` and are treated like agents (C rule 5).
4. **Student Contribution Unit (SCU).** A maximal run of consecutive messages by the same student, with no other speaker in between, where each message comes ≤ `unit_gap` (default 60 s) after the previous one. **Each agent message is its own unit.** Unit ids are `u001…` in order. A unit's timestamp is the timestamp of its first message, and `t_end` is the timestamp of its last message.
5. Derived fields: `seq`, `unit_id`, `is_agent`, `speaker` (pseudonym), `t_min` (minutes since the group's first message).

### 2.3 Pseudonymization (on by default)

- Students become `S1…Sk` by first appearance, and agents become `AG1…`. The map is local only, is never sent, and is written to disk only on explicit opt-in.
- In-text replacement: each original username and each `extra_names` entry is replaced by its pseudonym using word boundaries and case- and accent-insensitive matching, **only for names with ≥ `min_name_len_in_text` characters (default 3)**. *Reason:* dev usernames are single letters (`A`, `B`, `E`…). Replacing them would corrupt the Portuguese articles and conjunctions *a*, *e*, *o*. Shorter names are logged as residual risk (warning W013).
- `pseudonymize=False` requires an explicit argument and emits a `PrivacyWarning`.

### 2.4 Transcript rendering (sent to the LLM and shown to humans)

One line per unit, inside `<transcript>` data tags:

```
u014 | 19:38:34 | +16:12 | S2 | primeira msg ⏎ segunda msg
u015 | 19:38:50 | +16:28 | AG1 [AGENT] | texto do agente
```

The messages of a unit are joined with ` ⏎ `. Text is never translated. `chat_hash = sha256(rendered transcript)`.

### 2.5 Eligibility (before any API call)

A group is `insufficient_data` (NA scores, never 0) if it has fewer than `min_student_messages` (8) student messages or fewer than `min_students` (2) distinct students. A group is `too_long` if its estimated prompt exceeds `max_prompt_tokens` (80k). Chunking is deferred, and dev's maximum is about 7k tokens.

**Dev data check:** 8 groups, 627 messages. Seven groups are analyzable (18–201 student messages, 2–3 students, 4–22 agent messages, 31–52 min). `everyone-6aac62fe…` has 4 messages, 4 students and no agent, so it becomes `insufficient_data`. One group is 43% agent messages (19/44), which raises W009 and sets `flagged_agent_share`. With `exclude_flagged=True` (used for the dev analysis, U13) its status becomes `excluded` and no API call is made, which leaves 6 analyzable groups.

---

## 3. Construct operationalization

### 3.1 What a pattern is

A **pattern** of goal Gk is one contiguous exchange in which students pursue Gk **around one idea, question or claim and its direct development**. Rules, identical for the LLM and humans:

1. **Span** = `[start_id, end_id]` over units of any speaker. It may contain agent, off-task and filler units.
2. **New pattern of the same goal** begins when (a) students move to a different idea or question, or (b) `pattern_gap_units` (6) units of any speaker, or more than `pattern_gap` (5 min), pass without evidence for Gk.
3. **No overlap within a goal.** Spans of the same goal never overlap, and a unit is evidence for at most one pattern per goal (K R5). Interleaved threads in triads are merged into one pattern (a documented limitation).
4. **Evidence** = the student units that make up the pattern. Agent units are never evidence.
5. **Antecedent** (`antecedent_id`) = the peer's student unit that is taken up. It is required for G2 and G4 and is null for G1 and G3 unless an expansion answers a peer. It **may lie before `start_id`** (long-range uptake such as "as you said earlier", Q9). The span stays local.
6. Level 0 is never emitted. A goal with no patterns has frequency 0.

### 3.2 Goals, labels, indicators

| Code | Label (category) | Student-side outcome (C §0 translation) | `indicators` enum (validated per goal in code) |
|---|---|---|---|
| G1 | `share` | Students make their own task-relevant thinking public and expand or clarify it | `share_idea`, `expand_on_request`, `clarify_own`, `fragment` |
| G2 | `listen` | Students show they oriented to a peer's *content* | `rephrase`, `clarify_request`, `fitting_answer`, `check_understanding`, `content_reference`, `repair_loop`, `generic_uptake` |
| G3 | `deepen` | Students warrant, extend or revise reasoning | `warrant`, `extend_own`, `self_revision`, `answer_press`, `bare_marker` |
| G4 | `engage` | Students operate on a peer's *reasoning* | `reasoned_agree`, `reasoned_disagree`, `add_on`, `apply_or_counterexample`, `compare_or_link`, `defend_concede_revise`, `co_construct`, `bare_agree` |

Indicator definitions follow C §2. Talk-move sources: only the families named in the chapter, plus the JCAL Clair moves for G4, with citations. The Talk Science Primer nine-move list is **not** adopted (Q1).

### 3.3 Anchored levels (score = level/4)

The anchors are C §3 with these arbitrated edits. Rule: **assign the highest level whose conditions are all met; if torn between two levels, take the lower.**

| Level | G1 share | G2 listen | G3 deepen | G4 engage |
|---|---|---|---|---|
| 1 (.25) | One fragmentary or barely intelligible own idea | Generic uptake only, or a task-relevant reply that ignores the peer's content | Unelaborated marker ("porque sim"), vague appeal to authority | Bare agreement or disagreement with a peer's idea |
| 2 (.50) | One intelligible own idea, OR fragments from 2 students; no expansion | One content-contingent response (fitting answer, specific clarification request) | One warranted claim (evidence or mechanism) | One reasoned operation on a peer's reasoning |
| 3 (.75) | Intelligible ideas from ≥2 students, OR one student expands or clarifies on request | Rephrase or check of a peer's idea, OR ≥2 content-contingent responses running in both directions | Warranted claim extended by the same student, OR a warranted answer to a peer or agent press | The original reasoner responds to the operation (defends, concedes or modifies): a 3-unit exchange |
| 4 (1.0) | Intelligible ideas from **≥2 students** AND ≥1 expansion or clarification on request | A closed repair loop (rephrase confirmed or corrected) with uptake both ways | ≥2 linked warrants, or a reasoned self-revision | Several reasoned exchanges, both students operating on each other's reasoning, ending in a revised, joint, or explicitly reasoned divergent position |

Edits to C: G1 level 4 requires "≥2 students", not "every student" (Q3). Group-wide distribution is reported separately as `participation` (§10a), so that pattern quality does not depend on group size (2–4).

### 3.4 Structural constraints (in the codebook for humans; enforced in code for the LLM)

| Goal | Constraint | Violation action |
|---|---|---|
| G1 | level 3 needs ≥2 speakers in evidence, or ≥2 evidence units from one speaker; level 4 needs ≥2 speakers | demote to the highest level satisfied |
| G2 | `antecedent_id` not null, and its speaker ≠ the speaker of ≥1 other evidence unit | drop the pattern |
| G3 | level 3+ needs ≥2 evidence units, or one evidence unit preceded in-span by a press (any speaker) | demote to 2 |
| G4 | as G2; level 3+ also needs ≥3 evidence units and the antecedent speaker to reappear after the uptake | G2 rule: drop; level rule: demote to 2 |
| all | `start ≤` every in-span evidence `≤ end`; `start_id` and `end_id` both exist | drop |

Every demotion or drop is recorded in `qc_flags` and `run_metadata.qc`.

### 3.5 Boundary rules (C §4, adopted verbatim with defaults)

- Multi-labelling across goals is allowed, and goals are scored independently.
- There is **no ladder gating**: G3 and G4 are never capped by G1 or G2. Whether the ladder holds is an empirical question for later analysis.
- Non-evidence: off-task talk; pure procedure; backchannel ("ok", "kkk", emoji); verbatim copies; typo fixes (already merged in the SCU); dictated answers; pasted external text; interface questions to the agent.
- **Agent rules:** a response to an agent move can be G1 or G3 evidence. Agreeing with or rephrasing the *agent's* content is not G2 or G4, unless the agent relays a peer's idea, in which case `antecedent_id` = that peer's unit (Q5: yes). An answer to an agent recap counts only if it adds the student's own reasoning.
- Correctness is irrelevant, and informal spelling is not penalized.
- Content-loaded coordination ("vamos colocar a temperatura no eixo x") is a G1 idea, at most level 2 unless it is reasoned (Q4; confirmed, U11).

### 3.6 Resolution of C's open questions

| Q | Default | Rationale | User? |
|---|---|---|---|
| Q1 move inventory | Chapter families + JCAL moves only, cited | Avoids importing an unvalidated external list | no |
| Q2 unit | Whole chat; patterns by idea-thread + gap rule | U2. No fixed windows (K §2.1) | no (decided) |
| Q3 G1 distribution | Level 4 = ≥2 students; `participation` reported separately | Keeps pattern score independent of group size | decided (U10) |
| Q4 content coordination | Counts as G1, at most level 2 unless reasoned | It states a task-content proposal that peers can address | decided (U11) |
| Q5 agent-relayed ideas | Count as uptake of the original peer | C rule 4b | no |
| Q6 asymmetric G2 | One-directional uptake caps at level 3 | "One another" (M&O p. 338) | no |
| Q7 reasoned dissent | Can reach G4 level 4 | Reasoned divergence is engagement | no |
| Q8 extending a peer's chain | Both G3 and G4 | Multi-labelling is allowed | no |
| Q9 temporal reach | `antecedent_id` may be anywhere earlier; the span stays local | Keeps "as you said earlier" without huge spans | no |
| Q10 aggregation | Σ score (frequency) + count + rate per 100 student messages | U5; JCAL comparability (count-like) | decided (U8): frequency primary, rate secondary |
| Q11 validity anchor | Human double coding with this codebook (kappa/alpha). The legacy bridge is convergent evidence only (v0.3) | U6 | no (decided) |

---

## 4. LLM call design

Per eligible group, per run: **4 calls**, one per goal, all with the same request structure:

```
system:  [prompt:system]                                                      ← cache breakpoint 1
user[0]: [prompt:codebook_general] + [prompt:chat_conventions] + [goal_G1..G4] ← cache breakpoint 2
user[1]: <transcript>…</transcript> + chat facts (n units, speakers, language)  ← cache breakpoint 3
user[2]: [prompt:task_detect] rendered with goal=Gk                             (uncached tail)
output_config: {format: json_schema(patterns_v1 for this chat), effort: cfg.effort}
```

- **No `temperature`, `top_p` or `top_k`.** No prefill. The request ends on a user turn.
- All four goal sections sit in the cached prefix, so the model sees the boundaries between goals (G2 vs. G4). The tail names the target goal.
- The schema is **identical across goals within a chat** (the goal is an enum, verified in code), so the cache survives across the 4 goals × `n_runs` calls of a chat. The schema changes per chat (id enums), which invalidates the cache across chats. This trade-off is accepted.
- Effort defaults to `medium`. `max_tokens` = 16,000, because thinking counts against it.
- The justification asks for "a short justification (≤40 words) citing unit ids", never for "reasoning steps" (refusal risk, M §0).
- API facts are taken from M §0 (verified 2026-10-19). **They are re-verified at milestone M3** against current docs before the backend is frozen.

---

## 5. Repeated runs and consensus

- `n_runs` default **3**. Rationale: the cost is about $0.02 per 100-message chat (§13), three runs give a majority vote and a dispersion estimate, and 5 runs add little for production. `n_runs=5` is recommended for validation studies. `n_runs=1` is allowed and flagged `no_dispersion`.
- Each run is an independent request set (`run_idx` is part of the response-cache key).

**Consensus algorithm (per group × goal), deterministic:**

1. Let `R` = the number of successful runs for this goal. Represent each pattern by its unit-index interval `[s, e]`.
2. Clusters are seeded with run 0's patterns. For each next run r: build an IoU matrix between each cluster's current representative interval (the median start and median end of its members) and run r's patterns. Solve a Hungarian assignment that maximizes IoU, accept pairs with IoU ≥ `match_iou` (0.3), and turn unmatched patterns into new clusters. Within a run, patterns never overlap, so each cluster has ≤1 member per run.
3. `support = |cluster| / R`. A cluster is **consensus** if `support ≥ consensus_min_support` (0.5, i.e. 2 of 3). Otherwise it goes to `unstable_patterns`, which are kept and exported but not counted.
4. Consensus fields: `level` = **lower median** of member levels (consistent with "if torn, take the lower"); `level_min`, `level_max`; span = lower-median start and end; `evidence` = units present in ≥ half of member evidence sets (falling back to the union if that is empty); `antecedent` and `indicators` by member majority (ties → lowest unit id or alphabetical); `agent_uptake_id` = the id cited by > half of the members, else null; `justification` from the member whose level equals the consensus level and whose span IoU with the consensus span is highest.
5. If consensus spans of the same goal overlap after medians, they are merged (span union, max level, evidence union) and flagged `consensus_merge`.

**Group-level dispersion:** `freq_run_mean` and `freq_run_sd` are computed over the per-run Σ scores. The cross-run unit-membership Fleiss' kappa per goal goes into `run_metadata.stability`. It is the ceiling for interpreting human–LLM kappa.

---

## 6. Configuration (`AnalyzerConfig`, pydantic v2, frozen)

| Field | Default | In prompt hash? | Notes |
|---|---|---|---|
| `model` | `"claude-haiku-5-5"` | key | U1 |
| `api_key` | `None` → `$ANTHROPIC_API_KEY` | – | `SecretStr` |
| `backend` | `"anthropic"` | key | `anthropic`, `mock`, `replay` |
| `effort` | `"medium"` | key | `low`, `medium`, `high` |
| `max_tokens` | 16000 | key | |
| `timeout_s` / `max_retries` | 120 / 5 | – | |
| `max_concurrency` | 4 | – | plus optional `requests_per_minute` |
| `agent_pattern` | `"agent"` | – | case-insensitive substring (U3) |
| `agent_pattern_regex` | `False` | – | |
| `extra_agent_patterns` | `()` | – | teachers or moderators |
| `influence_lag` | `"2min"` | **no** (post hoc) | accepts str, int seconds or timedelta |
| `influence_rule` | `"uptake"` | no | `uptake` or `temporal` (§11) |
| `unit_gap` | `"60s"` | yes (changes units) | SCU merge threshold |
| `pattern_gap_units` / `pattern_gap` | 6 / `"5min"` | yes | rendered into the codebook |
| `goals` | `("G1","G2","G3","G4")` | – | subset allowed |
| `n_runs` | 3 | key | ≥1 |
| `consensus_min_support` / `match_iou` | 0.5 / 0.3 | no | |
| `min_student_messages` / `min_students` | 8 / 2 | – | |
| `max_agent_share` | 0.40 | – | above it: W009 + `flagged_agent_share` (U13) |
| `exclude_flagged` | `False` | – | `True` → flagged groups get status `excluded`, no API call |
| `max_prompt_tokens` | 80000 | – | stays below the 100k price tier |
| `pseudonymize` | `True` | – | U7 |
| `extra_names` / `min_name_len_in_text` | `()` / 3 | – | |
| `chat_language` | `None` | yes | e.g. `"pt-BR"`; `None` → "may be any language" |
| `justification_language` | `"en"` | yes | `"pt"` for Brazilian coders |
| `sort` | `True` | – | stable sort; out-of-order → W005 |
| `strict` | `False` | – | warnings become errors |
| `cache` | `"use"` | – | `use`, `refresh`, `off` |
| `cache_dir` | `platformdirs.user_cache_path("fgpd-analyzer")` | – | |
| `prompts_dir` | `None` | via hash | load overrides from a directory |
| `price_table` | packaged `pricing.toml` | – | versioned |

Precedence: constructor kwargs > `config=` > `fgpd.toml` (CLI) > `FGPD_*` env. `fingerprint()` hashes every field that affects outputs, excluding the key.

---

## 7. Public API

```python
class FGPDAnalyzer:
    def __init__(self, config: AnalyzerConfig | None = None, *, model: str = "claude-haiku-5-5",
                 backend: LLMBackend | None = None, prompts: PromptRegistry | dict | Path | None = None,
                 **overrides): ...

    # prompts (U1)
    def list_prompts(self) -> pd.DataFrame          # name, version, sha256, source(builtin|custom), required_vars
    def show_prompt(self, name: str, *, rendered: bool = False, goal: str | None = None) -> str
    def get_prompt(self, name: str) -> PromptTemplate
    def set_prompt(self, name: str, template: str | Path, *, description: str = "") -> PromptTemplate
    def reset_prompts(self, names: list[str] | None = None) -> None
    def export_prompts(self, directory: Path) -> list[Path]
    def export_codebook(self, path: Path, *, format: Literal["md","html"] = "md",
                        language: Literal["en", "pt"] = "en") -> Path   # "pt" = auxiliary translation (U12)

    # input
    def load(self, source, *, columns: ColumnMap | dict | None = None, **read_kw) -> pd.DataFrame
    def validate_input(self, source, *, columns=None) -> ValidationReport
    def preview(self, source, *, group: str, columns=None) -> str     # exact pseudonymized transcript
    def export_coding_sheet(self, source, path: Path, *, columns=None) -> Path  # human coding workbook

    # analysis
    def estimate_cost(self, source, *, columns=None) -> CostEstimate          # no network
    def analyze(self, source, *, columns=None, groups: list[str] | None = None,
                dry_run: bool = False, raise_on_partial: bool = False) -> FGPDResults
    async def analyze_async(self, source, **kw) -> FGPDResults

class FGPDResults:
    frequencies: pd.DataFrame          # (a)
    patterns: pd.DataFrame             # (c), consensus only
    unstable_patterns: pd.DataFrame
    run_patterns: pd.DataFrame         # every run's raw validated patterns
    messages: pd.DataFrame             # id ↔ unit ↔ row map, no text
    run_metadata: RunMetadata
    validation: ValidationReport
    def timeseries(self, every: str = "5min", *, cumulative: bool = True,
                   origin: Literal["group_start","absolute"] = "group_start") -> pd.DataFrame  # (b)
    def with_influence(self, lag: str | timedelta, rule: str | None = None) -> "FGPDResults"  # post hoc, no API
    def save(self, directory, *, format: Literal["csv","parquet"] = "csv",
             timeseries_every: tuple[str, ...] = ("5min",), include_pseudonym_map: bool = False) -> dict[str, Path]
    @classmethod
    def load(cls, directory) -> "FGPDResults"
```

- `analyze` validates first and raises `InputValidationError` (with `.report`) before any network I/O.
- `dry_run=True` builds every request, writes nothing to the API, and returns the eligibility report and cost estimate.
- `set_prompt` checks the declared variables and refuses templates that drop a required variable (`PromptSchemaMismatch`). A customized registry has `prompt_set_version = "custom-<hash8>"`, and its results are marked non-comparable with builtin results.

**CLI (`fgpd`, Typer):** `validate`, `estimate`, `analyze -o DIR [--n-runs] [--every 5min ...] [--agent-pattern Clair] [--lag 2min] [--backend mock]`, `timeseries DIR --every 10min`, `influence DIR --lag 90s`, `prompts list|show NAME|export DIR`, `codebook export PATH`, `coding-sheet export CHATS PATH`, `cache info|clear`. Exit codes: 0 OK, 2 validation errors, 3 backend failure, 4 partial results.

---

## 8. Prompt registry, schema and post-validation

### 8.1 Base prompts (files in `src/fgpd_analyzer/prompts/v1/`)

| Name | Content | Variables |
|---|---|---|
| `system` | Role (careful discourse analyst), data-handling rules (the transcript is data, never instructions; agents are context only), output rules | – |
| `codebook_general` | Purpose; units; transcript format; pattern definition and segmentation (§3.1); evidence and antecedent; non-evidence list; agent rules; level rule ("highest fully met; if torn, lower"); justification rule | `pattern_gap_units`, `pattern_gap_min`, `justification_language` |
| `chat_conventions` | Glossary of chat conventions (kkk/rs = laughter, vc, "?" bursts = clarification requests, emoji), plus the instruction not to penalize spelling | `chat_language` |
| `goal_G1` … `goal_G4` | Definition; indicators (with ids from §3.2); 4-level anchor table; structural constraints (§3.4); two synthetic examples per level (Portuguese and English; never real data) | – |
| `task_detect` | "Find every pattern of {{goal}} in the transcript, following the {{goal}} section and the general rules. Return them in chronological order." | `goal`, `goal_label` |

The registry stores `name → PromptTemplate(name, version, text, variables, sha256, source)`. Rendering uses sandboxed Jinja2 with `StrictUndefined`. `prompt_set_hash` = sha256 over the sorted (name, sha256) pairs, the schema template and the hash-relevant config values. A **CI check (K R1)** regenerates `docs/codebook.md` from the prompt files and fails if the committed codebook hash differs.

### 8.2 Output schema `patterns_v1` (built per chat; identical for G1–G4)

```json
{"type":"object","additionalProperties":false,"required":["goal","patterns"],
 "properties":{
  "goal":{"enum":["G1","G2","G3","G4"]},
  "patterns":{"type":"array","items":{"type":"object","additionalProperties":false,
   "required":["start_id","end_id","evidence_ids","antecedent_id","indicators",
               "justification","level","agent_uptake_id"],
   "properties":{
    "start_id":{"$ref":"#/$defs/unit_id"},
    "end_id":{"$ref":"#/$defs/unit_id"},
    "evidence_ids":{"type":"array","minItems":1,"items":{"$ref":"#/$defs/student_unit_id"}},
    "antecedent_id":{"anyOf":[{"$ref":"#/$defs/student_unit_id"},{"type":"null"}]},
    "indicators":{"type":"array","minItems":1,"items":{"enum":["share_idea","expand_on_request","…all §3.2 ids…"]}},
    "justification":{"type":"string"},
    "level":{"enum":[1,2,3,4]},
    "agent_uptake_id":{"anyOf":[{"$ref":"#/$defs/agent_unit_id"},{"type":"null"}]}}}}},
 "$defs":{"unit_id":{"enum":["u001","u002","…"]},
          "student_unit_id":{"enum":["u001","u003","…"]},
          "agent_unit_id":{"enum":["u002","…"]}}}
```

- If a chat has no agent units, `agent_uptake_id` is `{"type":"null"}`.
- Above `max_enum_ids` (1,500) units, or if the API rejects the schema for complexity, the id fields fall back to `type: string` with post-validation.
- Unsupported keywords (`minimum`, `maxLength`, `maxItems`) are not used. Enums are compared case-insensitively.

### 8.3 Post-validation (pydantic, per run, in this order)

1. Parse. On JSON failure: one retry with a fresh request.
2. `goal` equals the requested goal (else retry once). Unknown or out-of-goal indicators are removed (and flagged). A pattern left with no indicators is dropped.
3. Ids exist. Evidence ids are student units. In-span evidence lies in `[start, end]`, and only `antecedent_id` may precede `start`.
4. If `start > end`, they are swapped and flagged.
5. Structural constraints (§3.4): demote or drop.
6. Same-goal overlaps are merged (span union, max level, evidence union) and flagged.
7. The justification is truncated to 60 words and flagged `justification_truncated`.
8. Sorted chronologically, with `run_pattern_id = {group}:{goal}:{run}:{k}`.

**Retries:** 429/529/5xx/timeouts use exponential backoff with full jitter and honor `retry-after` (≤5 attempts). `max_tokens` → one retry at 2× (capped at 32k), then `failed`. `stop_reason="refusal"` → one retry, then `refused` for that goal-run (never imputed as 0).

---

## 9. Input validation rules

These are pure checks that run before preprocessing and the network. An `Issue` has `code`, `severity`, `group`, `row_indices` (capped at 20), `message` (never chat text) and `hint`.

| Code | Rule | Severity |
|---|---|---|
| E001 | Required column missing after mapping (suggests the closest names) | error |
| E002 | Two source columns map to one canonical column | error |
| E003 | Missing group, username or timestamp | error |
| E004 | Unparseable timestamp | error |
| E006 | Mixed tz-aware and naive timestamps | error |
| E007 | No student messages in the entire input | error |
| E008 | Duplicate `message_id` within a group | error |
| E009 | Invalid config/data combination (e.g. a goal subset that is not in G1–G4) | error |
| W001 | Empty or whitespace text (row dropped) | warning |
| W002 | Exact duplicate rows (dropped) | warning |
| W003 | Group below `min_student_messages` or `min_students` → `insufficient_data` (error if `strict`) | warning |
| W004 | Group has no agent messages | warning |
| W005 | Timestamps were out of order and have been stable-sorted (error if `sort=False`) | warning |
| W006 | Identical timestamps within a group (file order kept) | warning |
| W007 | Gap longer than 60 min within a group | warning |
| W008 | Agent pattern matches nothing in the whole input, or more than 50% of a group's usernames | warning |
| W009 | Agent share above 40% of a group's messages | warning |
| W010 | Group estimated above `max_prompt_tokens` → `too_long` | warning |
| W013 | Usernames shorter than `min_name_len_in_text` were not replaced in text | warning |
| I001 | Extra columns ignored; dtypes coerced | info |

`ValidationReport` offers `ok`, `errors`, `warnings`, a per-group `info` table (n_messages, n_student, n_agent, n_students, duration, eligible, est_tokens), `to_text()`, `to_dataframe()` and `raise_if_errors()`. Dev file expectation: no errors; W003 on `everyone-…`, W004 on the same group, W006 and W009 on the relevant groups, W013 for single-letter names.

---

## 10. Outputs

Status values per group × goal: `ok`, `partial` (some runs failed but ≥1 succeeded), `insufficient_data`, `too_long`, `excluded` (U13), `refused`, `failed`. All non-`ok`/`partial` statuses carry NA metrics.

### (a) `frequencies`: one row per group × goal (long; `wide=True` pivots it)

| Column | Dtype | Definition |
|---|---|---|
| `group` | string | |
| `goal` | category (G1–G4) | |
| `goal_label` | category | share, listen, deepen, engage |
| `status` | category | |
| `frequency` | Float64 | **Σ consensus `score`, the main metric (U5)** |
| `count` | Int32 | number of consensus patterns |
| `count_strong` | Int32 | patterns with level ≥3 |
| `mean_score` | Float64 | frequency / count (NA if 0) |
| `rate_per_100` | Float64 | frequency / `n_student_messages` × 100. **Secondary** (U8): it separates "more productive talk" from "more talk" |
| `n_student_messages`, `n_student_units`, `n_agent_messages`, `n_students` | Int32 | |
| `duration_min` | Float64 | last − first timestamp |
| `participation` | Float64 | share of the group's students appearing in ≥1 evidence unit of this goal |
| `influenced_frequency` | Float64 | Σ score of patterns counted as influenced under `influence_rule` |
| `influenced_count`, `uptake_count`, `temporal_only_count` | Int32 | |
| `freq_run_mean`, `freq_run_sd` | Float64 | over per-run Σ scores (SD NA if R = 1) |
| `n_runs_ok` | Int8 | |
| `n_unstable` | Int32 | clusters below the support threshold |

### (b) `timeseries(every=N)`: one row per group × goal × bin, derived from `patterns`, no API

- Origin = the group's first message. Bin k covers `(k·N, (k+1)·N]` minutes, and bin 0 also includes t = 0. The number of bins is `ceil(duration/N)`, minimum 1.
- A pattern is counted in the bin containing its **`end_ts`** (completion).
- Columns: `group`, `goal`, `bin_index` Int32, `bin_start_min`, `bin_end_min` Float64, `bin_end` datetime64 (when `origin="absolute"`), `frequency_cum` Float64, `count_cum` Int32, `influenced_frequency_cum` Float64, `student_messages_cum` Int32, `rate_per_100_cum` Float64.
- `cumulative=False` returns per-bin increments.
- **Invariants:** the cumulative series is non-decreasing, and the last bin equals (a).

### (c) `patterns`: one row per consensus pattern (unparameterized location)

| Column | Dtype |
|---|---|
| `pattern_id` (`{group}:{goal}:{k}`) | string |
| `group`, `goal`, `goal_label` | string, category, category |
| `level` Int8 (1–4), `score` Float64 (= level/4), `level_min`, `level_max` | |
| `indicators` | list[string] |
| `start_message_id`, `end_message_id` | string (first message of the start unit, last message of the end unit) |
| `start_unit_id`, `end_unit_id` | string |
| `start_ts`, `end_ts` | datetime64 |
| `start_min`, `end_min` | Float64 |
| `evidence_message_ids`, `evidence_unit_ids` | list[string] |
| `antecedent_message_id` | string/NA |
| `speakers` (pseudonyms), `n_speakers` | list[string], Int8 |
| `justification` | string (pseudonymized; the only field that can echo chat content) |
| `agent_cited_message_id` | string/NA (raw consensus citation, lag-agnostic) |
| `agent_influence` | category: `uptake`, `temporal_only`, `none` |
| `agent_message_id`, `agent_lag_s`, `agent_position` (`before`/`within`) | string/NA, Float64, category |
| `influenced` | boolean (under `influence_rule`) |
| `support`, `n_runs_detected` | Float64, Int8 |
| `qc_flags` | list[string] |
| `prompt_set_version` | string |

List columns are Arrow lists in parquet and JSON strings in csv.

### (d) `messages`

Columns: `group`, `row_index`, `message_id`, `unit_id`, `speaker`, `is_agent`, `timestamp`, `t_min`. There is no text column. Humans use it to map ids back to raw data.

### (e) `RunMetadata` (JSON)

`package_version`, `schema_version`, `run_id`, `started_at`/`finished_at`, `config` (redacted) + `config_fingerprint`, `model`, `effort`, `backend`, `prompts[{name, sha256, source}]`, `prompt_set_version`, `prompt_set_hash`, `schema_hash`, per-group `chat_hash`, `input_sha256`, per-call `usage` (input, cache_creation, cache_read, output), `request_id`s, `cost_usd`, `price_table_version`, `qc` (demotions, drops, merges, truncations, refusals per group/goal/run), `stability` (cross-run Fleiss' kappa per goal), `groups{status}`, `warnings` (codes and ids only).

---

## 11. Agent-influence algorithm (post hoc; mirrored in the codebook)

The **LLM or human** fills `agent_uptake_id`: "the agent unit whose question, move or topic the students take up in this pattern, or null". No lag is mentioned. The citation must be an agent unit that precedes the last evidence unit, otherwise code nulls it and flags it.

For each consensus pattern p, with `lag = influence_lag`:

```
E      = evidence units of p (student), e_last = max(E by order)
W(p)   = { a ∈ agent units : order(a) < order(e_last)  and  t(a) ≥ t(p.start) − lag }
           # covers agent prompts shortly before the pattern and agent moves inside it
u      = consensus agent_uptake_id (may be null)
if u ≠ null and u ∈ W(p):   influence = "uptake";        agent = u
elif W(p) ≠ ∅:              influence = "temporal_only"; agent = latest a ∈ W with t(a) ≤ t(p.start),
                                                          else earliest a ∈ W
else:                       influence = "none";          agent = NA
agent_lag_s    = t(first evidence unit after agent) − t(agent)
agent_position = "before" if order(agent) < order(p.start unit) else "within"
influenced     = (influence == "uptake")                        if influence_rule == "uptake"
                 (influence in {"uptake","temporal_only"})      if influence_rule == "temporal"
```

- `agent_cited_message_id` keeps `u` even when it falls outside W, so a wider lag can be applied later.
- `FGPDResults.with_influence(lag=…)` and `fgpd influence DIR --lag` recompute (a), (b) and (c) without API calls.
- This implements C rule 4c (`agent_prompted`) at pattern level.

---

## 12. Human codebook (mirrors the prompts exactly)

**Principle:** the human codebook is **generated from the same prompt files** by `export_codebook()`. Its sections are the prompt texts verbatim. Only a human wrapper (how to fill the sheet) is added, and that wrapper never changes any rule. The codebook header carries `prompt_set_hash`. Kappa is valid only between results and codings that share that hash.

| Codebook section | Source prompt |
|---|---|
| 0. Purpose, provenance, hash, citation of M&O pp. 338–344 and JCAL | `system` (data rules) + generated header |
| 1. Materials: the coding workbook from `export_coding_sheet` (sheet `transcript`: unit rows as rendered in §2.4; sheet `messages`: unit ↔ message map; one sheet per goal: the pattern form) | wrapper |
| 2. General rules: units, pattern definition, segmentation, evidence/antecedent, non-evidence, agent rules, level rule, justification | `codebook_general` |
| 3. Chat conventions | `chat_conventions` |
| 4–7. G1–G4: definition, indicators, anchors, structural constraints, examples | `goal_G1` … `goal_G4` |
| 8. Procedure: **four independent passes, one per goal, in the order G1→G4**; read the whole chat first; for each pass apply the task instruction | `task_detect` (with "you" for "the model") |
| 9. Pattern form = the schema fields as columns: `start_id`, `end_id`, `evidence_ids`, `antecedent_id`, `indicators`, `justification` (≤40 words), `level`, `agent_uptake_id` | `patterns_v1` |
| 10. Self-check: run `fgpd coding-sheet check` (the same structural constraints §3.4 and §8.3, reported rather than auto-applied) | wrapper |
| 11. Agreement plan: unit × goal membership (Cohen's kappa), weighted kappa on matched-pattern levels (same IoU matching as §5), ICC(2,1) on `frequency` and `rate_per_100`, kappa on `agent_influence` | wrapper (v0.2 computes it) |

Human codes imported via `import_human_codes()` (v0.2) become `run_patterns` with `backend="human:<coder>"`, so the downstream code (consensus between coders, aggregation, influence) is identical. Lag and consensus are applied by code to both.

---

## 13. Cost (Haiku 5.5, from M §0 prices; to be replaced by measured usage)

Assumptions for a 100-message chat: cached prefix ≈ 5k codebook + 3k transcript, tail ≈ 0.2k, output ≈ 2.5k per call (mostly thinking).

- One run (4 calls) ≈ **$0.006**. Three runs ≈ **$0.017–0.022** per 100-message chat.
- Dev set ≈ **$0.10–0.15**. 1,000 chats × 3 runs ≈ $17–22 (about half with Batch in v0.2).
- Output (mostly thinking) dominates, so `effort` is the main lever. A low vs. medium comparison is part of v0.2.

---

## 14. Privacy and security

- `preview()` and `dry_run` show exactly what would be sent.
- Logs contain ids, counts, hashes and timings only.
- The local response cache holds pseudonymized content only (`fgpd cache clear` wipes it).
- Prompt injection is mitigated by data tags and structured output.
- The docs state the consent requirement (U7).

---

## 15. Repository layout

```
fgpd-analyzer/
├── pyproject.toml  README.md  CHANGELOG.md  LICENSE  CITATION.cff
├── src/fgpd_analyzer/
│   ├── __init__.py            # FGPDAnalyzer, AnalyzerConfig, ColumnMap, FGPDResults, __version__
│   ├── analyzer.py            # orchestration
│   ├── config.py              # AnalyzerConfig, ColumnMap, duration parsing
│   ├── io.py                  # load + column mapping
│   ├── validation.py          # ValidationReport, Issue, rules §9
│   ├── preprocess.py          # sort, ids, roles, SCUs, eligibility
│   ├── pseudonymize.py        # Pseudonymizer (subclassable)
│   ├── render.py              # transcript rendering, chat_hash
│   ├── prompts/
│   │   ├── registry.py        # PromptTemplate, PromptRegistry
│   │   ├── v1/{system,codebook_general,chat_conventions,goal_G1..G4,task_detect}.md.j2
│   │   └── codebook_wrapper.md.j2
│   ├── schemas.py             # patterns_v1 builder, pydantic models
│   ├── backends/{base.py, anthropic.py, mock.py, replay.py, pricing.py, pricing.toml}
│   ├── cache.py               # SQLite response cache
│   ├── runner.py              # async fan-out, limiter, retries
│   ├── postprocess.py         # §8.3 validation, structural constraints
│   ├── consensus.py           # §5
│   ├── influence.py           # §11
│   ├── aggregation.py         # (a), (b)
│   ├── results.py             # FGPDResults, save/load
│   ├── coding.py              # coding sheet export/check (v0.1), import (v0.2)
│   ├── agreement.py           # v0.2
│   ├── exceptions.py  logging.py  cli.py
├── docs/ (mkdocs; codebook.md generated + hash-checked)
├── tests/{unit,property,golden,integration,live}/  fixtures/synthetic_*.tsv
└── examples/{quickstart.ipynb (mock backend), custom_prompt.py}
```

Exceptions follow A §7 (`FGPDError` → `ConfigError`, `InputValidationError(.report)`, `PromptError`, `BackendError`, `ResponseParseError`, `CacheError`, `PartialResultsError`). One failing group never aborts the batch. Dependencies, packaging, SemVer and CI follow A §12, with two changes: `anthropic` is pinned to a version that supports `output_config`, and `scipy` (Hungarian) is a core dependency. A change to a built-in prompt is a **minor** bump with a CHANGELOG entry and a new `prompt_set_version`. A change to the output schema is a **major** bump.

---

## 16. Testing plan

**Unit tests:**
- ColumnMap auto-mapping on the dev headers.
- One test per validation code.
- Stable sort with ties.
- SCU boundaries: 59 s/61 s, an interleaved agent, same-second ties.
- Pseudonymization: bijective; single-letter names are not replaced; accent-insensitive matching ("José" vs. "jose").
- Schema builder: enums; no-agent chat; fallback above 1,500 ids.
- Every §8.3 rule and every §3.4 demotion or drop.
- Consensus: 3 runs with identical, shifted and missing patterns; ties; the lower-median level.
- Influence boundaries: exactly at the lag; 1 s beyond; an agent inside the span; an agent after the last evidence; a citation outside W.
- Bin edges, empty bins, and the frequency formulas.
- Prompt hashing; `set_prompt` variable checks; cache keys.

**Property tests (Hypothesis):**
- The cumulative series is non-decreasing and its last bin equals `frequencies`.
- Scores lie in {.25, .5, .75, 1}, and start ≤ end.
- There is no same-goal overlap after postprocessing.
- Results are invariant to row shuffling with unique timestamps.
- Duplicating a group under a new name yields identical metrics (mock backend; K R7).
- The validator never raises on arbitrary frames.

**Golden tests:** synthetic PT and EN multi-group chats (including the construct's anchor examples, translated) with recorded `ReplayBackend` responses. Expected `frequencies.csv`, `patterns.json` and `timeseries_5min.csv` are compared with `pandas.testing`. **No real student data is committed.**

**Integration tests:** `respx` fake transport injecting 429, 529, malformed JSON, `max_tokens` and `refusal`. CLI exit codes. A `dry_run` makes zero network calls.

**Privacy test:** a sentinel name in the input never appears in logs, the cache keyspace, metadata or exports. The justification field is excluded because it is pseudonymized output.

**Live tests** (`@pytest.mark.live`, budget-capped, skipped without a key):
1. Smoke test on 2 synthetic chats.
2. Construct anchor fixtures. Target: ≥80% at the exact level, all within ±1 level.
3. Negative controls (procedural or off-task chats). Target: no pattern above level 1 (K "leniency").
4. A–A–A–B yields exactly one G1 pattern (K R5).
5. Filler insertion ("ok", typo fixes) changes unit × goal membership in ≤5% of units (K R3).

Quality gates: ruff, `mypy --strict`, coverage ≥ 90% (offline).

---

## 17. Scope

### v0.1 (MVP)
- Input I/O, validation, preprocessing, SCUs, pseudonymization.
- Prompt registry with view, replace and export.
- Generated codebook with a CI hash check; coding-sheet export and check.
- Anthropic (async), mock and replay backends; structured outputs; prompt caching; response cache; retries.
- Per-goal detection; postprocessing; `n_runs` consensus.
- Post-hoc influence.
- Outputs (a)–(e); save and load; cost estimate; `dry_run`; CLI (validate, estimate, analyze, timeseries, influence, prompts, codebook, coding-sheet, cache).

### v0.2
- Message Batches mode (`mode="batch"`, 1h cache TTL).
- Human-code import + `agreement` module (Cohen's/weighted kappa, Krippendorff's alpha, ICC, bootstrap CIs by group).
- Optional Stage A turn coding, kept only if an ablation shows it raises human–LLM kappa.
- Gap audit.
- Chunking for chats above 80k tokens.
- Language auto-detection (W011).
- Effort and prompt-variant sensitivity sweeps (K R11 and prompt sensitivity).
- Pattern-gap sensitivity sweep.

### v0.3+
- Open-weights backend validated under R2.
- Legacy bridge to JCAL counts (R14).
- Translation-robustness subset (R9).
- Positional-recall report.
- Cross-family evaluator check when Clair runs on Claude.
- Interleaved-thread patterns, i.e. overlapping spans with disjoint evidence.

### Rejected
A composite index across goals, ladder gating, verbalized confidence, temperature or seed control, and phases or sessions.

---

## 18. Implementation plan (ordered milestones, with acceptance criteria)

**M0. Scaffold (0.5 d).** pyproject, CI (ruff, mypy, pytest on 3.10–3.13), package skeleton, exceptions, logging with `RedactingFilter`.
*Accept:* CI is green on an empty test suite, and `pip install -e .[dev]` works.

**M1. Input pipeline (1.5 d).** `io`, `ColumnMap`, `validation`, `preprocess`, `pseudonymize`, `render`, `messages` table, `preview`.
*Accept:* `validate_input("dev_chats.tsv")` returns no errors, 7 eligible groups, and W003 + W004 on `everyone-…`. SCU and pseudonym unit tests pass. The sentinel privacy test passes. Single-letter names are not replaced in text.

**M2. Prompts and codebook (2 d).** Write `prompts/v1/*` from §3, with synthetic examples in PT and EN. Build the registry API (`list/show/get/set/reset/export`), `export_codebook`, `export_coding_sheet`, and the CI hash check.
*Accept:* the codebook renders and its sections are byte-identical to the rendered prompts. `set_prompt` without a required variable raises an error. `prompt_set_hash` is stable across runs and machines.

**M3. Backend and postprocess (2 d).** Re-verify the M §0 API facts. Build the `patterns_v1` schema builder, `AnthropicBackend` (`output_config.format`, effort, cache breakpoints, no sampling params), mock and replay backends, the SQLite cache, the runner (concurrency, retries, refusal handling), and `postprocess`.
*Accept:* respx tests cover 429, 529, malformed JSON, `max_tokens` and refusal. No request contains `temperature`. Replaying from the cache is bit-identical. Every §8.3 and §3.4 rule has a test.

**M4. Consensus, influence, aggregation, results (2 d).** Build §5, §11, §10, save/load, `timeseries`, `with_influence`, `estimate_cost`, and the CLI.
*Accept:* all property tests pass. Golden files match. `with_influence(lag="60s")` makes zero network calls. CLI exit codes are correct.

**M5. Live dev run (1 d).** `analyze("dev_chats.tsv", n_runs=3)` with the default model.
*Accept:*
- With `exclude_flagged=True`, all 6 remaining eligible groups are `ok`, or `partial` with documented causes, and there are 0 invalid ids.
- The cost is ≤ $0.50, and the manifest is complete.
- A re-run from the cache is identical.
- Cross-run stability (Fleiss' kappa per goal) is reported. Target ≥ .80 for membership. If it is lower, it is reported as a finding rather than a blocker.
- Live tests 1–4 (§16) pass.
- The author spot-checks 20 patterns and 20 non-pattern stretches, and the error log feeds M6.

**M6. Human pilot and freeze (2 d plus coder time).** Two coders use the generated codebook and coding sheet on 2 dev groups (one dyad and one triad) and time each pass. Unclear rules are revised in the prompts, which regenerates the codebook. The CHANGELOG is updated and `v0.1.0` is tagged.
*Accept:* coders finish without asking for out-of-codebook rules. `coding-sheet check` passes on their sheets. Every prompt revision is recorded with its new `prompt_set_hash`. The formal kappa study runs in v0.2 on a fresh, stratified sample that is not the dev set.
