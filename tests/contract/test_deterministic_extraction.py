"""Deterministic extraction, suspicious emptiness, official alcaldia names.

The failure this closes: with temperature 0.1 the extractor returned zero events for
data/eval/006.txt between 25% and 83% of the time, for a notice announcing tandeo in four
alcaldias. A discard count does not catch it, because nothing is malformed -- the model
returns a valid empty list. Somebody asking whether their water is cut off gets told no.

Tested through the observable surface: what reaches the API, what extract() returns, and what
/refresh reports.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import extractor, llm
from app.models import Announcement


AVISO_CON_SENALES = (
    "La Secretaría de Gestión Integral del Agua informa que habrá suspensión del servicio "
    "en las colonias Del Valle y Narvarte, alcaldía Benito Juárez, por mantenimiento."
)
TEXTO_SIN_CORTES = (
    "El Sistema de Aguas informó que la presa opera con normalidad y no se prevén "
    "afectaciones. Se recuerda a la población el calendario de pagos del bimestre."
)


class _Recorder:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        import json

        message = SimpleNamespace(content=json.dumps(self.payload), reasoning_content="")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")]
        )

    def as_client(self):
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self._create))
        )


def test_extraction_sends_temperature_zero(monkeypatch):
    """0.1 is what made the extractor unreliable; the value must reach the API as 0."""
    rec = _Recorder({"events": []})
    monkeypatch.setattr(llm, "client", rec.as_client)
    extractor.extract(Announcement(url="https://x.test/a", content=TEXTO_SIN_CORTES))
    assert rec.calls, "no request was made"
    assert rec.calls[0]["temperature"] == 0, (
        f"extraction ran at temperature {rec.calls[0]['temperature']}, expected 0"
    )


def test_an_empty_result_on_a_notice_full_of_signals_is_suspicious(monkeypatch):
    rec = _Recorder({"events": []})
    monkeypatch.setattr(llm, "client", rec.as_client)
    result = extractor.extract(Announcement(url="https://x.test/a", content=AVISO_CON_SENALES))

    assert len(result) == 0
    assert getattr(result, "suspicious", None) is True, (
        "zero events from a text naming an alcaldia, colonias and a suspension must be "
        "flagged, not reported as 'no cuts announced'"
    )


def test_a_genuinely_quiet_notice_is_not_suspicious(monkeypatch):
    rec = _Recorder({"events": []})
    monkeypatch.setattr(llm, "client", rec.as_client)
    result = extractor.extract(Announcement(url="https://x.test/b", content=TEXTO_SIN_CORTES))
    assert getattr(result, "suspicious", None) is False, (
        "a text with no interruption signals must not be flagged, or the flag means nothing"
    )


def test_refresh_reports_suspicious_emptiness(monkeypatch):
    from fastapi.testclient import TestClient

    from app import collector, main

    monkeypatch.setattr(main.config, "NEBIUS_API_KEY", "fake-key")
    monkeypatch.setattr(
        collector, "collect",
        lambda *a, **kw: [Announcement(url="https://x.test/a", content=AVISO_CON_SENALES)],
    )
    rec = _Recorder({"events": []})
    monkeypatch.setattr(llm, "client", rec.as_client)

    body = TestClient(main.app).post("/refresh?geocode=false").json()
    assert body.get("events") == 0
    assert body.get("suspicious", 0) == 1, (
        f"/refresh must surface suspicious emptiness, got {body}"
    )


@pytest.mark.parametrize(
    "crudo,oficial",
    [
        ("Magdalena Contreras", "La Magdalena Contreras"),
        ("GAM", "Gustavo A. Madero"),
        ("Benito Juarez", "Benito Juárez"),
        ("CUAUHTEMOC", "Cuauhtémoc"),
        ("alvaro obregon", "Álvaro Obregón"),
    ],
)
def test_alcaldia_is_normalised_to_the_official_name(monkeypatch, crudo, oficial):
    """Correct extraction scored as error: 003 lost alcaldia entirely over a missing article."""
    rec = _Recorder({"events": [{"alcaldia": crudo, "colonias": ["Centro"],
                                 "kind": "suspension"}]})
    monkeypatch.setattr(llm, "client", rec.as_client)
    result = extractor.extract(Announcement(url="https://x.test/c", content=AVISO_CON_SENALES))
    assert [e.alcaldia for e in result] == [oficial]


def test_an_unrecognised_alcaldia_is_kept_not_dropped(monkeypatch):
    """Silently discarding an unknown name would hide a real event."""
    rec = _Recorder({"events": [{"alcaldia": "Nezahualcóyotl", "colonias": ["Centro"],
                                 "kind": "suspension"}]})
    monkeypatch.setattr(llm, "client", rec.as_client)
    result = extractor.extract(Announcement(url="https://x.test/d", content=AVISO_CON_SENALES))
    assert len(result) == 1 and result[0].alcaldia == "Nezahualcóyotl"
