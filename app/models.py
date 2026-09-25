from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class Announcement(BaseModel):
    """Raw text collected from an official or press source."""
    url: str
    title: str = ""
    content: str
    published: Optional[str] = None


class TandeoEvent(BaseModel):
    """One structured service interruption / tandeo window."""
    alcaldia: str
    colonias: list[str] = Field(default_factory=list)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    kind: str = "suspension"          # suspension | tandeo | baja_presion
    reason: Optional[str] = None
    source_url: str
    confidence: float = 0.5
    lat: Optional[float] = None
    lon: Optional[float] = None


class AutonomyInput(BaseModel):
    tank_capacity_l: float = Field(gt=0)
    current_level_pct: float = Field(ge=0, le=100)
    people: int = Field(ge=1, default=4)
    daily_use_l_per_person: float = Field(gt=0, default=100)
    history_daily_l: list[float] = Field(default_factory=list)


class AutonomyResult(BaseModel):
    volume_l: float
    daily_demand_l: float
    autonomy_days: float
    next_cut: Optional[TandeoEvent] = None
    covers_next_cut: Optional[bool] = None
    advice: str
