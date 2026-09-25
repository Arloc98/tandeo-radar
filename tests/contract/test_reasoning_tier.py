"""The retry escalates to the reasoning tier.

What this closes: two model tiers were configured and nothing ever reached the second one.
extract() called complete_json without a tier, so everything ran on the fast tier while
NEBIUS_MODEL_REASONING sat configured and inert. A suspicious empty result is the right place
to escalate -- retrying with the same model that just failed buys little.
"""
from __future__ import annotations

import pytest

from app import extractor, llm
from app.models import Announcement


AVISO_CON_SENALES = (
    "Suspensión del servicio de agua en las colonias Del Valle y Narvarte, "
    "alcaldía Benito Juárez, por mantenimiento."
)
TEXTO_SIN_CORTES = "El Sistema de Aguas informó que la red opera con normalidad este mes."
EVENTO = {"alcaldia": "Benito Juárez", "colonias": ["Del Valle"], "kind": "suspension"}


class _Espia:
    """Records the tier of every call and returns each payload in turn."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.tiers: list[str] = []

    def __call__(self, system, user, **kwargs):
        self.tiers.append(kwargs.get("tier", "fast"))
        indice = min(len(self.tiers) - 1, len(self.payloads) - 1)
        resultado = self.payloads[indice]
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


def test_the_first_attempt_uses_the_fast_tier(monkeypatch):
    espia = _Espia({"events": [EVENTO]})
    monkeypatch.setattr(llm, "complete_json", espia)
    extractor.extract(Announcement(url="https://x.test/a", content=AVISO_CON_SENALES))
    assert espia.tiers == ["fast"], f"first attempt ran on {espia.tiers}"


def test_the_retry_escalates_to_the_reasoning_tier(monkeypatch):
    """Retrying on the model that just returned nothing is not a retry, it is a repeat."""
    espia = _Espia({"events": []}, {"events": [EVENTO]})
    monkeypatch.setattr(llm, "complete_json", espia)
    resultado = extractor.extract(
        Announcement(url="https://x.test/b", content=AVISO_CON_SENALES)
    )
    assert espia.tiers == ["fast", "reasoning"], f"tiers used: {espia.tiers}"
    assert len(resultado) == 1


def test_the_result_says_which_tier_found_it(monkeypatch):
    espia = _Espia({"events": []}, {"events": [EVENTO]})
    monkeypatch.setattr(llm, "complete_json", espia)
    resultado = extractor.extract(
        Announcement(url="https://x.test/c", content=AVISO_CON_SENALES)
    )
    assert getattr(resultado, "tier", None) == "reasoning", (
        "an event found by escalation must be distinguishable from a first-try one"
    )


def test_a_first_try_success_never_touches_the_expensive_tier(monkeypatch):
    espia = _Espia({"events": [EVENTO]})
    monkeypatch.setattr(llm, "complete_json", espia)
    extractor.extract(Announcement(url="https://x.test/d", content=AVISO_CON_SENALES))
    assert "reasoning" not in espia.tiers, "the normal path must not pay for Super"


def test_a_quiet_notice_never_escalates(monkeypatch):
    espia = _Espia({"events": []})
    monkeypatch.setattr(llm, "complete_json", espia)
    extractor.extract(Announcement(url="https://x.test/e", content=TEXTO_SIN_CORTES))
    assert espia.tiers == ["fast"], "a non-suspicious empty result must not escalate"


def test_a_failing_escalation_does_not_lose_the_first_result(monkeypatch):
    """A retired or unavailable reasoning model must not turn a known empty into a crash."""
    espia = _Espia({"events": []}, RuntimeError("reasoning model unavailable"))
    monkeypatch.setattr(llm, "complete_json", espia)
    resultado = extractor.extract(
        Announcement(url="https://x.test/f", content=AVISO_CON_SENALES)
    )
    assert len(resultado) == 0
    assert getattr(resultado, "suspicious", None) is True, (
        "the first attempt's suspicious empty result must survive a failed escalation"
    )


def test_refresh_reports_escalations(monkeypatch):
    from fastapi.testclient import TestClient

    from app import collector, main

    monkeypatch.setattr(main.config, "NEBIUS_API_KEY", "fake-key")
    monkeypatch.setattr(
        collector, "collect",
        lambda *a, **kw: ([Announcement(url="https://x.test/g", content=AVISO_CON_SENALES)], 0),
    )
    monkeypatch.setattr(llm, "complete_json", _Espia({"events": []}, {"events": [EVENTO]}))
    body = TestClient(main.app).post("/refresh?geocode=false").json()
    assert body.get("escalated", 0) == 1, f"/refresh must report escalations, got {body}"
