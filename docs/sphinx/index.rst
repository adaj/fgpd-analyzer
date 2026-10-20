fgpd-analyzer
=============

``fgpd-analyzer`` measures the **Four Goals for Productive Discussion** (Michaels &
O'Connor) in student group chats with a Claude model: G1 *share*, G2 *listen*,
G3 *deepen* and G4 *engage*. For every group it finds where each goal is pursued,
how fully (levels 1–4, score = level / 4), and whether a pedagogical agent's
message was taken up.

The model applies the same written codebook that human coders use
(``docs/codebook.md``), so human–model agreement can be measured on identical
units.

.. note::

   Status: 0.1 development version. The prompts have not yet been validated
   against human coding. Do not use the numbers as findings before that study.

Install
-------

.. code-block:: bash

   pip install "fgpd-analyzer[xlsx,parquet] @ git+https://github.com/adaj/fgpd-analyzer"
   export ANTHROPIC_API_KEY=...        # only needed for real analyses

Quick start
-----------

.. code-block:: python

   from fgpd_analyzer import FGPDAnalyzer

   fa = FGPDAnalyzer(
       agent_pattern="Clair",           # usernames containing this (any case) are agents
       influence_lag="2min",            # window for agent influence (applied after the fact)
       n_runs=3,                        # independent runs, majority consensus
       justification_language="pt",
   )

   report = fa.validate_input("chats.csv")          # no network
   print(report.to_text())
   print(fa.estimate_cost("chats.csv"))             # no network
   print(fa.preview("chats.csv", group="g01"))      # exactly what the model would see

   res = fa.analyze("chats.csv")                    # API calls
   res.frequencies                     # one row per group x goal; `frequency` = main metric
   res.patterns                        # where each pattern happened, level, agent influence
   res.timeseries(every="5min")        # cumulative series; any bin width, no new calls
   res.with_influence(lag="60s")       # other lag, no new calls
   res.save("results/")

Input: one row per message with a group, a username, a timestamp and the text,
for one or many groups. Column names are matched automatically (``Group``,
``Username``, ``Timestamp``, ``Message``, ...) or mapped explicitly:
``columns={"text": "Mensagem"}``.

Pipeline
--------

input → validation → units and pseudonyms → prompts → one request per group ×
goal × run → post-validation → consensus over runs → agent influence (post hoc)
→ output tables.

1. Consecutive messages of one student within 60 s form one **unit**; each agent
   message is its own unit. Students become S1, S2, ..., agents AG1, ...
2. For each goal, one call over the whole chat returns every **pattern** of that
   goal: its span, evidence units, the peer unit taken up (antecedent),
   indicators, a level and the agent unit taken up, if any.
3. Code enforces the codebook's structural rules, then builds a consensus over
   the runs.
4. **Agent influence** = the students take up the content of an agent message
   inside the lag. Temporal proximity alone is reported (``temporal_only``) but
   not counted.

The main metric is ``frequency`` = Σ pattern scores per group and goal.
``rate_per_100``, ``n_student_messages`` and ``duration_min`` are reported next
to it.

Privacy
-------

Chat transcripts are sent to the Anthropic API. Use only data whose participants
consented to this processing. Pseudonymized data are still personal data.

Design and decisions: ``docs/design.md`` and ``docs/decisions.md`` in the
repository.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   api
