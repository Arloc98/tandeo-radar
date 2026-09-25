"""Geocoding must survive a read-only filesystem.

On a serverless deployment the app directory cannot be written. The previous version
called CACHE.parent.mkdir() as its very first statement, so /refresh raised OSError
before geocoding anything -- the cache was a hard dependency instead of an optimisation.
"""
from __future__ import annotations

import pytest

from app import geo


@pytest.fixture(autouse=True)
def _cache_limpia(monkeypatch):
    monkeypatch.setattr(geo, "_MEM", {})
    monkeypatch.setattr(geo, "_LOADED", False)
    monkeypatch.setattr(geo, "_last", 0.0)


class _RespuestaFalsa:
    @staticmethod
    def json():
        return [{"lat": "19.3378", "lon": "-99.1034"}]


def test_geocoding_works_when_nothing_can_be_written(monkeypatch):
    def _sin_escritura(*a, **kw):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(geo.Path, "mkdir", _sin_escritura)
    monkeypatch.setattr(geo.Path, "write_text", _sin_escritura)
    monkeypatch.setattr(geo.httpx, "get", lambda *a, **kw: _RespuestaFalsa())

    assert geo.geocode("Del Valle", "Benito Juárez") == [19.3378, -99.1034]


def test_the_result_is_reused_from_memory_without_a_second_request(monkeypatch):
    llamadas = []

    def _contar(*a, **kw):
        llamadas.append(1)
        return _RespuestaFalsa()

    monkeypatch.setattr(geo, "_persist", lambda: None)
    monkeypatch.setattr(geo.httpx, "get", _contar)

    geo.geocode("Del Valle", "Benito Juárez")
    geo.geocode("Del Valle", "Benito Juárez")

    assert len(llamadas) == 1, "a cached colonia must not hit Nominatim twice"


def test_an_unreadable_cache_file_is_not_fatal(monkeypatch):
    monkeypatch.setattr(geo.Path, "exists", lambda self: True)
    monkeypatch.setattr(geo.Path, "read_text",
                        lambda self, **kw: "{ esto no es json")
    monkeypatch.setattr(geo, "_persist", lambda: None)
    monkeypatch.setattr(geo.httpx, "get", lambda *a, **kw: _RespuestaFalsa())

    assert geo.geocode("Del Valle", "Benito Juárez") == [19.3378, -99.1034]


def test_a_miss_is_remembered_so_it_is_not_retried(monkeypatch):
    """Nominatim returning nothing is an answer, and repeating it costs a second."""
    llamadas = []

    class _Vacia:
        @staticmethod
        def json():
            llamadas.append(1)
            return []

    monkeypatch.setattr(geo, "_persist", lambda: None)
    monkeypatch.setattr(geo.httpx, "get", lambda *a, **kw: _Vacia())

    assert geo.geocode("Colonia Inexistente", "Iztapalapa") is None
    assert geo.geocode("Colonia Inexistente", "Iztapalapa") is None
    assert len(llamadas) == 1
