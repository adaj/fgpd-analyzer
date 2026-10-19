"""Human coding workbook (DESIGN §12): export and read back.

The workbook shows exactly the units the model sees. Coders fill one sheet per
goal with the same fields as the model's output schema.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from .codebook import GOAL_INDICATORS
from .config import GOAL_LABELS, GOALS
from .exceptions import ConfigError
from .pipeline import PreparedInput

FORM_COLUMNS: tuple[str, ...] = (
    "start_id",
    "end_id",
    "evidence_ids",
    "antecedent_id",
    "indicators",
    "level",
    "agent_uptake_id",
    "justification",
)
_ID_RX = re.compile(r"u\d{3,}", re.IGNORECASE)


def export_coding_sheet(
    prep: PreparedInput,
    path: Path,
    *,
    groups: list[str] | None = None,
    prompt_set_hash: str = "",
    coder: str = "",
) -> Path:
    """One workbook per call. With several groups, the transcript and forms carry a
    `group` column; for a coding study, export one workbook per group."""
    try:
        from openpyxl.styles import Alignment
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError as e:  # pragma: no cover
        raise ConfigError("the coding sheet needs: pip install 'fgpd-analyzer[xlsx]'") from e
    from .render import render_unit

    chosen = groups or prep.eligible
    missing = [g for g in chosen if g not in prep.groups]
    if missing:
        raise KeyError(f"groups not found: {missing}")
    rows, msg_rows = [], []
    for g in chosen:
        pg = prep.groups[g]
        for u in pg.units:
            rows.append(
                {
                    "group": g,
                    "unit_id": u.unit_id,
                    "time": f"{u.t_start:%H:%M:%S}",
                    "minutes": round(u.t_min, 2),
                    "speaker": f"{u.speaker} [AGENT]" if u.is_agent else u.speaker,
                    "text": render_unit(u).split(" | ", 4)[4],
                }
            )
        msg_rows.append(pg.messages[["group", "message_id", "unit_id", "row_index"]])
    transcript = pd.DataFrame(rows)
    messages = pd.concat(msg_rows, ignore_index=True) if msg_rows else pd.DataFrame()
    form_cols = (["group"] if len(chosen) > 1 else []) + list(FORM_COLUMNS)
    readme = pd.DataFrame(
        {
            "field": ["coder", "prompt_set_hash", "groups", "instructions"],
            "value": [
                coder,
                prompt_set_hash,
                ", ".join(chosen),
                "Follow the FGPD codebook with the same prompt_set_hash. "
                "Fill one row per pattern in each goal sheet.",
            ],
        }
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        readme.to_excel(xw, sheet_name="README", index=False)
        transcript.to_excel(xw, sheet_name="transcript", index=False)
        messages.to_excel(xw, sheet_name="messages", index=False)
        for goal in GOALS:
            pd.DataFrame(columns=form_cols).to_excel(xw, sheet_name=goal, index=False, startrow=2)
            ws = xw.sheets[goal]
            ws["A1"] = (
                f"{goal} {GOAL_LABELS[goal]}: indicators = {', '.join(GOAL_INDICATORS[goal])}"
            )
            ws["A2"] = "minutes"
            lvl_col = chr(ord("A") + form_cols.index("level"))
            dv = DataValidation(type="list", formula1='"1,2,3,4"', allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{lvl_col}4:{lvl_col}500")
        tws = xw.sheets["transcript"]
        tws.column_dimensions["F"].width = 100
        for cell in tws["F"]:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    return path


def _first(value: Any) -> str | None:
    ids = _ids(value)
    return ids[0] if ids else None


def _ids(value: Any) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    return [m.group(0).lower() for m in _ID_RX.finditer(str(value))]


def read_coding_sheet(path: Path) -> pd.DataFrame:
    """Return the coded patterns as a long table: group, goal, and the form fields
    parsed into lists and ints (`run_patterns` shape, coder = README coder)."""
    book = pd.read_excel(path, sheet_name=None, dtype=str)
    meta = dict(zip(book["README"]["field"], book["README"]["value"], strict=False))
    groups = [g.strip() for g in str(meta.get("groups", "")).split(",") if g.strip()]
    out = []
    for goal in GOALS:
        df = pd.read_excel(path, sheet_name=goal, dtype=str, header=2)
        df = df.dropna(how="all")
        for idx, r in df.iterrows():
            ind = [
                s.strip()
                for s in re.split(r"[,;\s]+", str(r.get("indicators") or ""))
                if s.strip() and s.strip().lower() != "nan"
            ]
            level = pd.to_numeric(r.get("level"), errors="coerce")
            out.append(
                {
                    "group": r.get("group")
                    if "group" in df.columns and isinstance(r.get("group"), str)
                    else (groups[0] if len(groups) == 1 else None),
                    "goal": goal,
                    "start_id": _first(r.get("start_id")),
                    "end_id": _first(r.get("end_id")),
                    "evidence_ids": _ids(r.get("evidence_ids")),
                    "antecedent_id": _first(r.get("antecedent_id")),
                    "indicators": ind,
                    "level": None if pd.isna(level) else int(level),
                    "agent_uptake_id": _first(r.get("agent_uptake_id")),
                    "justification": ""
                    if pd.isna(r.get("justification"))
                    else str(r.get("justification")),
                    "coder": meta.get("coder") or "",
                    "sheet_row": int(idx) + 4,  # header on row 3 of the goal sheet
                }
            )
    return pd.DataFrame(out)


def check_coding_sheet(prep: PreparedInput, path: Path) -> pd.DataFrame:
    """Run the model's post-validation in report-only mode on a filled workbook.
    Returns one row per flagged pattern (empty when everything is fine)."""
    from .postprocess import QC, validate_patterns

    coded = read_coding_sheet(path)
    rows = []
    for (group, goal), df in coded.groupby(["group", "goal"], sort=False, dropna=False):
        df = df[df["level"].notna() | df["start_id"].notna()]
        if df.empty:
            continue
        if not isinstance(group, str) or group not in prep.groups:
            for sr in df["sheet_row"]:
                rows.append(
                    {
                        "group": group,
                        "goal": goal,
                        "row": int(sr),
                        "problems": "missing or unknown group",
                    }
                )
            continue
        raw = df[
            [
                "start_id",
                "end_id",
                "evidence_ids",
                "antecedent_id",
                "indicators",
                "level",
                "agent_uptake_id",
                "justification",
            ]
        ].to_dict(orient="records")
        pats, _ = validate_patterns(
            raw, prep.groups[group], str(goal), 0, source="human", qc=QC(), apply=False
        )
        for sr, p in zip(df["sheet_row"], pats, strict=True):
            if p.qc_flags:
                rows.append(
                    {
                        "group": group,
                        "goal": goal,
                        "row": int(sr),
                        "problems": ", ".join(p.qc_flags),
                    }
                )
    return pd.DataFrame(rows, columns=["group", "goal", "row", "problems"])
