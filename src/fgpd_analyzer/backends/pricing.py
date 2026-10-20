"""Price table (USD per million tokens) and per-call cost.

Used by the cost estimate before a run and by the run metadata after it
(outputs stage of the pipeline). Prices were checked against
https://platform.claude.com/docs/en/about-claude/pricing on 2026-10-19.
Update `PRICE_TABLE_VERSION` whenever a value changes.
"""

from __future__ import annotations

#: Date of the price table, stored in cost estimates and run metadata.
PRICE_TABLE_VERSION = "2026-10-19"

# Prompts up to 100k input tokens. Above that, Haiku 5.5 costs 5x (kept out by
# max_prompt_tokens = 80k).
#: Model → USD per million tokens for input, output, cache writes and cache reads.
PRICES: dict[str, dict[str, float]] = {
    "claude-haiku-5-5": {
        "input": 0.10,
        "output": 0.50,
        "cache_write_5m": 0.125,
        "cache_write_1h": 0.20,
        "cache_read": 0.01,
    },
}


def cost_usd(model: str, usage: dict[str, int]) -> float | None:
    """Return the cost of one call in USD.

    Parameters
    ----------
    model : str
        Model id.
    usage : dict of str to int
        Token counts ``input_tokens``, ``cache_creation_input_tokens`` (priced
        as 5-minute cache writes), ``cache_read_input_tokens`` and
        ``output_tokens``; missing counts are 0.

    Returns
    -------
    float or None
        Cost in USD, or None when the model is not in `PRICES`.
    """
    p = PRICES.get(model)
    if p is None:
        return None
    m = 1e-6
    return (
        usage.get("input_tokens", 0) * p["input"] * m
        + usage.get("cache_creation_input_tokens", 0) * p["cache_write_5m"] * m
        + usage.get("cache_read_input_tokens", 0) * p["cache_read"] * m
        + usage.get("output_tokens", 0) * p["output"] * m
    )
