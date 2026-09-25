"""Offline tests for structured extraction and loud failure handling."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app import collector, extractor, llm
from app.models import Announcement

SCHEMA = {"type": "object", "properties": {"events": {"type": "array"}}}


class _FakeClient:
    """Records every call and returns the configured response."""

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


def _response(content: str, *, reasoning: str = "", finish: str = "stop") -> SimpleNamespace:
    message = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


def test_schema_path_sends_json_schema_and_disables_thinking(monkeypatch):
    fake = _FakeClient(_response('{"events": []}'))
    monkeypatch.setattr(llm, "client", fake.as_client)

    result = llm.complete_json("system", "user", schema=SCHEMA, think=False)

    assert result == {"events": []}
    assert len(fake.calls) == 1
    sent = fake.calls[0]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_rejected_parameter_is_retried_once(monkeypatch):
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    rejection = httpx.HTTPStatusError(
        "400 Bad Request",
        request=request,
        response=httpx.Response(400, request=request, text="unsupported parameter"),
    )
    fake = _FakeClient(rejection, _response('{"events": []}'))
    monkeypatch.setattr(llm, "client", fake.as_client)

    result = llm.complete_json("system", "user", schema=SCHEMA, think=False)

    assert result == {"events": []}
    assert len(fake.calls) == 2


def test_truncated_answer_raises_truncated_answer(monkeypatch):
    fake = _FakeClient(_response('{"events": [{"alcaldia": "Tlal', finish="length"))
    monkeypatch.setattr(llm, "client", fake.as_client)

    with pytest.raises(llm.TruncatedAnswer):
        llm.complete_json("system", "user", schema=SCHEMA, think=False)


def test_lightning_reasoning_in_content_with_empty_reasoning_is_truncated(monkeypatch):
    """Lightning spills reasoning into content and leaves reasoning_content empty."""
    spilled = "Here's a thinking process:\n\n1. **Analyze User Input:** the notice says"
    fake = _FakeClient(_response(spilled, reasoning="", finish="length"))
    monkeypatch.setattr(llm, "client", fake.as_client)

    with pytest.raises(llm.TruncatedAnswer):
        llm.complete_json("system", "user", schema=SCHEMA, think=False)


def test_parse_items_counts_discarded_rows():
    rows = [{"colonias": ["sin alcaldia"]}, {"kind": "tandeo"}]
    events, discarded = extractor.parse_items(rows, "https://example.test/aviso")
    assert events == []
    assert discarded == 2


def test_extract_wraps_results_under_events(monkeypatch):
    captured: dict = {}

    def _fake_complete(system, user, **kwargs):
        captured.update(kwargs)
        return {"events": [{"alcaldia": "Tlalpan", "colonias": ["Ajusco"]}]}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    notice = Announcement(url="https://example.test/aviso", content="corte en Ajusco")
    events = extractor.extract(notice)

    assert captured.get("schema") is not None
    assert [e.alcaldia for e in events] == ["Tlalpan"]
    assert events.discarded == 0


def test_extraction_uses_temperature_zero(monkeypatch):
    """Extraction must call complete_json with temperature=0.0."""
    captured: dict = {}

    def _fake_complete(system, user, **kwargs):
        captured.update(kwargs)
        return {"events": []}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    notice = Announcement(url="https://example.test/aviso", content="corte en Ajusco")
    extractor.extract(notice)

    assert captured.get("temperature") == 0.0


def test_suspicious_empty_result_flagged(monkeypatch):
    """Empty result with interruption signals should be flagged as suspicious."""
    def _fake_complete(system, user, **kwargs):
        return {"events": []}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    # Text with alcaldia + colonia + suspension signal
    notice = Announcement(
        url="https://example.test/aviso",
        content="Suspensión del servicio en colonias Del Valle y Narvarte, alcaldía Benito Juárez"
    )
    result = extractor.extract(notice)

    assert len(result) == 0
    assert result.suspicious is True


def test_legitimate_empty_result_not_flagged(monkeypatch):
    """Empty result without interruption signals should not be flagged."""
    def _fake_complete(system, user, **kwargs):
        return {"events": []}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    # Text without interruption signals
    notice = Announcement(
        url="https://example.test/aviso",
        content="La presa opera con normalidad y no se prevén afectaciones."
    )
    result = extractor.extract(notice)

    assert len(result) == 0
    assert result.suspicious is False


def test_alcaldia_normalization(monkeypatch):
    """Alcaldia names should be normalized to official form."""
    def _fake_complete(system, user, **kwargs):
        return {"events": [{"alcaldia": "Magdalena Contreras", "colonias": ["Centro"], "kind": "suspension"}]}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    notice = Announcement(url="https://example.test/aviso", content="corte en Centro")
    result = extractor.extract(notice)

    assert len(result) == 1
    assert result[0].alcaldia == "La Magdalena Contreras"


def test_alcaldia_gam_normalization(monkeypatch):
    """GAM abbreviation should be normalized to Gustavo A. Madero."""
    def _fake_complete(system, user, **kwargs):
        return {"events": [{"alcaldia": "GAM", "colonias": ["Centro"], "kind": "suspension"}]}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    notice = Announcement(url="https://example.test/aviso", content="corte en Centro")
    result = extractor.extract(notice)

    assert len(result) == 1
    assert result[0].alcaldia == "Gustavo A. Madero"


def test_alcaldia_accent_normalization(monkeypatch):
    """Missing accents should be corrected."""
    def _fake_complete(system, user, **kwargs):
        return {"events": [{"alcaldia": "Benito Juarez", "colonias": ["Centro"], "kind": "suspension"}]}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    notice = Announcement(url="https://example.test/aviso", content="corte en Centro")
    result = extractor.extract(notice)

    assert len(result) == 1
    assert result[0].alcaldia == "Benito Juárez"


def test_alcaldia_case_normalization(monkeypatch):
    """Uppercase names should be normalized to proper case."""
    def _fake_complete(system, user, **kwargs):
        return {"events": [{"alcaldia": "CUAUHTEMOC", "colonias": ["Centro"], "kind": "suspension"}]}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    notice = Announcement(url="https://example.test/aviso", content="corte en Centro")
    result = extractor.extract(notice)

    assert len(result) == 1
    assert result[0].alcaldia == "Cuauhtémoc"


def test_unrecognized_alcaldia_kept(monkeypatch):
    """Unrecognized alcaldia names should be kept as-is, not dropped."""
    def _fake_complete(system, user, **kwargs):
        return {"events": [{"alcaldia": "Nezahualcóyotl", "colonias": ["Centro"], "kind": "suspension"}]}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    notice = Announcement(url="https://example.test/aviso", content="corte en Centro")
    result = extractor.extract(notice)

    assert len(result) == 1
    assert result[0].alcaldia == "Nezahualcóyotl"


def _make_announcement_with_signals() -> Announcement:
    return Announcement(
        url="https://example.test/aviso",
        content="Suspensión del servicio en colonias Del Valle y Narvarte, alcaldía Benito Juárez",
    )


def test_suspicious_empty_result_is_retried_once_and_can_succeed(monkeypatch):
    calls = []

    def _fake_complete(system, user, **kwargs):
        calls.append("call")
        if len(calls) == 1:
            return {"events": []}
        return {"events": [{"alcaldia": "Benito Juárez", "colonias": ["Del Valle"], "kind": "suspension"}]}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    result = extractor.extract(_make_announcement_with_signals())

    assert len(calls) == 2, "a suspicious empty result must be retried exactly once"
    assert len(result) == 1, "the retry's events must be returned"
    assert result.suspicious is False
    assert result.retried is True
    assert result.tier == "reasoning"


def test_first_attempt_uses_fast_tier(monkeypatch):
    """The first extraction attempt must run on the fast tier."""
    captured: dict = {}

    def _fake_complete(system, user, **kwargs):
        captured.update(kwargs)
        return {"events": []}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    notice = Announcement(
        url="https://example.test/aviso",
        content="El Sistema de Aguas informó que la red opera con normalidad este mes.",
    )
    result = extractor.extract(notice)

    assert captured.get("tier", "fast") == "fast"
    assert result.tier == "fast"
    assert result.retried is False


def test_retry_escalates_to_reasoning_tier(monkeypatch):
    """A suspicious empty result must be retried on the reasoning tier."""
    calls = []

    def _fake_complete(system, user, **kwargs):
        calls.append(kwargs.get("tier", "fast"))
        if len(calls) == 1:
            return {"events": []}
        return {"events": [{"alcaldia": "Benito Juárez", "colonias": ["Del Valle"], "kind": "suspension"}]}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    result = extractor.extract(_make_announcement_with_signals())

    assert calls == ["fast", "reasoning"]
    assert result.retried is True
    assert result.tier == "reasoning"


def test_failed_escalation_keeps_first_attempt_result(monkeypatch):
    """A reasoning-tier failure must not discard the first attempt's suspicious empty result."""
    calls = []

    def _fake_complete(system, user, **kwargs):
        calls.append(kwargs.get("tier", "fast"))
        if len(calls) == 1:
            return {"events": []}
        raise RuntimeError("reasoning model unavailable")

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    result = extractor.extract(_make_announcement_with_signals())

    assert calls == ["fast", "reasoning"]
    assert len(result) == 0
    assert result.suspicious is True
    assert result.retried is True
    assert result.tier == "fast"


def test_two_empty_suspicious_answers_stay_flagged(monkeypatch):
    def _fake_complete(system, user, **kwargs):
        return {"events": []}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    result = extractor.extract(_make_announcement_with_signals())

    assert len(result) == 0
    assert result.suspicious is True
    assert result.retried is True


def test_legitimate_empty_result_is_not_retried(monkeypatch):
    calls = []

    def _fake_complete(system, user, **kwargs):
        calls.append("call")
        return {"events": []}

    monkeypatch.setattr(llm, "complete_json", _fake_complete)
    notice = Announcement(
        url="https://example.test/aviso",
        content="La presa opera con normalidad y no se prevén afectaciones.",
    )
    result = extractor.extract(notice)

    assert len(calls) == 1, "a non-suspicious empty result must not be retried"
    assert len(result) == 0
    assert result.suspicious is False
    assert result.retried is False


def test_refresh_reports_retry_counts(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    calls = []

    def _fake_complete(system, user, **kwargs):
        calls.append("call")
        if len(calls) == 1:
            return {"events": []}
        return {"events": [{"alcaldia": "Benito Juárez", "colonias": ["Del Valle"], "kind": "suspension"}]}

    monkeypatch.setattr(main.config, "NEBIUS_API_KEY", "fake-key")
    monkeypatch.setattr(
        collector,
        "collect",
        lambda *a, **kw: ([_make_announcement_with_signals()], 0),
    )
    monkeypatch.setattr(llm, "complete_json", _fake_complete)

    body = TestClient(main.app).post("/refresh?geocode=false").json()

    assert body.get("retried") == 1, f"/refresh must report retried count, got {body}"
    assert body.get("retried_still_empty") == 0
