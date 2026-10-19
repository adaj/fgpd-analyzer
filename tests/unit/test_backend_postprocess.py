from __future__ import annotations

import asyncio
import json

import httpx2 as httpx
import pytest

from conftest import make_chat
from fgpd_analyzer import FGPDAnalyzer
from fgpd_analyzer.backends.anthropic import AnthropicBackend
from fgpd_analyzer.backends.base import LLMResponse
from fgpd_analyzer.backends.mock import MockBackend, ReplayBackend
from fgpd_analyzer.backends.pricing import cost_usd
from fgpd_analyzer.cache import ResponseCache
from fgpd_analyzer.codebook import ALL_INDICATORS
from fgpd_analyzer.postprocess import parse_response, validate_patterns
from fgpd_analyzer.request import build_payload, build_request
from fgpd_analyzer.runner import Job, run_jobs
from fgpd_analyzer.schemas import build_schema


@pytest.fixture
def chat():
    """u001 AG1, then S1/S2 alternating (each message its own unit, 2 min apart)."""
    rows = [("g", "agente 1", "10:00:00", "Por que vocês acham isso?")]
    for i in range(10):
        rows.append(
            (
                "g",
                "Ana" if i % 2 == 0 else "Bia",
                f"10:{2 * i + 1:02d}:00",
                f"mensagem {i} com ideia",
            )
        )
    rows.append(("g", "agente 1", "10:25:00", "Ana, pode explicar?"))
    rows.append(("g", "Ana", "10:26:00", "sim porque sim"))
    return make_chat(rows)


@pytest.fixture
def prepared(chat):
    fa = FGPDAnalyzer()
    p = fa.prepare(chat)
    return fa, p.groups["g"]


def P(**kw):
    base = {
        "start_id": "u002",
        "end_id": "u003",
        "evidence_ids": ["u003"],
        "antecedent_id": "u002",
        "indicators": ["rephrase"],
        "justification": "ok",
        "level": 2,
        "agent_uptake_id": None,
    }
    base.update(kw)
    return base


# ------------------------------------------------------------------ schema


def test_schema_enums(prepared):
    _, pg = prepared
    s = build_schema(pg)
    assert s["$defs"]["unit_id"]["enum"][0] == "u001"
    assert "u001" not in s["$defs"]["student_unit_id"]["enum"]
    assert s["$defs"]["agent_unit_id"]["enum"] == ["u001", "u012"]
    props = s["properties"]["patterns"]["items"]["properties"]
    assert props["indicators"]["items"]["enum"] == list(ALL_INDICATORS)
    assert props["level"]["enum"] == [1, 2, 3, 4]
    dump = json.dumps(s)
    for kw in ("minimum", "maximum", "maxItems", "maxLength", "minLength"):
        assert kw not in dump


def test_schema_without_agents_and_fallback(chat):
    df = chat[~chat.Username.str.startswith("agente")]
    pg = FGPDAnalyzer().prepare(df).groups["g"]
    s = build_schema(pg)
    assert s["properties"]["patterns"]["items"]["properties"]["agent_uptake_id"] == {"type": "null"}
    assert "agent_unit_id" not in s["$defs"]
    big = build_schema(pg, max_enum_ids=3)
    assert big["$defs"]["unit_id"] == {"type": "string"}


# ------------------------------------------------------------------ request


def test_request_has_no_sampling_params_and_cache_breakpoints(prepared):
    fa, pg = prepared
    payload = build_payload(pg, fa.prompts, fa.config)
    req = build_request(payload, pg, fa.prompts, fa.config, fa.prompt_set_hash, "G2", 0)
    kw = req.api_kwargs()
    assert set(kw) == {"model", "max_tokens", "system", "messages", "output_config"}
    for bad in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
        assert bad not in kw and bad not in kw["output_config"]
    assert kw["output_config"]["effort"] == "medium"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    content = kw["messages"][0]["content"]
    assert [("cache_control" in b) for b in content] == [True, True, False]
    assert "cache_control" in kw["system"][0]
    assert content[1]["text"].startswith("<transcript>\n") and "Ana" not in content[1]["text"]
    assert "pattern of G2 (listen)" in content[2]["text"]
    assert kw["messages"][-1]["role"] == "user"
    # identical prefix across goals and runs; different key
    other = build_request(payload, pg, fa.prompts, fa.config, fa.prompt_set_hash, "G4", 1)
    assert other.api_kwargs()["messages"][0]["content"][:2] == content[:2]
    assert other.api_kwargs()["output_config"] == kw["output_config"]
    assert other.cache_key != req.cache_key


# ------------------------------------------------------------------ parse + postprocess


def test_parse_response_errors():
    from fgpd_analyzer.exceptions import ResponseParseError

    with pytest.raises(ResponseParseError):
        parse_response("{not json", "G1")
    with pytest.raises(ResponseParseError):
        parse_response('{"goal": "G2", "patterns": []}', "G1")
    with pytest.raises(ResponseParseError):
        parse_response('{"goal": "G1"}', "G1")
    assert parse_response('{"goal": "g1", "patterns": []}', "G1") == []


def _v(pg, goal, *items, apply=True):
    pats, qc = validate_patterns(list(items), pg, goal, 0, apply=apply)
    return pats, qc


def test_indicators_filtered_and_dropped(prepared):
    _, pg = prepared
    pats, qc = _v(
        pg,
        "G2",
        P(indicators=["REPHRASE", "warrant"]),
        P(
            start_id="u005",
            end_id="u006",
            evidence_ids=["u006"],
            antecedent_id="u005",
            indicators=["warrant"],
        ),
    )
    assert len(pats) == 1 and pats[0].indicators == ("rephrase",)
    assert qc.counts["indicator_removed"] == 2 and qc.counts["drop_no_indicators"] == 1


def test_span_and_id_rules(prepared):
    _, pg = prepared
    pats, qc = _v(
        pg,
        "G1",
        P(
            start_id="u004",
            end_id="u002",
            evidence_ids=["u002", "u003", "u009"],
            antecedent_id=None,
            indicators=["share_idea"],
        ),
    )
    p = pats[0]
    assert (p.start_id, p.end_id) == ("u002", "u004")
    assert p.evidence_ids == ("u002", "u003")
    assert {"span_swapped", "evidence_outside_span_removed"} <= set(p.qc_flags)
    pats, _ = _v(pg, "G1", P(start_id="u999", indicators=["share_idea"]))
    assert pats == []
    pats, _ = _v(pg, "G1", P(evidence_ids=["u001"], start_id="u001", indicators=["share_idea"]))
    assert pats == []  # agent unit is never evidence -> no evidence left


def test_g2_g4_need_peer_antecedent(prepared):
    _, pg = prepared
    assert _v(pg, "G2", P(antecedent_id=None))[0] == []
    # antecedent by the same speaker as all evidence (u002 and u004 are both S1)
    assert _v(pg, "G2", P(start_id="u002", end_id="u004", evidence_ids=["u004"]))[0] == []
    # agent as antecedent is nulled, then dropped
    pats, qc = _v(pg, "G4", P(antecedent_id="u001", indicators=["add_on"]))
    assert pats == [] and qc.counts["antecedent_invalid_nulled"] == 1
    # antecedent may lie before the span; never inside evidence
    pats, _ = _v(
        pg,
        "G2",
        P(start_id="u004", end_id="u005", evidence_ids=["u004", "u005"], antecedent_id="u004"),
    )
    assert pats[0].evidence_ids == ("u005",) and pats[0].antecedent_id == "u004"
    pats, _ = _v(
        pg, "G2", P(start_id="u005", end_id="u005", evidence_ids=["u005"], antecedent_id="u002")
    )
    assert pats[0].antecedent_id == "u002"  # antecedent before the span is allowed


def test_demotions(prepared):
    _, pg = prepared
    g1 = _v(
        pg, "G1", P(evidence_ids=["u002"], antecedent_id=None, level=4, indicators=["share_idea"])
    )[0][0]
    assert g1.level == 2 and "demoted_4_to_2" in g1.qc_flags
    g1b = _v(
        pg,
        "G1",
        P(
            start_id="u002",
            end_id="u004",
            evidence_ids=["u002", "u004"],
            antecedent_id=None,
            level=4,
            indicators=["share_idea"],
        ),
    )[0][0]
    assert g1b.level == 3  # two units, one speaker
    g3 = _v(
        pg,
        "G3",
        P(
            start_id="u003",
            end_id="u003",
            evidence_ids=["u003"],
            antecedent_id=None,
            level=3,
            indicators=["warrant"],
        ),
    )[0][0]
    assert g3.level == 2
    g3ok = _v(
        pg,
        "G3",
        P(
            start_id="u012",
            end_id="u013",
            evidence_ids=["u013"],
            antecedent_id=None,
            level=3,
            indicators=["answer_press"],
            agent_uptake_id="u012",
        ),
    )[0][0]
    assert g3ok.level == 3  # press inside the span before the evidence
    g4 = _v(
        pg,
        "G4",
        P(
            start_id="u003",
            end_id="u004",
            evidence_ids=["u004"],
            antecedent_id="u003",
            level=3,
            indicators=["add_on"],
        ),
    )[0][0]
    assert g4.level == 2
    g4ok = _v(
        pg,
        "G4",
        P(
            start_id="u002",
            end_id="u004",
            evidence_ids=["u003", "u004"],
            antecedent_id="u002",
            level=3,
            indicators=["add_on"],
        ),
    )[0][0]
    assert g4ok.level == 3  # S2 operates, S1 (antecedent speaker) returns


def test_agent_citation_rules(prepared):
    _, pg = prepared
    p = _v(pg, "G2", P(agent_uptake_id="u012"))[0][0]
    assert p.agent_uptake_id is None and "agent_citation_invalid_nulled" in p.qc_flags
    p = _v(pg, "G2", P(agent_uptake_id="u001"))[0][0]
    assert p.agent_uptake_id == "u001"


def test_overlap_merge_and_truncation(prepared):
    _, pg = prepared
    long = " ".join(["palavra"] * 80)
    pats, qc = _v(
        pg,
        "G2",
        P(start_id="u002", end_id="u004", evidence_ids=["u003", "u004"], level=2),
        P(
            start_id="u004",
            end_id="u006",
            evidence_ids=["u005"],
            antecedent_id="u004",
            level=3,
            justification=long,
        ),
    )
    assert len(pats) == 1
    m = pats[0]
    assert (m.start_id, m.end_id, m.level) == ("u002", "u006", 3)
    assert "u002" not in m.evidence_ids and "overlap_merged" in m.qc_flags
    assert len(m.justification.split()) == 60
    assert m.run_pattern_id == "g:G2:0:0"


def test_report_only_mode(prepared):
    _, pg = prepared
    pats, qc = _v(
        pg,
        "G2",
        P(antecedent_id=None),
        P(start_id="u003", end_id="u005", evidence_ids=["u005"], antecedent_id="u004", level=4),
        apply=False,
    )
    assert len(pats) == 2
    assert "drop_missing_antecedent" in pats[0].qc_flags
    assert pats[1].level == 4 and "overlap" in pats[1].qc_flags


# ------------------------------------------------------------------ runner


def _run(fa, pg, backend, cache=None, goals=("G1",), runs=(0,)):
    payload = build_payload(pg, fa.prompts, fa.config)
    jobs = [Job(pg, payload, g, r) for g in goals for r in runs]
    return asyncio.run(run_jobs(jobs, backend, cache, fa.config, fa.prompts, fa.prompt_set_hash))


def _resp(text, stop="end_turn"):
    return LLMResponse(text=text, stop_reason=stop, usage={"input_tokens": 1, "output_tokens": 1})


def test_runner_retries_and_statuses(prepared):
    fa, pg = prepared
    good = json.dumps(
        {
            "goal": "G1",
            "patterns": [P(evidence_ids=["u002"], antecedent_id=None, indicators=["share_idea"])],
        }
    )
    seq = iter([_resp("", "max_tokens"), _resp(good)])
    mb = MockBackend(script=lambda r: next(seq))
    (res,) = _run(fa, pg, mb)
    assert res.status == "ok" and res.attempts == 2 and len(res.patterns) == 1
    assert [c.max_tokens for c in mb.calls] == [16000, 32000]

    seq2 = iter([_resp("", "refusal"), _resp("", "refusal")])
    (res,) = _run(fa, pg, MockBackend(script=lambda r: next(seq2)))
    assert res.status == "refused" and res.attempts == 2

    seq3 = iter([_resp("{bad"), _resp('{"goal": "G1", "patterns": []}')])
    (res,) = _run(fa, pg, MockBackend(script=lambda r: next(seq3)))
    assert res.status == "ok" and res.patterns == []

    seq4 = iter([_resp("{bad"), _resp("{bad")])
    (res,) = _run(fa, pg, MockBackend(script=lambda r: next(seq4)))
    assert res.status == "failed" and res.error.startswith("parse")


def test_cache_replay_is_identical(prepared, tmp_path):
    fa, pg = prepared
    cache = ResponseCache(tmp_path)
    mb = MockBackend()
    first = _run(fa, pg, mb, cache, goals=("G1", "G2", "G3", "G4"), runs=(0, 1, 2))
    assert len(mb.calls) == 12 and len(cache) == 12
    again = _run(fa, pg, MockBackend(), cache, goals=("G1", "G2", "G3", "G4"), runs=(0, 1, 2))
    assert all(r.from_cache for r in again)
    assert [r.patterns for r in first] == [r.patterns for r in again]
    jsonl = cache.export_jsonl(tmp_path / "rec.jsonl")
    replayed = _run(
        fa, pg, ReplayBackend(jsonl), None, goals=("G1", "G2", "G3", "G4"), runs=(0, 1, 2)
    )
    assert [r.patterns for r in replayed] == [r.patterns for r in first]
    (miss,) = _run(fa, pg, ReplayBackend([]))
    assert miss.status == "failed" and "replay" in miss.error


def test_mock_varies_across_runs_but_is_deterministic(prepared):
    fa, pg = prepared
    a = _run(fa, pg, MockBackend(), goals=("G1",), runs=(0, 1, 2))
    b = _run(fa, pg, MockBackend(), goals=("G1",), runs=(0, 1, 2))
    assert [r.patterns for r in a] == [r.patterns for r in b]


def test_pricing():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    assert cost_usd("claude-haiku-5-5", usage) == pytest.approx(0.60)
    assert cost_usd("unknown", usage) is None


# ------------------------------------------------------------------ Anthropic backend over HTTP


def _api_message(text, stop="end_turn"):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 7,
            "cache_read_input_tokens": 0,
        },
    }


def _backend(responses, max_retries):
    import anthropic

    calls = []
    it = iter(responses)

    def handler(request):
        calls.append(request)
        return next(it)

    client = anthropic.AsyncAnthropic(
        api_key="sk-test",
        max_retries=max_retries,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return AnthropicBackend(client=client), calls


def _err(status, kind):
    return httpx.Response(
        status,
        headers={"retry-after": "0"},
        json={"type": "error", "error": {"type": kind, "message": "x"}},
    )


def test_anthropic_backend_http_retries(prepared):
    fa, pg = prepared
    good = json.dumps({"goal": "G1", "patterns": []})
    backend, calls = _backend(
        [
            _err(429, "rate_limit_error"),
            _err(529, "overloaded_error"),
            httpx.Response(200, json=_api_message(good, "max_tokens")),
            httpx.Response(200, json=_api_message(good)),
        ],
        max_retries=3,
    )
    (res,) = _run(fa, pg, backend)
    assert res.status == "ok" and len(calls) == 4
    sent = json.loads(calls[-1].content)
    assert "temperature" not in sent and sent["max_tokens"] == 32000
    assert sent["output_config"]["effort"] == "medium"
    assert res.usage[-1]["cache_creation_input_tokens"] == 7


def test_anthropic_backend_hard_failure(prepared):
    fa, pg = prepared
    backend, calls = _backend([_err(400, "invalid_request_error")], max_retries=0)
    (res,) = _run(fa, pg, backend)
    assert res.status == "failed" and "400" in res.error and len(calls) == 1


def test_anthropic_backend_refusal_over_http(prepared):
    fa, pg = prepared
    backend, calls = _backend(
        [httpx.Response(200, json=_api_message("", "refusal"))] * 2, max_retries=0
    )
    (res,) = _run(fa, pg, backend)
    assert res.status == "refused" and len(calls) == 2
