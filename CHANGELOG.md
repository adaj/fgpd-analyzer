# Changelog

## 0.1.0 (2026-10-19)

First release. Prompt set **v1** (frozen; `prompts/v1/MANIFEST.json`). Not yet validated against
human coders: treat measures as exploratory until the human-coder pilot is reported.

Highlights
- One LLM call per goal over the whole chat; patterns located by span, levels 1–4, consensus over 3 runs.
- Main metric `frequency` (Σ pattern scores), with rate per 100 student messages, duration and run-to-run SD.
- Post-hoc agent influence (content uptake within a lag), recomputable without API calls.
- Pseudonymization by default, name key with variants and one-letter typos, agent vocative redaction.
- Human codebook generated from the prompts (EN canonical + PT translation), coding workbook and checker.
- CLI and Python API; local response cache and bit-identical replay; Sphinx documentation with quick start.

Development log

- M0: package scaffold, CI, exceptions, redacting logger.
- M1: input reading, column mapping, validation (§9), roles, Student Contribution Units, pseudonymization, transcript rendering, preview.
- M2: base prompts v1, prompt registry (list/show/set/reset/export), generated codebook (docs/codebook.md) with a staleness test, human coding workbook export/read.
- M1 audit fixes (timezones, log redaction, Unicode, cross-group names, duplicates, Excel NA, pseudonym collisions).
- M3: patterns_v1 schema builder, request builder with 3 cache breakpoints, Anthropic/mock/replay backends, SQLite response cache, async runner with content retries, post-validation and structural constraints.
- M4: run consensus (IoU + Hungarian, lower-median level), post-hoc agent influence, frequencies, cumulative time series, patterns with message locations, save/load (csv, parquet), cost estimate, dry run, CLI.
- M3 audit fixes (cache key, backend namespaces, merge re-check, robust error handling, schema fallback).
