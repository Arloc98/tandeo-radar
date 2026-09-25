"""Geocode colonias with OpenStreetMap Nominatim (cached, max 1 req/s per its policy)."""
import json
import time
from pathlib import Path
import httpx

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "geocode.json"
_last = 0.0


def geocode(colonia: str, alcaldia: str):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    key = f"{colonia}|{alcaldia}"
    if key in cache:
        return cache[key]
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
    cache[key] = point
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
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
