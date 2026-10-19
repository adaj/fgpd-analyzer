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
