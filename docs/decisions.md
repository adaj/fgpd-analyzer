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
