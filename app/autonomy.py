"""Household water autonomy: A = V / Q_out, checked against the next announced cut."""
from datetime import datetime, timezone
from statistics import mean
from .models import AutonomyInput, AutonomyResult, TandeoEvent


def daily_demand(inp: AutonomyInput) -> float:
    if len(inp.history_daily_l) >= 3:
        return mean(inp.history_daily_l[-7:])   # moving average of recent days
    return inp.people * inp.daily_use_l_per_person


def compute(inp: AutonomyInput, upcoming: list[TandeoEvent] | None = None,
            now: datetime | None = None) -> AutonomyResult:
    now = now or datetime.now(timezone.utc)
    volume = inp.tank_capacity_l * inp.current_level_pct / 100
    demand = daily_demand(inp)
    days = volume / demand

    future = sorted([e for e in (upcoming or []) if e.end and e.end > now],
                    key=lambda e: e.start or now)
    nxt = future[0] if future else None
    covers = None
    if nxt:
        days_until_end = (nxt.end - now).total_seconds() / 86400
        covers = days >= days_until_end
        if covers:
            advice = "Tu reserva cubre el próximo corte."
        else:
            missing = (days_until_end - days) * demand
            advice = (f"Tu reserva se agota antes de que termine el corte. "
                      f"Faltan ~{missing:.0f} L: llena antes o reduce consumo.")
    else:
        advice = "Sin cortes anunciados. " + (
            "Nivel bajo: aprovecha para llenar." if days < 1.5 else "Reserva adecuada.")
    return AutonomyResult(volume_l=round(volume, 1), daily_demand_l=round(demand, 1),
                          autonomy_days=round(days, 2), next_cut=nxt,
                          covers_next_cut=covers, advice=advice)
