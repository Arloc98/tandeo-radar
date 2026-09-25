from datetime import datetime, timezone, timedelta
from app.autonomy import compute
from app.models import AutonomyInput, TandeoEvent
from app.llm import extract_json
from app.extractor import parse_items

NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def test_autonomy_basic():
    r = compute(AutonomyInput(tank_capacity_l=1100, current_level_pct=50, people=4), now=NOW)
    assert r.volume_l == 550 and r.daily_demand_l == 400
    assert abs(r.autonomy_days - 1.38) < 0.01


def test_history_overrides_default():
    r = compute(AutonomyInput(tank_capacity_l=1000, current_level_pct=100,
                              history_daily_l=[200, 250, 300]), now=NOW)
    assert r.daily_demand_l == 250


def test_cut_not_covered():
    cut = TandeoEvent(alcaldia="Coyoacán", source_url="x",
                      start=NOW + timedelta(hours=6), end=NOW + timedelta(days=3))
    r = compute(AutonomyInput(tank_capacity_l=1100, current_level_pct=50), [cut], now=NOW)
    assert r.covers_next_cut is False and "Faltan" in r.advice


def test_json_after_reasoning():
    text = "Let me think... the answer is:\n```json\n[{\"alcaldia\": \"Tlalpan\"}]\n```"
    assert extract_json(text) == [{"alcaldia": "Tlalpan"}]


def test_parse_skips_bad_rows():
    ev, discarded = parse_items(
        [{"alcaldia": "Tlalpan"}, {"colonias": ["sin alcaldía"]}], "u"
    )
    assert len(ev) == 1
    assert discarded == 1


def test_extraction_result_suspicious_field():
    """ExtractionResult should support the suspicious field."""
    from app.extractor import ExtractionResult
    result = ExtractionResult([], 0, suspicious=True)
    assert result.suspicious is True
    result2 = ExtractionResult([], 0, suspicious=False)
    assert result2.suspicious is False
