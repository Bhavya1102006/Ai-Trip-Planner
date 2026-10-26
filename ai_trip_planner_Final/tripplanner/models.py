"""Plain data containers shared by every module."""
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class POI:
    id: str
    name: str
    lat: float
    lon: float
    tags: tuple
    open_min: int      # minutes after midnight
    close_min: int
    duration: int      # typical visit length, minutes
    cost: int          # approx. spend per person, INR
    rating: float
    mainstream: bool


@dataclass
class TripBrief:
    destination: str
    days: int
    budget_per_day: int
    interests: List[str]
    pace: str = "moderate"   # relaxed | moderate | packed


@dataclass
class Stop:
    poi: POI
    arrive: int
    depart: int
    travel_from_prev_km: float


@dataclass
class DayPlan:
    day: int
    stops: List[Stop] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)

    @property
    def distance_km(self) -> float:
        return round(sum(s.travel_from_prev_km for s in self.stops), 2)

    @property
    def cost(self) -> int:
        return sum(s.poi.cost for s in self.stops)
