from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from .models import Announcement, AutonomyInput, AutonomyResult, TandeoEvent
import json
from . import collector, extractor, geo, autonomy, config

app = FastAPI(title="Tandeo Radar", version="0.1.0")
WEB = Path(__file__).resolve().parent.parent / "web"
STATE: dict[str, list[TandeoEvent]] = {"events": []}


@app.post("/refresh")
def refresh(geocode: bool = True) -> dict:
    """Collect announcements -> LLM extraction -> geocoding."""
    events: list[TandeoEvent] = []
    discarded = 0
    suspicious = 0
    retried = 0
    escalated = 0
    retried_still_empty = 0
    collector_discarded = 0
    errors = []
    if not config.NEBIUS_API_KEY:   # no LLM key yet: serve fixtures so the UI can be built
        raw = json.loads((WEB.parent / "data" / "sample_events.json").read_text(encoding="utf-8"))
        STATE["events"] = [TandeoEvent(**e) for e in raw]
        return {"events": len(STATE["events"]), "errors": [], "mode": "fixtures"}
    announcements: list[Announcement] = []
    try:
        collected = collector.collect()
    except collector.CollectorDegraded as ex:
        # Tavily delivered snippets instead of pages: an error condition, not a quiet day.
        errors.append({"stage": "collector", "error": str(ex)})
        collector_discarded = ex.discarded
    else:
        # collect() returns (announcements, discards), but older callers and test stubs
        # hand back a bare list, so accept both shapes.
        announcements, collector_discarded = (
            collected if isinstance(collected, tuple) else (collected, 0)
        )
    for a in announcements:
        try:
            result = extractor.extract(a)
            events.extend(result)
            discarded += result.discarded
            if result.suspicious:
                suspicious += 1
            if result.retried:
                retried += 1
                escalated += 1
                if not result:
                    retried_still_empty += 1
        except Exception as ex:
            errors.append({"url": a.url, "error": str(ex)})
    if geocode:
        for e in events:
            if e.colonias:
                pt = geo.geocode(e.colonias[0], e.alcaldia)
                if pt:
                    e.lat, e.lon = pt
    STATE["events"] = events
    return {
        "events": len(events),
        "collector_discarded": collector_discarded,
        "discarded": discarded,
        "suspicious": suspicious,
        "retried": retried,
        "escalated": escalated,
        "retried_still_empty": retried_still_empty,
        "errors": errors,
    }


def _filter(alcaldia: str | None, colonia: str | None) -> list[TandeoEvent]:
    ev = STATE["events"]
    if alcaldia:
        ev = [e for e in ev if e.alcaldia.lower() == alcaldia.lower()]
    if colonia:
        ev = [e for e in ev if any(colonia.lower() in c.lower() for c in e.colonias)]
    return ev


@app.get("/events")
def events(alcaldia: str | None = None, colonia: str | None = None) -> list[TandeoEvent]:
    return _filter(alcaldia, colonia)


@app.get("/events.geojson")
def events_geojson() -> dict:
    return geo.to_geojson(STATE["events"])


@app.post("/autonomy")
def get_autonomy(inp: AutonomyInput, alcaldia: str | None = Query(None),
                 colonia: str | None = Query(None)) -> AutonomyResult:
    return autonomy.compute(inp, _filter(alcaldia, colonia))


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
