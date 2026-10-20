# Quick start

From raw chat logs to FGPD measures, in eight steps. Every step except 5 runs offline.

## 1. Install

```bash
git clone https://github.com/adaj/fgpd-analyzer
cd fgpd-analyzer
python3 -m venv .venv
source .venv/bin/activate          # do this in every new terminal
pip install -e ".[xlsx,parquet]"   # add ,docs to build this documentation
fgpd version
```

If a terminal says `command not found: fgpd` (or `sphinx-build`), the virtual environment is not
active: run `source .venv/bin/activate` again. The prompt then starts with `(.venv)`.

## 2. API key

Create a key in the Anthropic Console (a dedicated key with a low spending limit is a good idea)
and store it in a `.env` file at the repository root. `.env` is ignored by git.

```bash
printf 'ANTHROPIC_API_KEY=\n' > .env && chmod 600 .env
# paste the key after the "=", then load it in the terminal:
set -a; source .env; set +a
```

## 3. Prepare the input

One row per message, for one or many groups, with four columns: group, username, timestamp and
text. Usual names (`Group`, `Username`, `Timestamp`, `Message`, `grupo`, `mensagem`, ...) are
recognized; others are mapped with `-c text=Mensagem` (CLI) or `columns={"text": "Mensagem"}`.
Accepted formats: csv, tsv, xlsx, parquet.

Keep chat files and results **outside git** (the repository ignores `results/`, `*.tsv`,
`*.jsonl`). Use only data whose participants consented to processing by an external LLM
provider.

**Agents.** Usernames that contain `--agent-pattern` (default `agent`, any case) are agents:
their messages are context and never count as student evidence. Use e.g.
`--agent-pattern Clair`.

**Names.** Usernames become S1, S2, ... (students) and AG1, ... (agents), also inside message
text. If the export already replaced usernames by codes but real names still appear in the
text, give the name key (`{"real name": "username"}`, JSON):

```bash
--name-map results/private/name_map.json
```

Full names, name parts ("joão" for "joão pedro") and one-letter typos are replaced.

## 4. Validate and estimate (no network)

```bash
fgpd validate chats.tsv --exclude-flagged --name-map results/private/name_map.json
fgpd estimate chats.tsv --exclude-flagged
```

`validate` reports problems (missing columns, bad timestamps, tiny groups, groups where the agent
writes more than 40% of the messages) and the eligible groups. `--exclude-flagged` leaves out the
groups flagged for agent share. `estimate` prints the expected cost: with Claude Haiku 5.5 and
3 runs, about **US$0.06 per 100-message chat**.

To see exactly what the model will receive for one group:

```python
from fgpd_analyzer import FGPDAnalyzer
print(FGPDAnalyzer(name_map="results/private/name_map.json").preview("chats.tsv", group="g01"))
```

## 5. Analyze

```bash
fgpd analyze chats.tsv -o results/run1 \
  --exclude-flagged --name-map results/private/name_map.json \
  --justification-language pt --chat-language pt-BR \
  --every 5min --every 10min
fgpd cache export results/run1/responses.jsonl --results results/run1
```

Four calls per group (one per goal) × 3 runs. Responses are cached locally, so re-running with
the same input and settings costs nothing. The export lets anyone replay the analysis
bit-identically without the API (`ReplayBackend`).

## 6. Read the outputs (`results/run1/`)

| File | Content |
|---|---|
| `frequencies.csv` | One row per group × goal. **`frequency`** (Σ pattern scores) is the main metric; `count`, `rate_per_100`, `n_student_messages`, `duration_min`, `participation`, `influenced_frequency`, run-to-run SD and `status` are next to it. |
| `patterns.csv` | One row per pattern: goal, level (1–4), score, indicators, start/end message ids and times, evidence and antecedent, the model's justification, and agent influence (`uptake`, `temporal_only`, `none`). |
| `timeseries_5min.csv` | Cumulative frequency per group × goal every 5 minutes. |
| `unstable_patterns.csv`, `run_patterns.csv` | Patterns found in fewer than half of the runs; every run's patterns. |
| `messages.csv`, `units.csv` | Message ↔ unit ↔ row map (no text). |
| `run_metadata.json` | Model, prompt-set hash, settings, token usage, cost, quality-control counts, cross-run stability. |

Change the influence lag or the bin width later, without new calls:

```bash
fgpd influence results/run1 --lag 90s
fgpd timeseries results/run1 --every 10min
```

## 7. Interpret with care

- Compare groups of the same size, and report `n_student_messages` and `duration_min` with the
  frequencies: a group that talks more can show more patterns without talking better
  (`rate_per_100` separates the two).
- Each goal measures something different (see {doc}`fgpd`); do not add them into one score.
- Patterns with `support` below 1 were not found in every run; `freq_run_sd` shows how much the
  frequency moved between runs.
- The prompts (prompt set v1) have **not yet been validated against human coders**. Until
  that pilot is done, treat the measures as exploratory.

## 8. Human coding (for reliability studies)

```bash
fgpd codebook export codebook.md --justification-language pt     # English; PT translation in docs/
fgpd coding-sheet export chats.tsv coder_A_g01.xlsx --group g01 --coder A
fgpd coding-sheet check chats.tsv coder_A_g01.xlsx                # same rules the model follows
```

Coders read the codebook ({doc}`codebook`; Portuguese: {doc}`codebook_pt`), code one workbook per
chat in four passes (G1 to G4), and run `check` before handing it in. Human and model codes refer
to the same units, so agreement is computed directly.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `command not found: fgpd` | Activate the environment: `source .venv/bin/activate`. |
| `API error 401` | Key missing or invalid: `set -a; source .env; set +a`, check the key in the Console. Sandboxed environments with an egress proxy may block the API; run on your own machine. |
| `disk I/O error` on the cache | The cache folder is on a network or synced drive; use `--cache-dir ~/.cache/fgpd` or the default. |
| Cost far above the estimate | The model thinks more on long or dense chats; check `run_metadata.json` → `usage_total`. Try `effort="low"` in Python for a cheaper sensitivity run. |
| A group is missing from the results | See `validation.json`: it may be `insufficient_data` (fewer than 8 student messages or 2 students), `excluded` (agent share) or `too_long`. |
