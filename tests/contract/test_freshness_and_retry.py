"""Recent notices, and a retry when the model goes quiet.

Two measured failures. The collector passed no time_range, so a live run returned notices
published in January 2026, December 2025 and November 2025. And a suspicious empty extraction
was visible but never retried, although retrying once took one sample notice from 4 of 8 empty
to 2 of 8.

Tested through what the caller sees, never a helper.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import collector, config, extractor, llm
from app.models import Announcement

TEXTO_LARGO = (
    "La Secretaría de Gestión Integral del Agua informa que habrá suspensión del servicio en "
    "las colonias Del Valle y Narvarte, alcaldía Benito Juárez, del 25 al 26 de septiembre. "
    * 12
)
AVISO_CON_SENALES = (
    "Suspensión del servicio de agua en las colonias Del Valle y Narvarte, "
    "alcaldía Benito Juárez."
)
TEXTO_SIN_CORTES = "El Sistema de Aguas informó que la red opera con normalidad este mes."


class _TavilyStub:
    def __init__(self, resultados):
        self.resultados = resultados
        self.llamadas: list[dict] = []

    def search(self, query, **kwargs):
        self.llamadas.append(dict(kwargs, query=query))
        return {"results": list(self.resultados)}


def _resultado(url, published=None, raw=TEXTO_LARGO):
    return {"url": url, "title": "aviso", "content": "snippet",
            "raw_content": raw, "published_date": published}


@pytest.fixture
def en_linea(monkeypatch):
    def _montar(resultados):
        stub = _TavilyStub(resultados)
        monkeypatch.setattr(collector.config, "OFFLINE", False)
        monkeypatch.setattr(collector.config, "TAVILY_API_KEY", "fake-key")
        monkeypatch.setattr(collector, "_tavily_client", lambda: stub, raising=False)
        monkeypatch.setattr(collector, "TavilyClient", lambda api_key: stub, raising=False)
        return stub
    return _montar


# --- frescura -------------------------------------------------------------------------

def test_the_time_window_is_sent_to_tavily(en_linea):
    stub = en_linea([_resultado("https://x.test/1", "Thu, 24 Sep 2026 00:00:00 GMT")])
    collector.collect()
    assert stub.llamadas, "no search was issued"
    assert stub.llamadas[0].get("time_range") == collector.SEARCH_TIME_RANGE


def test_the_window_defaults_to_a_week():
    assert collector.SEARCH_TIME_RANGE == "week"


def test_a_notice_older_than_the_window_is_not_returned(en_linea):
    """A cut announced in December 2025 is not news in September 2026."""
    en_linea([
        _resultado("https://x.test/viejo", "Mon, 22 Dec 2025 00:00:00 GMT"),
        _resultado("https://x.test/fresco", "Thu, 24 Sep 2026 00:00:00 GMT"),
    ])
    avisos, _ = collector.collect()
    assert [a.url for a in avisos] == ["https://x.test/fresco"]


def test_a_notice_without_a_date_is_kept(en_linea):
    """Dropping undated notices would lose the official sources, which rarely carry one."""
    en_linea([_resultado("https://x.test/sinfecha", None)])
    avisos, _ = collector.collect()
    assert [a.url for a in avisos] == ["https://x.test/sinfecha"]


# --- reintento ------------------------------------------------------------------------

class _Modelo:
    """Returns each payload in turn, so a retry can differ from the first answer."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.llamadas = 0

    def __call__(self, *a, **kw):
        self.llamadas += 1
        return self.payloads[min(self.llamadas - 1, len(self.payloads) - 1)]


EVENTO = {"alcaldia": "Benito Juárez", "colonias": ["Del Valle"], "kind": "suspension"}


def test_a_suspicious_empty_result_is_retried_and_can_succeed(monkeypatch):
    modelo = _Modelo({"events": []}, {"events": [EVENTO]})
    monkeypatch.setattr(llm, "complete_json", modelo)
    resultado = extractor.extract(Announcement(url="https://x.test/a", content=AVISO_CON_SENALES))

    assert modelo.llamadas == 2, "a suspicious empty result must be retried exactly once"
    assert len(resultado) == 1, "the retry's events must be the ones returned"
    assert getattr(resultado, "suspicious", None) is False


def test_two_empty_answers_stay_flagged(monkeypatch):
    modelo = _Modelo({"events": []}, {"events": []})
    monkeypatch.setattr(llm, "complete_json", modelo)
    resultado = extractor.extract(Announcement(url="https://x.test/b", content=AVISO_CON_SENALES))

    assert modelo.llamadas == 2, "exactly one retry, not an unbounded loop"
    assert len(resultado) == 0
    assert getattr(resultado, "suspicious", None) is True


def test_a_legitimately_quiet_notice_is_not_retried(monkeypatch):
    """Paying twice for a quiet day is the cost this guard exists to avoid."""
    modelo = _Modelo({"events": []})
    monkeypatch.setattr(llm, "complete_json", modelo)
    resultado = extractor.extract(Announcement(url="https://x.test/c", content=TEXTO_SIN_CORTES))

    assert modelo.llamadas == 1, "a non-suspicious empty result must not be retried"
    assert getattr(resultado, "suspicious", None) is False


def test_the_retry_is_counted_for_the_caller(monkeypatch):
    modelo = _Modelo({"events": []}, {"events": [EVENTO]})
    monkeypatch.setattr(llm, "complete_json", modelo)
    resultado = extractor.extract(Announcement(url="https://x.test/d", content=AVISO_CON_SENALES))
    assert getattr(resultado, "retried", None) is True, (
        "the caller must be able to tell a first-try answer from a retried one"
    )


def test_refresh_reports_retries(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(main.config, "NEBIUS_API_KEY", "fake-key")
    monkeypatch.setattr(
        collector, "collect",
        lambda *a, **kw: ([Announcement(url="https://x.test/e", content=AVISO_CON_SENALES)], 0),
    )
    monkeypatch.setattr(llm, "complete_json", _Modelo({"events": []}, {"events": [EVENTO]}))

    body = TestClient(main.app).post("/refresh?geocode=false").json()
    assert body.get("retried", 0) == 1, f"/refresh must report retries, got {body}"
