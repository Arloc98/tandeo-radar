"""Structured extraction, thinking off, and loud failures.

The failure this closes: Lightning leaves `reasoning_content` empty and spills its reasoning
into `content`, so `extract_json` raises, `parse_items` swallows it, and the app reports zero
events. For a service that tells people when their water is cut off, "no cuts announced" and
"the model call broke" must never look the same.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import extractor, llm
from app.models import Announcement


def _response(content: str, *, reasoning: str = "", finish: str = "stop") -> SimpleNamespace:
    message = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


class _Recorder:
    """Stands in for the OpenAI client and remembers how it was called."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def as_client(self):
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self._create))
        )


SCHEMA = {"type": "object", "properties": {"events": {"type": "array"}}}


def test_extraction_disables_thinking_and_sends_the_schema(monkeypatch):
    rec = _Recorder(_response('{"events": []}'))
    monkeypatch.setattr(llm, "client", rec.as_client)
    llm.complete_json("system", "user", schema=SCHEMA, think=False)

    sent = rec.calls[0]
    assert sent["response_format"]["type"] == "json_schema"
    thinking = sent["extra_body"]["chat_template_kwargs"]["enable_thinking"]
    assert thinking is False


def test_a_truncated_answer_raises_instead_of_being_parsed(monkeypatch):
    """finish_reason 'length' means the answer is cut off; parsing it invents data.

    A dedicated exception, not any Exception: an earlier draft of this contract passed
    on TypeError because complete_json did not accept `schema` yet -- green for the
    wrong reason.
    """
    rec = _Recorder(_response('{"events": [{"alcaldia": "Tlal', finish="length"))
    monkeypatch.setattr(llm, "client", rec.as_client)
    with pytest.raises(llm.TruncatedAnswer):
        llm.complete_json("system", "user", schema=SCHEMA, think=False)


def test_a_rejected_parameter_is_retried_once_without_it(monkeypatch):
    """Token Factory forwards chat_template_kwargs today, but that is not a guarantee.

    Raises what the SDK actually raises. An earlier version of this test used
    httpx.HTTPStatusError, which openai never raises -- BadRequestError descends from
    APIStatusError, not from httpx -- so a retry that only recognised the httpx type would
    have passed the contract and never fired against the real API.
    """
    import httpx
    import openai

    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(400, request=request, text="unsupported parameter")
    rejection = openai.BadRequestError("400", response=response, body=None)

    rec = _Recorder(rejection, _response('{"events": []}'))
    monkeypatch.setattr(llm, "client", rec.as_client)

    llm.complete_json("system", "user", schema=SCHEMA, think=False)
    assert len(rec.calls) == 2, "expected exactly one retry after the 400"
    assert "response_format" not in rec.calls[1], "the retry must drop the rejected parameter"


def test_lightning_reasoning_in_content_is_an_error_not_an_empty_result(monkeypatch):
    """The measured Lightning failure: reasoning in content, reasoning_content empty."""
    spilled = "Here's a thinking process:\n\n1. **Analyze User Input:** the notice says"
    rec = _Recorder(_response(spilled, reasoning="", finish="length"))
    monkeypatch.setattr(llm, "client", rec.as_client)
    with pytest.raises(llm.TruncatedAnswer):
        llm.complete_json("system", "user", schema=SCHEMA, think=False)


def test_discarded_rows_are_counted_not_swallowed():
    """All rows failing must be distinguishable from a notice with no interruptions."""
    rows = [{"colonias": ["sin alcaldia"]}, {"kind": "tandeo"}]
    events, discarded = extractor.parse_items(rows, "https://example.test/aviso")
    assert events == []
    assert discarded == 2, "a caller must be able to tell 2 bad rows from 0 events"


def test_a_notice_with_no_interruption_reports_zero_discards():
    events, discarded = extractor.parse_items([], "https://example.test/aviso")
    assert events == [] and discarded == 0


def test_a_total_parse_failure_is_visible_to_the_caller(monkeypatch):
    """The point of the whole task, checked where it actually matters.

    An earlier version of this contract only exercised parse_items directly, which it
    satisfied while extract() still returned a bare [] and /refresh still answered
    {"events": 0, "errors": []} for both a broken model call and a notice with no
    interruptions. Test the caller, not just the helper.
    """
    all_bad = {"events": [{"colonias": ["Del Valle"]}, {"kind": "tandeo"}]}
    monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: all_bad)
    broken = extractor.extract(Announcement(url="https://x.test/roto", content="corte"))

    monkeypatch.setattr(llm, "complete_json", lambda *a, **kw: {"events": []})
    quiet = extractor.extract(Announcement(url="https://x.test/sin-cortes", content="nada"))

    assert broken != quiet, (
        "two discarded rows must not look identical to a notice with no interruptions"
    )


def test_refresh_reports_discards_over_the_api(monkeypatch):
    """Whoever calls POST /refresh has to be able to tell a failure from a quiet day."""
    from fastapi.testclient import TestClient

    from app import collector, config, main

    monkeypatch.setattr(config, "NEBIUS_API_KEY", "fake-key")
    monkeypatch.setattr(main.config, "NEBIUS_API_KEY", "fake-key")
    monkeypatch.setattr(
        collector, "collect",
        lambda *a, **kw: [Announcement(url="https://x.test/aviso", content="corte")],
    )
    monkeypatch.setattr(
        llm, "complete_json",
        lambda *a, **kw: {"events": [{"colonias": ["sin alcaldia"]}, {"kind": "tandeo"}]},
    )

    body = TestClient(main.app).post("/refresh?geocode=false").json()
    assert body.get("events") == 0
    assert body.get("discarded") == 2, (
        f"/refresh must surface discarded rows, got {body}"
    )


def test_extractor_wraps_results_under_events(monkeypatch):
    captured: dict = {}

    def _complete(system, user, **kwargs):
        captured.update(kwargs)
        return {"events": [{"alcaldia": "Tlalpan", "colonias": ["Ajusco"]}]}

    monkeypatch.setattr(llm, "complete_json", _complete)
    notice = Announcement(url="https://example.test/aviso", content="corte en Ajusco")
    events = extractor.extract(notice)

    assert captured.get("schema") is not None, "the extractor must pass a schema"
    assert [e.alcaldia for e in events] == ["Tlalpan"]
