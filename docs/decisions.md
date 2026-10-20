# Decision log

Every implementation choice that is not already fixed in `docs/design.md`, in order. Each entry
gives the decision, the reason and the rejected alternative. Decisions marked **(user)** were
taken by Adelson; the rest are proposals he can veto.

## 2026-10-19: design round 2 (user)

| # | Decision | Reason |
|---|---|---|
| U8 | Productivity = raw frequency (Σ pattern scores). `rate_per_100` is secondary; `n_student_messages` and `duration_min` always appear next to it. | FGPD describes behaviours that should happen, not a ratio. Rate per time would penalize slower groups. The rate stays available to separate "more productive talk" from "more talk". |
| U9 | Agent influence = content uptake inside the lag. Temporal proximity alone is `temporal_only` and is not counted. | The agent writes up to 43% of the messages in a group, so temporal co-occurrence is weak evidence. |
| U10 | G1 level 4 needs ideas from ≥2 students. | Analyses compare groups of the same size. |
| U11 | Content-loaded coordination is G1, at most level 2. | — |
| U12 | Codebook canonical in English; PT justifications and auxiliary translation. | The codebook text must be byte-identical to the prompts. |
| U13 | Groups above `max_agent_share` (0.40) are flagged; `exclude_flagged=True` removes them. The dev analysis uses it. | Excluding on an observed value automatically would be a researcher degree of freedom; the flag keeps it explicit. |

## M0–M1: scaffold and input pipeline

| # | Decision | Reason | Rejected |
|---|---|---|---|
| D1 | Build backend: hatchling; `src/` layout; Python ≥ 3.10. | Standard, no setup.py. | setuptools |
| D2 | License MIT (placeholder until Adelson confirms). | Permissive, common for research software. | GPL, none |
| D3 | All file columns are read as strings, then parsed. | Keeps ids such as `007` and avoids pandas guessing dtypes. | `dtype` inference |
| D4 | Timestamps parsed with `format="mixed"`; tz-aware inputs converted to UTC; mixed tz-aware and naive is an error (E006). | Mixing would silently shift times. | assuming local time |
| D5 | `message_id`: if the input has none, `m0001…` per group in sorted order. | Stable ids for the human coding sheet. | global row numbers |
| D6 | Unit gap is inclusive: a message exactly 60 s after the previous one joins the unit. | "≤ 60 s" in DESIGN §2.2. | exclusive |
| D7 | Accent folding keeps one output character per input character, so replacement positions are exact. | Names like "José"/"jose" match without corrupting the surrounding text. | `unidecode` (changes length) |
| D8 | Extra names (`extra_names`) become `N1…`, separate from speakers. | They are people mentioned in text, not participants. | reuse `S` numbers |
| D9 | Token estimate before calls: characters / 3 + 6,000 overhead. Replaced by the measured prompt size after M2. | Conservative for PT/NL chat. | tokenizer call (needs network) |
| D10 | A log filter redacts every original username (≥ 2 chars) and extra name from log records. | Second line of defence; logs should contain ids only anyway. | — |
| D11 | `mypy --strict` with `warn_return_any = false` and the pydantic plugin. | pandas-stubs 3.0 types several DataFrame constructors as `Any`. | dropping strict |
| D12 | Tests that need the private dev file run only when `FGPD_DEV_DATA` points to it; the file is never committed (`*.tsv` is git-ignored outside `tests/fixtures`). | Privacy. | committing a sample |

### M1 audit (independent reviewer, 2026-10-19): 2 major and 9 minor findings, all fixed

| # | Finding | Fix |
|---|---|---|
| R1 (major) | Timezone detection by regex crashed on `-03`, `UTC` and DST changes. | Each value is parsed on its own; E006 if aware and naive are mixed, E004 otherwise; never raises. Aware times keep the first value's offset, so clock times stay local. |
| R2 (major) | The redacting log filter did not apply to child loggers. | Every module gets its logger from `get_logger()`, which attaches the filter; redaction also ignores accents. |
| R3 | NFD usernames were not matched in NFC text. | Every text column is normalized to NFC during validation. |
| R4 | A name from another group stayed in the text. | Names of students in other groups become `[NAME]`. |
| R5 | `extra_names` appeared in `repr` and metadata. | Shown as a count; their hash enters the fingerprint. |
| R6 | Rows that differ only in `message_id` were dropped as duplicates. | `message_id` is part of the duplicate key. |
| R7 | Excel cells reading `NA`, `null` or `N/A` were dropped. | Excel is read without NA conversion. |
| R8 | A username such as `S2` could collide with a pseudonym. | Prefixes switch to `ST`/`AGT` (W014). |
| R9 | Column, timestamp and id codes overlapped. | Mapped-but-missing column → E001 only; ambiguous synonyms → E002; empty timestamp → E003 only; an all-empty `message_id` column falls back to generated ids (I002). |
| R10 | `\r\n` broke one-line rendering; ids past `u999` broke sorting; `05/09` was read month-first silently. | `splitlines()`; zero-padded ids sized to the chat; `dayfirst` option and W015 when the order was guessed. |
| R11 | Missing tests. | 19 tests added. |

| # | Decision | Reason | Rejected |
|---|---|---|---|
| D13 | Invalid configuration raises `ConfigError` when the analyzer is built; E009 is reserved and not emitted. | Fail before reading any data. | reporting config errors per input |

## M2: prompts and codebook

| # | Decision | Reason | Rejected |
|---|---|---|---|
| D14 | Prompts address the reader as "you". The same text is the model's instruction and the human codebook. | Byte-identical sections without rewording. | separate model and human versions |
| D15 | One synthetic example per level, alternating PT and EN (DESIGN asked for two per level). | Keeps the cached prefix near 5k tokens and limits anchoring on examples. Can grow if the pilot shows confusion. | 32 examples |
| D16 | The antecedent is never also an evidence unit. G4 level ≥ 3 is restated as "≥ 2 evidence units, one of them from the antecedent's speaker after the first operation", which is the design's 3-unit exchange counted without the antecedent. | One rule for all goals; `participation` does not count the student who was only taken up. | antecedent inside evidence for G4 only |
| D17 | `docs/codebook.md` is generated from the prompts and committed; a test fails when it is stale. | DESIGN K R1 (no drift between prompts and codebook). | — |
| D18 | The Portuguese translation of the codebook is written in M6, after the pilot revisions. | Translate once, not after every prompt change. | translating now |
| D19 | References in the codebook header: full book reference of Michaels & O'Connor and the JCAL author list are marked "to be confirmed". | Not verifiable from the PDF alone. | guessing |
| D20 | Coding workbook: README, `transcript`, `messages`, one sheet per goal with a level dropdown. One workbook per group is recommended for coding studies. | Matches the model's output fields. | one long sheet |

### Blind-coder pilot 1 (2 dev chats, codebook as of M2): revisions to the prompts

An agent acting as a human coder applied the codebook to one dyad and one triad without any model output. Its 13 notes led to these prompt changes (the prompt-set hash changes; v1 is not yet released):

| Note | Change |
|---|---|
| Interleaved threads were merged into very long patterns, making counts coarse. | Segment by idea: close a pattern at its last evidence unit when another idea starts; a resumed idea is a new pattern that may cite the earlier antecedent. Replaces "code interleaved threads as one pattern". |
| Several antecedents / several agent units taken up. | Cite the one the first (earliest) evidence unit responds to. |
| G2 `content_reference` had no level. | Named in level 2. |
| Talk about the agent or the tool itself. | Listed as non-evidence. |
| What counts as a press in G3. | Defined: any question asking for a reason, evidence, a prediction or an explanation. |
| Self-revision prompted by a peer. | Counts as reasoned self-revision when the reason is stated; can also be G4. |
| G4 add-on without "because"; building further on a peer's addition; triads. | Add-on with new content = level 2; level 3 includes building further; level 4 says "at least two students". |
| Real first names in agent messages (privacy). | `redact_vocatives=True` (default): words that agents use to address someone are replaced by `[NAME]` in every message of the input (W016). `name_candidates()` lists them for the researcher only. |
| Some agent-account messages read as if a student wrote them. | Open question for Adelson (data, not codebook). |

## M3: backend, cache, post-validation

API facts re-checked against platform.claude.com on 2026-10-19: `output_config.format` (json_schema; enum, anyOf, `$ref`/`$defs`, `minItems` 0/1 supported; `minimum`, `maxItems`, `maxLength` not), `output_config.effort` (Haiku 5.5: low, medium [default], high, xhigh, max), prompt caching minimum 512 tokens for Haiku 5.5 with up to 4 breakpoints, refusals as `stop_reason: "refusal"`, Haiku 5.5 at $0.10 / $0.50 per MTok (cache write $0.125, read $0.01).

| # | Decision | Reason | Rejected |
|---|---|---|---|
| D21 | Transport retries (429, 529, 5xx, timeouts, `retry-after`) are left to the official SDK (`max_retries`). The runner handles content retries: `max_tokens` (once, 2× budget, cap 32k), refusal (once), unparseable output or wrong goal (once). | The SDK already implements backoff with jitter correctly. | a second retry layer |
| D22 | Every request carries an explicit timeout (default 300 s). | The SDK refuses non-streaming calls with large `max_tokens` unless a timeout is given; a 32k retry can take minutes. | streaming |
| D23 | Evidence units outside the span are removed (flag `evidence_outside_span_removed`); the pattern is dropped only if no evidence is left. The design said "drop". | Keeps patterns whose span is off by one unit; the guarantee "evidence inside the span" holds either way. | dropping the whole pattern |
| D24 | Human coding sheets go through the same validation with `apply=False`: everything is flagged, nothing is changed. | `coding-sheet check` reports exactly the rules applied to the model. | separate checker |
| D25 | `MockBackend` produces deterministic synthetic patterns with run-to-run variation; `ReplayBackend` answers only from recorded responses. | Offline tests, examples and bit-identical replays. | — |
| D26 | Prices live in `backends/pricing.py` with a version date. | Reported in run metadata. | TOML (needs Python 3.11) |

### M3 audit (independent reviewer): 4 major and 7 minor findings, all fixed

| # | Finding | Fix |
|---|---|---|
| R12 (major) | After a `max_tokens` retry, the response was cached under the escalated key, so reruns called the API again and replays failed. | The cache key always uses the configured `max_tokens`. |
| R13 (major) | The cache key did not include the backend, so mock responses could be served to a real run. | The key includes the backend namespace (`anthropic`, `mock`); `ReplayBackend` replays a named namespace. |
| R14 (major) | Merging overlapping patterns skipped the structural rules and could shrink evidence. | The merged pattern is re-checked; evidence is never removed; the antecedent is the first that is not evidence; if the union breaks a rule, the stronger pattern is kept (`overlap_dropped`). |
| R15 (major) | A malformed 200 response aborted the whole batch. | All API errors become `BackendError`; any unexpected error fails only its own call. |
| R16 | The key ignored the chat-facts line. | The key is the hash of the exact request, so everything sent is in it by construction. |
| R17 | With `pseudonymize=False`, agents were listed as students in the chat facts. | Uses the unit role. |
| R18 | A same-speaker antecedent was accepted for G1/G3. | Nulled and flagged (`antecedent_same_speaker_nulled`). |
| R19 | Report-only mode missed nested overlaps; displayed unknown ids as "None"; read 3.0 as 0. | Running maximum; raw ids kept; float levels accepted. |
| R20 | No schema fallback when the API rejects the enum schema. | On a 400, one retry with string ids (`schema_fallback`). |
| R21 | Missing tests. | Added (flags, upper-case ids, three-way merges, cache modes, malformed bodies). |

| # | Decision | Reason | Rejected |
|---|---|---|---|
| D27 | The G3 rule "an evidence unit preceded in the span by a press" is checked in code only as "there is an earlier unit in the span"; whether it is a press is the coder's judgement. | Code cannot read meaning. | keyword detection |

## M4: consensus, influence, outputs, CLI

| # | Decision | Reason | Rejected |
|---|---|---|---|
| D28 | Consensus patterns go through the same post-validation again (structural rules, merge of overlaps created by the medians; flag `consensus_merge`). | The median span or lower-median level can break a rule that every member satisfied. | trusting the members |
| D29 | `support ≥ 0.5` is applied literally: with 2 successful runs, a pattern found in 1 counts. | DESIGN §5. Groups with failed runs are `partial` and visible. | requiring 2 runs |
| D30 | Agent unit for `temporal_only`: the latest agent unit at or before the pattern start, else the earliest inside. `agent_lag_s` = first evidence unit after the agent minus the agent unit. | DESIGN §11. | — |
| D31 | Results keep internal tables (`units`, `groups`, `calls`, `consensus`) so that `patterns`, `frequencies` and `timeseries` are recomputed on load; changing lag, rule or bin width needs no API call. | U5, §11. | storing only final tables |
| D32 | `run_patterns` stores every run's validated patterns; `freq_run_mean`/`freq_run_sd` are computed from them. | Run-to-run dispersion is part of the measurement. | — |
| D33 | Cost estimate: one cache write per chat, cache reads for the other 4·n_runs − 1 calls, 1,500–4,000 output tokens per call. Dev set (6 groups, 3 runs): about $0.10 (range $0.07–$0.16). | Output (mostly thinking) dominates. | — |
| D34 | The CLI exits 0 ok, 2 validation errors, 3 backend failure (every call failed), 4 partial (some calls failed) or problems found by `coding-sheet check`. | DESIGN §7. | — |
| D35 | Golden replay files from real responses are recorded in M5 (they need real model output); M4 covers the same invariants with the mock backend and property tests. | No real responses exist yet. | synthetic "golden" files |

### M4 audit (independent reviewer): 1 blocker, 4 major and 7 minor findings

| # | Finding | Fix |
|---|---|---|
| R22 (blocker) | CSV results loaded ids such as `007`, `NA` and `001` as numbers or NaN. | CSV is read as text; known numeric, boolean and time columns are cast explicitly. Round-trip test with those ids. |
| R23 (major) | Groups not passed to `analyze(groups=...)` were reported as `failed`. | New status `not_selected` (NA metrics). |
| R24 (major) | A run that needed the string-id schema fallback could not be replayed, and paid for a failing call on every rerun. | The runner goes straight to the fallback when its response is cached or recorded; failing attempts are counted. |
| R25 (major) | With an even number of runs, the lower-median span could exclude shared evidence, and the pattern vanished without trace. | The consensus span is widened to cover the consensus evidence; any cluster dropped during consensus validation is kept as unstable (`dropped_in_consensus`) and counted in `run_metadata.consensus_qc`. |
| R26 (major) | `fgpd influence` on parquet results wrote CSV and left stale files; a bad rule crashed. | Keeps the directory's format and regenerates every existing `timeseries_*` file; errors exit 2. |
| R27 | `no_dispersion` was never flagged for `n_runs=1`. | Flag on every consensus pattern and in metadata. |
| R28 | One antecedent out of three members won the consensus for G1/G3. | Optional antecedents need a strict majority; required ones (G2, G4) take the most cited. |
| R29 | Fleiss' kappa returned 1.0 when no unit was ever coded. | Returns None (undefined). |
| R30 | Dtypes differed from §10. | Categories for goal, label, status, influence and position; Int8 levels; Int32 counts in the time series; `n_runs_ok = 0` for failed or refused. |
| R31 | Missing API/CLI pieces. | `save(include_pseudonym_map=True)` (in memory only, with a PrivacyWarning); per-group statuses in metadata; `analyze --group/--cache/--cache-dir`; `cache * --cache-dir`; configuration errors exit 2; no eligible group exits 2; empty time series keep their columns. |
| R32 | `coding-sheet check` reported pattern numbers, not spreadsheet rows, and skipped rows without a group. | Reports spreadsheet rows; a missing group is reported (filled automatically when the workbook has one group). |
| R33 | An unexpected error dropped the usage of earlier attempts. | Usage is kept. |

| # | Decision | Reason | Rejected |
|---|---|---|---|
| D36 | `FGPD_*` environment variables and `fgpd.toml` (DESIGN §6 precedence) are deferred to v0.2; the CLI takes options and the API takes arguments. | Not needed for the dev run. | — |

## Name key (2026-10-19, user request)

Adelson chose a deterministic name key over an LLM-based name detector (an LLM pass would send the raw names to the API, which pseudonymization is meant to avoid).

| # | Decision | Reason | Rejected |
|---|---|---|---|
| D37 | `name_map` (real name → username in the data; dict, JSON file or a printed Python dict) replaces every known variant of a real name inside the text: the full name, each part of a multi-part name ("joão pedro" → "joão"), and with `fuzzy_names=True` a single-word name with one extra letter, or one missing letter for names of 7+ letters ("fraier" → "fraifer"). Substitutions never match. | Catches typos and first names without a dictionary. "bianca" minus one letter is "banca", a common word, hence the 7-letter floor. | LLM detection; edit distance with substitutions |
| D38 | A name maps to the pseudonym of that student in the current group; anyone else (another group, unknown, a name part shared by two people) becomes `[NAME]`. Vocatives found in agent messages that are one letter from a known name take that person's pseudonym. | Keeps "who addressed whom" inside the group. | `[NAME]` for everything |
| D39 | Fuzzy replacements are counted (W017); `name_map` appears in metadata only as a count; its hash enters the fingerprint. | Review and privacy. | — |

Dev data with the key: 0 real names left in the transcripts (13 caught as agent vocatives, 1 typo caught by fuzzy matching).

## Dev data: Wizard of Oz (stated by Adelson, 2026-10-19)

The dev chats come from a study in which one student in each group played the agent and had to use predefined phrases. Messages of that account are all treated as agent context, including the few the student typed freely.

## M5: first real run on the dev data (2026-10-19)

Run on Adelson's Mac (the Cowork VM's proxy blocks API calls with a user key): 6 groups × 4 goals × 3 runs, Haiku 5.5, effort medium, `name_map`, PT justifications.

| # | Finding | Decision |
|---|---|---|
| D40 | Cost $0.385 against an estimate of $0.104. Output (mostly thinking) was 8.6k tokens per call at the median, mean 9.2k, p90 16k, against the 2.5k assumed; 6 of 72 calls hit `max_tokens=16000` and were retried at 32k. | Estimate now uses 3k / 9k / 16k output tokens; default `max_tokens` 32,000 and retry cap 64,000 (a cut-off call is paid and thrown away). Responses recorded with the old default replay with `max_tokens=16000`. An effort comparison (low vs medium) is the cost lever to test next. |

### Docs pass (Sphinx-ready docstrings) and follow-ups

NumPy-style docstrings on the whole public API, pydocstyle in ruff, Sphinx skeleton in `docs/sphinx/` (`pip install -e ".[docs]"`; `sphinx-build -b html docs/sphinx docs/sphinx/_build`). Points the docs agent raised, fixed: `redact_vocatives`, `dayfirst` and `sort` enter the config fingerprint; one character-per-token estimator; `fgpd timeseries` keeps the results' format; design §6 defaults synced with the code. Still open (v0.2): `export_codebook(format, language)` from design §7, and `analyze(dry_run=True)` returning a dict rather than a typed object.

### Blind coder, round 2 (current codebook, same 2 chats)

Totals: 53 human vs 49 model patterns; summed frequency 30.0 vs 26.25; correlation of the 8 chat × goal frequencies 0.76. Unit-membership kappa 0.89/0.76/0.81/0.90 (dyad G1–G4) and 1.00/0.71/0.62/0.47 (triad). Matched patterns differ by at most one level. The human splits G1 into more patterns than the model. Remaining ambiguities (segmentation of threads, cross-speaker warrant chains in G3, "both directions" in G2 triads, "revised position" in G4 level 4) are the input for the M6 codebook revision. These are development numbers (2 chats, an agent as coder), not validation.

### Codebook revision after blind-coder round 2 (2026-10-19)

| Ambiguity | Change |
|---|---|
| Strict segmentation produced one-unit fragments; an answer separated by one unit broke the thread. | A brief interruption (≤ 2 units on something else) does not close a pattern; a pattern closes when another idea runs for 3+ units. "One idea" is defined (a claim, proposal, interpretation or question with its elaboration, justification and replies). |
| Where a span starts. | At the agent or peer unit the first evidence responds to (antecedent included), when it is at most two units before; otherwise at the first evidence unit. |
| G1: unrequested clarification; what is a request; distinct ideas. | Unrequested clarification or an example inside the same unit counts at level 2; a request is any question asking to say more, explain, give an example or clarify (agent's "o que mais você acrescentaria?" included); "on request" needs a later unit; each distinct idea starts its own G1 pattern. |
| G2 "both directions" in triads. | At least two students each take up the other's content; any pair counts. |
| G3: chains across speakers; revision without a reason. | Level-4 warrant chains come from one student (cross-student building is G4); a revision without a stated reason is not G3. |
| G4: responding to unreasoned opinions; "revised position". | An unreasoned ranking or opinion is a G1 idea; a reply is level 1 unless it gives a reason (then level 2). A revised or joint position = explicitly changing, narrowing, qualifying or combining positions, or both sides giving reasons for different positions. |
| A peer's question about the agent's wording as antecedent. | Explicitly valid. |
