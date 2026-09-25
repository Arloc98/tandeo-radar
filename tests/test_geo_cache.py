"""The geocode cache must be UTF-8 on disk: colonia names carry accents and the
Windows default (cp1252) writes JSON that no UTF-8 consumer can read."""
import json

import pytest

from app import geo


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def cache_at(tmp_path, monkeypatch):
    """Point the module cache at a temp file and keep the test offline and fast."""
    path = tmp_path / "geocode.json"
    monkeypatch.setattr(geo, "CACHE", path)
    monkeypatch.setattr(geo.time, "sleep", lambda _s: None)
    return path


def test_reads_utf8_cache_with_accents(cache_at, monkeypatch):
    def _no_network(*a, **kw):
        raise AssertionError("cache hit must not call the network")

    monkeypatch.setattr(geo.httpx, "get", _no_network)
    cache_at.write_text(
        json.dumps({"Coyoacán Centro|Coyoacán": [19.35, -99.16]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert geo.geocode("Coyoacán Centro", "Coyoacán") == [19.35, -99.16]


def test_writes_valid_utf8(cache_at, monkeypatch):
    monkeypatch.setattr(
        geo.httpx, "get", lambda *a, **kw: _FakeResponse([{"lat": "19.35", "lon": "-99.16"}])
    )
    assert geo.geocode("San Andrés Tetepilco", "Iztapalapa") == [19.35, -99.16]

    raw = cache_at.read_bytes()
    raw.decode("utf-8")                     # fails loudly if cp1252 slipped back in
    assert "San Andrés Tetepilco" in json.loads(raw.decode("utf-8")).popitem()[0]


def test_writes_chars_outside_cp1252(cache_at, monkeypatch):
    """A stray non-Latin-1 character used to crash the write with 'charmap' codec."""
    monkeypatch.setattr(
        geo.httpx, "get", lambda *a, **kw: _FakeResponse([{"lat": "19.4", "lon": "-99.1"}])
    )
    geo.geocode("Colonia \u0915 prueba", "Cuauhtémoc")
    assert json.loads(cache_at.read_bytes().decode("utf-8"))
