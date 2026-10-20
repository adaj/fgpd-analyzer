# fgpd-analyzer

<p align="center">
  <img src="docs/sphinx/_static/fgpd_quadrants.jpg" alt="The four goals on two axes: reasoning level (medium, high) and mode of engagement (individual, joint). Goal 1 sharing thoughts, Goal 2 orienting and listening, Goal 3 deepening the reasoning, Goal 4 engaging with each other's reasoning." width="100%">
</p>
<p align="center"><em>The Four Goals for Productive Discussion across reasoning level and mode of engagement (de Araujo, 2024, Figure 5.1). Background, prior measurement and validity notes: <a href="docs/sphinx/fgpd.md">docs/sphinx/fgpd.md</a>.</em></p>

Measures the **Four Goals for Productive Discussion** (Michaels & O'Connor) in student group
chats with a Claude model: G1 *share*, G2 *listen*, G3 *deepen*, G4 *engage*. For every group
it finds where each goal is pursued, how fully (levels 1–4, score = level/4), and whether a
pedagogical agent's message was taken up.

The model applies the same written codebook that human coders use (`docs/codebook.md`), so
human–model agreement can be measured on identical units.

> Status: 0.1 development version. The prompts have not yet been validated against human
> coding. Do not use the numbers as findings before that study.

## Install

```bash
pip install "fgpd-analyzer[xlsx,parquet] @ git+https://github.com/adaj/fgpd-analyzer"
export ANTHROPIC_API_KEY=...        # only needed for real analyses
```

## Quick start

```python
from fgpd_analyzer import FGPDAnalyzer

fa = FGPDAnalyzer(
    agent_pattern="Clair",           # usernames containing this (any case) are agents
    influence_lag="2min",            # window for agent influence (applied after the fact)
    n_runs=3,                        # independent runs, majority consensus
    justification_language="pt",
)

report = fa.validate_input("chats.csv")          # no network
print(report.to_text()); print(report.info)
print(fa.estimate_cost("chats.csv"))             # no network
print(fa.preview("chats.csv", group="g01"))      # exactly what the model would see

res = fa.analyze("chats.csv")
res.frequencies                     # one row per group x goal; `frequency` = main metric
res.patterns                        # where each pattern happened (message ids, times), level,
                                    # indicators, agent influence
res.timeseries(every="5min")        # cumulative series; any bin width, no new calls
res.with_influence(lag="60s")       # other lag, no new calls
res.save("results/")
```

Input: one row per message with a group, a username, a timestamp and the text, for one or
many groups. Column names are matched automatically (`Group`, `Username`, `Timestamp`,
`Message`, …) or mapped explicitly: `columns={"text": "Mensagem"}`.

Command line: `fgpd validate`, `fgpd estimate`, `fgpd analyze CHATS -o DIR`,
`fgpd timeseries DIR --every 10min`, `fgpd influence DIR --lag 90s`, `fgpd prompts list|show|export`,
`fgpd codebook export`, `fgpd coding-sheet export|check`, `fgpd cache info|clear`.

## How it measures

1. Consecutive messages of one student within 60 s form one **unit**; each agent message is its
   own unit. Students become S1, S2…, agents AG1…
2. For each goal, one call over the whole chat returns every **pattern** of that goal: its span,
   evidence units, the peer unit taken up (antecedent), indicators, a level and the agent unit
   taken up, if any. Output is constrained by a JSON schema whose unit ids are enums.
3. Code enforces the codebook's structural rules (e.g. G2/G4 need a peer's unit; G1 level 4
   needs two students), then builds a consensus over the runs.
4. **Agent influence** = the students take up the content of an agent message inside the lag.
   Temporal proximity alone is reported (`temporal_only`) but not counted.

The main metric is `frequency` = Σ pattern scores per group and goal (raw amount of productive
talk). `rate_per_100` (per 100 student messages), `n_student_messages` and `duration_min` are
always reported next to it, so that "more productive talk" can be told apart from "more talk".

## Prompts and codebook

```python
fa.list_prompts()                          # names, hashes, builtin/custom
print(fa.show_prompt("goal_G2", rendered=True))
fa.set_prompt("goal_G2", open("my_G2.md").read())   # must keep the same template variables
fa.export_codebook("codebook.md")          # human codebook from the current prompts
fa.export_coding_sheet("chats.csv", "coder_A.xlsx", groups=["g01"])
```

Results made with custom prompts carry `prompt_set_version = "custom-…"` and are not comparable
with results from the built-in prompts.

## Privacy

Chat transcripts are sent to the Anthropic API. Use only data whose participants consented to
this processing. Pseudonymized data are still personal data.

- Usernames are replaced by pseudonyms in the speaker field and inside message text (names of
  three or more characters, ignoring case and accents). Names that agents use to address
  students are detected and replaced by `[NAME]`. Add other known names with `extra_names`.
- No output table contains message text. The only free text is the model's justification.
- Logs contain ids, counts and hashes; a filter redacts known names as a second line of defence.

## Development

```bash
pip install -e ".[dev]"
pytest                       # offline; FGPD_DEV_DATA=... enables the private-data tests
ruff check src tests && mypy
python -m fgpd_analyzer.tools.regenerate_docs   # after changing a prompt
```

Design: `docs/design.md`. Every implementation decision: `docs/decisions.md`.
