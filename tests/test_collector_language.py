"""The Tavily search must be anchored to Spanish, and its cost must be observable.

Measured on 25 Sep 2026: with no language anchor a live run returned an event for
San Pedro Garza Garcia -- Nuevo Leon, not Mexico City -- alongside traffic reports.
The collector is documented as CDMX-only, so a result in another state is a defect.

Credits are tracked because Tavily only reports them when include_usage is asked for,
and a demo that anyone can trigger needs its spend to be measurable.
"""
from __future__ import annotations

import pytest

from app import collector, config


TEXTO = ("La Secretaria de Gestion Integral del Agua informa que habra suspension del "
         "servicio en las colonias Del Valle y Narvarte, alcaldia Benito Juarez. " * 12)


class _Espia:
    """Records every kwarg the collector hands to Tavily."""

    def __init__(self, usage=None):
        self.llamadas = []
        self._usage = usage

    def search(self, query, **kwargs):
        self.llamadas.append(kwargs)
        res = {
            "results": [{
                "url": f"https://x.test/{len(self.llamadas)}",
                "title": "aviso",
                "content": "titular",
                "raw_content": TEXTO,
                "published_date": "Thu, 24 Sep 2026 00:00:00 GMT",
            }]
        }
        if self._usage is not None:
            res["usage"] = self._usage
        return res


@pytest.fixture
def en_linea(monkeypatch):
    monkeypatch.setattr(config, "OFFLINE", False)
    monkeypatch.setattr(collector.config, "OFFLINE", False)
    monkeypatch.setattr(config, "TAVILY_API_KEY", "k")
    monkeypatch.setattr(collector.config, "TAVILY_API_KEY", "k")


def test_every_search_is_anchored_to_spanish(en_linea, monkeypatch):
    espia = _Espia()
    monkeypatch.setattr(collector, "_tavily_client", lambda: espia)

    collector.collect()

    assert espia.llamadas, "the collector must reach Tavily at all"
    for kw in espia.llamadas:
        assert kw.get("language") == "es", f"missing language anchor, got {kw}"
        assert kw.get("filter_by_language") is True, f"language filter off, got {kw}"


def test_the_search_asks_for_its_own_cost(en_linea, monkeypatch):
    espia = _Espia(usage={"credits": 1})
    monkeypatch.setattr(collector, "_tavily_client", lambda: espia)

    collector.collect()

    for kw in espia.llamadas:
        assert kw.get("include_usage") is True, f"cost not requested, got {kw}"
    assert collector.LAST_USAGE["credits"] == len(espia.llamadas)
    assert collector.LAST_USAGE["calls"] == len(espia.llamadas)


def test_the_credit_count_is_per_run_not_cumulative(en_linea, monkeypatch):
    espia = _Espia(usage={"credits": 1})
    monkeypatch.setattr(collector, "_tavily_client", lambda: espia)

    collector.collect()
    primera = collector.LAST_USAGE["credits"]
    collector.collect()

    assert collector.LAST_USAGE["credits"] == primera, (
        "a second run must report its own spend, not the running total"
    )


def test_a_response_without_usage_does_not_crash(en_linea, monkeypatch):
    """Tavily may answer without the usage block; that is not a failure."""
    espia = _Espia(usage=None)
    monkeypatch.setattr(collector, "_tavily_client", lambda: espia)

    collector.collect()

    assert collector.LAST_USAGE["credits"] == 0
    assert collector.LAST_USAGE["calls"] == len(espia.llamadas)


# --- pertenencia geografica ------------------------------------------------

FUERA_DE_CDMX = ("El organismo operador de agua de San Pedro Garza Garcia informa que "
                 "suspendera el suministro en varias colonias del municipio. " * 12)


def test_a_notice_from_another_state_is_discarded(en_linea, monkeypatch):
    """language='es' covers every Spanish-speaking place, so the code anchors geography."""
    espia = _Espia()
    espia.search = lambda query, **kw: {"results": [{
        "url": "https://posta.com.mx/nuevo-leon/corte-en-san-pedro",
        "title": "corte", "content": "titular", "raw_content": FUERA_DE_CDMX,
        "published_date": "Thu, 24 Sep 2026 00:00:00 GMT",
    }]}
    monkeypatch.setattr(collector, "_tavily_client", lambda: espia)

    avisos, descartes = collector.collect()

    assert avisos == [], f"a Nuevo Leon notice must never become an announcement, got {avisos}"
    assert descartes >= 1, "the discard must be counted, not silent"


def test_a_cdmx_notice_survives_the_filter(en_linea, monkeypatch):
    espia = _Espia()
    monkeypatch.setattr(collector, "_tavily_client", lambda: espia)

    avisos, _ = collector.collect()

    assert avisos, "a Benito Juarez notice must pass the geographic filter"


@pytest.mark.parametrize("marcador", [
    "alcaldia Iztapalapa", "en la CDMX", "Ciudad de Mexico", "SACMEX informa",
])
def test_the_filter_accepts_the_usual_ways_of_naming_the_city(marcador):
    assert collector._is_cdmx("Aviso de corte. " + marcador + " habra suspension.")


@pytest.mark.parametrize("texto", [
    "Corte de agua en Monterrey, Nuevo Leon",
    "Suspension del servicio en Guadalajara, Jalisco",
    "Aviso de JAPAY en Merida, Yucatan",
])
def test_the_filter_rejects_other_cities(texto):
    assert not collector._is_cdmx(texto)
