"""Collect raw service-interruption announcements (Tavily or offline fixtures)."""
import json
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import httpx
from .models import Announcement
from . import config
from .extractor import OFFICIAL_ALCALDIAS

logger = logging.getLogger(__name__)

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_announcements.json"

# A usable notice names colonias, dates and a schedule; a headline never reaches
# this size. Tavily's degraded answers (200 OK, raw_content null, snippet only)
# carried 61 to 140 chars on 24 Sep 2026, and a snippet cannot be extracted from.
MIN_CONTENT_CHARS = 1200

QUERIES = [
    "SACMEX suspensión suministro agua colonias",
    "corte de agua CDMX alcaldía colonias horario",
    "baja presión agua CDMX aviso SACMEX",
]

# Default time window for Tavily searches. Overridable via SEARCH_TIME_RANGE env.
SEARCH_TIME_RANGE = config.SEARCH_TIME_RANGE

_WINDOW_DAYS = {
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}


class BudgetExhausted(Exception):
    """The demo budget is spent. Not an error: a deliberate stop before spending more."""

    def __init__(self, used: int, ceiling: int):
        self.used, self.ceiling = used, ceiling
        super().__init__(f"Tavily usage {used} reached the demo ceiling {ceiling}")


def plan_usage() -> int:
    """Credits consumed this month, from Tavily's own meter.

    The endpoint is undocumented but stable, and reading it costs no credits. Raises
    on failure so the caller can refuse to spend rather than guess.
    """
    r = httpx.get(
        "https://api.tavily.com/usage",
        headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
        timeout=10,
    )
    r.raise_for_status()
    return int(r.json()["account"]["plan_usage"])


def check_budget() -> None:
    """Stop before a public demo drains the plan. A ceiling of 0 disables the guard."""
    ceiling = config.TAVILY_USAGE_CEILING
    if ceiling <= 0:
        return
    try:
        used = plan_usage()
    except Exception as ex:
        # Si el medidor no responde, no se gasta: en una demo publica fallar
        # abierto es justo el modo de fallo que el techo existe para evitar.
        raise BudgetExhausted(-1, ceiling) from ex
    if used >= ceiling:
        raise BudgetExhausted(used, ceiling)


# Tavily no ancla geografia: `language="es"` cubre todo el espanol, asi que una
# busqueda de cortes devuelve Nuevo Leon, Yucatan y Chiapas. Medido el 25 sep 2026,
# dos corridas, 2 de 8 resultados fuera de la CDMX. El filtro lo hace el codigo.
_CDMX_MARKERS = tuple(OFFICIAL_ALCALDIAS.keys()) + (
    "cdmx", "ciudad de mexico", "ciudad de méxico", "sacmex", "segiagua",
    "valle de méxico", "valle de mexico",
)


def _is_cdmx(text: str) -> bool:
    """True when the text names the city, its water utility or one of its alcaldias."""
    low = text.lower()
    return any(marker in low for marker in _CDMX_MARKERS)


# Creditos consumidos por la ultima llamada a collect(). Tavily los devuelve por
# peticion cuando se pide include_usage; sin esto el gasto es invisible.
LAST_USAGE = {"credits": 0, "calls": 0}


def _record_usage(res: dict) -> None:
    usage = res.get("usage") or {}
    credits = usage.get("credits")
    LAST_USAGE["calls"] += 1
    if isinstance(credits, (int, float)):
        LAST_USAGE["credits"] += credits


def _parse_published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


class CollectorDegraded(Exception):
    """Tavily answered with snippets instead of pages and nothing usable survived.

    Raised when at least one result came back without raw_content and no
    announcement passed the minimum-length filter, so a total degradation is
    reported as an error instead of a quiet day with zero notices.
    """

    def __init__(self, degraded: int, discarded: int, urls: list[str]):
        self.degraded = degraded
        self.discarded = discarded
        self.urls = urls
        super().__init__(
            f"Tavily returned {degraded} degraded result(s) without raw_content and "
            f"no usable announcement was collected (discarded: {discarded}): {urls}"
        )


def _tavily_client():
    """Build the Tavily client lazily, so offline runs never import it."""
    from tavily import TavilyClient
    return TavilyClient(api_key=config.TAVILY_API_KEY)


def collect(max_results: int = 5, time_range: str | None = None) -> tuple[list[Announcement], int]:
    """Search Tavily and keep only the results whose text is usable and recent.

    Returns the usable announcements and how many results were discarded for
    being shorter than MIN_CONTENT_CHARS or older than the configured window. A
    headline cannot be extracted from, so it is dropped instead of being passed
    off as a notice. A result that arrives with `content` but no `raw_content`
    is logged as degraded with its URL. When degradation cost every usable
    candidate, CollectorDegraded is raised so the caller can tell a degraded
    Tavily from a day with no notices.

    Offline mode (or a missing key) serves the synthetic fixtures and never
    touches the network.
    """
    if config.OFFLINE or not config.TAVILY_API_KEY:
        return [Announcement(**a) for a in json.loads(SAMPLE.read_text(encoding="utf-8"))], 0

    window = time_range or SEARCH_TIME_RANGE
    cutoff = datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS.get(window, 7))

    tv = _tavily_client()
    LAST_USAGE["credits"] = 0      # el gasto se cuenta por corrida, no acumulado
    LAST_USAGE["calls"] = 0
    seen, out = set(), []
    discarded = 0
    undated = 0
    degraded_urls: list[str] = []
    for q in QUERIES:
        res = tv.search(
            q,
            topic="news",
            max_results=max_results,
            include_raw_content=True,
            time_range=window,
            # Sin anclaje de idioma, buscar cortes en una alcaldia devolvia notas de
            # Nuevo Leon, Texas y vuelos en Australia. Medido el 25 sep 2026.
            language="es",
            filter_by_language=True,
            # Devuelve {"credits": N} por llamada: es la unica forma de saber el gasto.
            include_usage=True,
        )
        _record_usage(res)
        for r in res.get("results", []):
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            raw = r.get("raw_content")
            if not raw:
                degraded_urls.append(r["url"])
                logger.warning(
                    "degraded Tavily result, raw_content missing (snippet only): %s",
                    r["url"],
                )
            content = (raw or r.get("content") or "")[:8000]
            if len(content) < MIN_CONTENT_CHARS:
                discarded += 1
                logger.info(
                    "discarded result of %d chars, below MIN_CONTENT_CHARS=%d: %s",
                    len(content), MIN_CONTENT_CHARS, r["url"],
                )
                continue
            if not _is_cdmx(content):
                discarded += 1
                logger.info("discarded result outside Mexico City: %s", r["url"])
                continue
            published = r.get("published_date")
            parsed = _parse_published(published)
            if parsed is not None and parsed < cutoff:
                discarded += 1
                logger.info(
                    "discarded result older than window=%s: %s (%s)",
                    window,
                    r["url"],
                    published,
                )
                continue
            if parsed is None:
                undated += 1
                logger.info("kept undated result: %s", r["url"])
            out.append(Announcement(
                url=r["url"],
                title=r.get("title", ""),
                content=content,
                published=published,
            ))
    logger.info(
        "collector kept %d notices (undated %d), discarded %d within window %s",
        len(out),
        undated,
        discarded,
        window,
    )
    if degraded_urls and not out:
        raise CollectorDegraded(
            degraded=len(degraded_urls), discarded=discarded, urls=degraded_urls
        )
    return out, discarded
