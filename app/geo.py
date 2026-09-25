"""Geocode colonias with OpenStreetMap Nominatim (cached, max 1 req/s per its policy)."""
import json
import time
from pathlib import Path
import httpx

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "geocode.json"
_last = 0.0

# La cache vive en memoria y el disco es solo persistencia oportunista. En un
# despliegue serverless el filesystem es de solo lectura, y la version anterior
# hacia mkdir() como primera sentencia: reventaba antes de geocodificar nada.
# Ademas releia y reescribia el JSON entero por cada colonia.
_MEM: dict[str, list[float] | None] = {}
_LOADED = False


def _load_cache() -> None:
    """Read the cache once per process; an unreadable file is not an error."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        if CACHE.exists():
            _MEM.update(json.loads(CACHE.read_text(encoding="utf-8")))
    except Exception:
        pass


def _persist() -> None:
    """Best effort: on a read-only filesystem the in-memory cache still works."""
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_MEM, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def geocode(colonia: str, alcaldia: str):
    _load_cache()
    key = f"{colonia}|{alcaldia}"
    if key in _MEM:
        return _MEM[key]
    global _last
    time.sleep(max(0, 1.0 - (time.time() - _last)))
    try:
        r = httpx.get("https://nominatim.openstreetmap.org/search",
                      params={"q": f"{colonia}, {alcaldia}, Ciudad de México",
                              "format": "json", "limit": 1},
                      headers={"User-Agent": "tandeo-radar-hackathon/0.1"}, timeout=10)
        hits = r.json()
    except Exception:
        return None
    finally:
        _last = time.time()
    point = [float(hits[0]["lat"]), float(hits[0]["lon"])] if hits else None
    _MEM[key] = point
    _persist()
    return point


def to_geojson(events) -> dict:
    feats = []
    for e in events:
        if e.lat is None:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [e.lon, e.lat]},
            "properties": e.model_dump(mode="json", exclude={"lat", "lon"}),
        })
    return {"type": "FeatureCollection", "features": feats}
