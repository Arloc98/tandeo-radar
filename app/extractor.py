"""Turn unstructured announcements into TandeoEvent records with the LLM."""
import logging

from . import llm
from .models import Announcement, TandeoEvent


class ExtractionResult(list[TandeoEvent]):
    """List of extracted events that also carries the number of discarded rows."""

    def __init__(
        self,
        events: list[TandeoEvent],
        discarded: int,
        suspicious: bool = False,
        retried: bool = False,
        tier: str = "fast",
    ):
        super().__init__(events)
        self.discarded = discarded
        self.suspicious = suspicious
        self.retried = retried
        self.tier = tier

    def __eq__(self, other):
        if not isinstance(other, ExtractionResult):
            return NotImplemented
        return (
            list(self) == list(other)
            and self.discarded == other.discarded
            and self.suspicious == other.suspicious
            and self.retried == other.retried
            and self.tier == other.tier
        )

    def __ne__(self, other):
        eq = self.__eq__(other)
        if eq is NotImplemented:
            return NotImplemented
        return not eq

    def __repr__(self):
        return (
            f"ExtractionResult(events={list(self)!r}, discarded={self.discarded}, "
            f"suspicious={self.suspicious}, retried={self.retried}, tier={self.tier!r})"
        )


# Official alcaldia names for normalization
OFFICIAL_ALCALDIAS = {
    "álvaro obregón": "Álvaro Obregón",
    "alvaro obregon": "Álvaro Obregón",
    "azcapotzalco": "Azcapotzalco",
    "benito juarez": "Benito Juárez",
    "benito juárez": "Benito Juárez",
    "coyoacán": "Coyoacán",
    "coyoacan": "Coyoacán",
    "cuajimalpa de morelos": "Cuajimalpa de Morelos",
    "cuajimalpa": "Cuajimalpa de Morelos",
    "cuauhtémoc": "Cuauhtémoc",
    "cuauhtemoc": "Cuauhtémoc",
    "gam": "Gustavo A. Madero",
    "gustavo a. madero": "Gustavo A. Madero",
    "gustavo a madero": "Gustavo A. Madero",
    "iztapalapa": "Iztapalapa",
    "la magdalena contreras": "La Magdalena Contreras",
    "magdalena contreras": "La Magdalena Contreras",
    "milpa alta": "Milpa Alta",
    "miguel hidalgo": "Miguel Hidalgo",
    "tlahuac": "Tláhuac",
    "tlalhuac": "Tláhuac",
    "tlalpan": "Tlalpan",
    "venustiano carranza": "Venustiano Carranza",
    "xochimilco": "Xochimilco",
}


def normalize_alcaldia(name: str) -> str:
    """Normalize alcaldia name to official form."""
    if not name:
        return name
    key = name.strip().lower()
    return OFFICIAL_ALCALDIAS.get(key, name)


SUSPICIOUS_SIGNALS = [
    "corte",
    "suspensión",
    "suspension",
    "tandeo",
    "baja presión",
    "baja presion",
    "afectación",
    "afectacion",
    "afectaciones",
    "desabasto",
    "interrupción",
    "interrupcion",
    "reducción",
    "reduccion",
    "falta de agua",
    "sin agua",
    "corte de agua",
    "suministro",
]


def has_suspicious_signals(text: str) -> bool:
    """Check if text contains signals of water interruption."""
    if not text:
        return False
    text_lower = text.lower()
    # Check for alcaldia name + "colonia" or any suspicious signal
    has_alcaldia = any(alc in text_lower for alc in OFFICIAL_ALCALDIAS.keys())
    has_colonia = "colonia" in text_lower or "colonias" in text_lower
    
    # Check for signals, but exclude negative contexts
    has_signal = False
    for signal in SUSPICIOUS_SIGNALS:
        if signal in text_lower:
            # Check if signal appears in a negative context
            idx = text_lower.index(signal)
            # Look at surrounding context (50 chars before)
            context_start = max(0, idx - 50)
            context = text_lower[context_start:idx]
            # Negative indicators
            negative_indicators = ["no ", "sin ", "ningún", "ningun", "no se", "no hay", "no habrá", "no habra", "no prev", "sin prev", "normalidad", "operan con normalidad"]
            is_negative = any(neg in context for neg in negative_indicators)
            if not is_negative:
                has_signal = True
                break
    
    return (has_alcaldia and has_colonia) or has_signal


logger = logging.getLogger(__name__)

SYSTEM = """You extract water service interruptions in Mexico City from Spanish text.
Return ONLY a JSON object with an "events" array. Each event item:
{"alcaldia": str, "colonias": [str], "start": ISO-8601 or null, "end": ISO-8601 or null,
 "kind": "suspension"|"tandeo"|"baja_presion", "reason": str or null, "confidence": 0..1}
Use the America/Mexico_City offset (-06:00). If a date or time is not stated, use null.
Never invent colonias. If the text has no interruption, return {"events": []}."""

EVENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "alcaldia": {"type": "string"},
                    "colonias": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "start": {"type": ["string", "null"]},
                    "end": {"type": ["string", "null"]},
                    "kind": {
                        "type": "string",
                        "enum": ["suspension", "tandeo", "baja_presion"],
                    },
                    "reason": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                },
                "required": ["alcaldia", "colonias", "kind"],
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}


def parse_items(items, source_url: str) -> tuple[list[TandeoEvent], int]:
    if isinstance(items, dict):
        items = [items]
    events: list[TandeoEvent] = []
    discarded = 0
    for it in items or []:
        try:
            # Normalize alcaldia name before creating event
            if "alcaldia" in it:
                it["alcaldia"] = normalize_alcaldia(it["alcaldia"])
            events.append(TandeoEvent(source_url=source_url, **it))
        except Exception as exc:  # noqa: BLE001
            discarded += 1
            logger.warning(
                "Discarded malformed row from %s: %s (reason: %s)",
                source_url,
                it,
                exc,
            )
    return events, discarded


def _extract_once(a: Announcement, year_hint: int, tier: str | None = None) -> ExtractionResult:
    user = f"Year context: {year_hint}\nPublished: {a.published}\nTitle: {a.title}\n\n{a.content}"
    kwargs: dict[str, object] = {"think": False, "temperature": 0.0}
    if tier is not None:
        kwargs["tier"] = tier
    raw = llm.complete_json(SYSTEM, user, schema=EVENTS_SCHEMA, **kwargs)
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("events", [])
    else:
        items = []
    events, discarded = parse_items(items, a.url)
    if discarded:
        logger.warning("%s rows discarded from %s", discarded, a.url)

    # Check for suspicious emptiness
    full_text = f"{a.title}\n{a.content}"
    suspicious = len(events) == 0 and has_suspicious_signals(full_text)
    if suspicious:
        logger.warning(
            "Suspicious empty extraction from %s: text has interruption signals but model returned no events",
            a.url,
        )

    return ExtractionResult(events, discarded, suspicious, tier=tier or "fast")


def extract(a: Announcement, year_hint: int = 2026) -> ExtractionResult:
    first = _extract_once(a, year_hint)
    if not first.suspicious:
        first.retried = False
        return first

    # One escalation to the reasoning tier for a suspicious empty result.
    try:
        retry = _extract_once(a, year_hint, tier="reasoning")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Escalation to reasoning tier failed for %s: %s. Keeping first-attempt result.",
            a.url,
            exc,
        )
        first.retried = True
        return first

    if retry:
        retry.retried = True
        retry.suspicious = False
        return retry

    retry.retried = True
    return retry
