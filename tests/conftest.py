from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

DEV_DATA = os.environ.get("FGPD_DEV_DATA", "")


def make_chat(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    """rows = (group, username, 'HH:MM:SS', text) on a fixed date."""
    return pd.DataFrame(
        [
            {"Group": g, "Username": u, "Timestamp": f"2026-09-17 {t}", "Message": m}
            for g, u, t, m in rows
        ]
    )


@pytest.fixture
def small_chat() -> pd.DataFrame:
    """Dyad with an agent; 10 student messages."""
    rows = [
        ("g1", "agente 1", "10:00:00", "Olá! Qual risco vocês acham mais importante?"),
        ("g1", "Mariana", "10:00:30", "acho que privacidade"),
        ("g1", "Mariana", "10:01:00", "porque os dados vazam"),  # +30 s -> same unit
        ("g1", "Joao", "10:02:30", "concordo Mariana, mas e o viés?"),
        ("g1", "Joao", "10:03:31", "o viés afeta mais gente"),  # +61 s -> new unit
        ("g1", "agente 1", "10:04:00", "Joao, pode explicar melhor?"),
        ("g1", "Joao", "10:04:20", "sim, modelos aprendem com dados enviesados"),
        ("g1", "Mariana", "10:05:00", "ok"),
        ("g1", "Mariana", "10:05:10", "faz sentido, a Mariana aqui concorda"),
        ("g1", "Joao", "10:06:00", "vamos escrever isso"),
        ("g1", "Mariana", "10:07:00", "beleza"),
        ("g1", "Joao", "10:08:00", "fechado"),
    ]
    return make_chat(rows)


@pytest.fixture
def dev_data() -> Path:
    if not DEV_DATA or not Path(DEV_DATA).exists():
        pytest.skip("set FGPD_DEV_DATA to the private dev chat file")
    return Path(DEV_DATA)
