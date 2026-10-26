"""Itinerary planning engine: score -> select -> cluster -> sequence.

Pipeline (matches the architecture in Stage 1, Fig. 1):
  1. score    every POI against the traveller's interests
  2. select   the best-scoring pool of candidates for the trip
  3. cluster  candidates geographically, one cluster per day (k-means)
  4. sequence each cluster in time using a window-aware greedy heuristic
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .errors import InfeasiblePlanError
from .geo import haversine_km, kmeans, road_km, travel_minutes
from .models import POI, DayPlan, Stop, TripBrief

log = logging.getLogger("tripplanner")

DAY_START = 9 * 60       # 09:00
DAY_END = 20 * 60        # 20:00
NIGHT_DAY_END = 23 * 60 + 30   # used when the traveller asked for nightlife
STOPS_PER_DAY = {"relaxed": 3, "moderate": 4, "packed": 5}

# user-facing interest -> POI tags that satisfy it
INTEREST_TAGS: Dict[str, set] = {
    "heritage": {"heritage", "history", "architecture"},
    "food": {"food", "local", "sweets", "street food", "cafe"},
    "shopping": {"shopping"},
    "nature": {"nature", "relaxation"},
    "art": {"art", "culture", "science"},
    "spiritual": {"spiritual"},
    "photography": {"photography", "views"},
    "nightlife": {"nightlife", "entertainment"},
}


@dataclass
class Itinerary:
    brief: TripBrief
    days: List[DayPlan]
    unscheduled: List[str] = field(default_factory=list)

    @property
    def total_km(self) -> float:
        return round(sum(d.distance_km for d in self.days), 2)

    @property
    def stop_count(self) -> int:
        return sum(len(d.stops) for d in self.days)


def score_poi(poi: POI, interests: List[str]) -> float:
    """Higher is better. Interest overlap dominates; rating breaks ties and
    smaller, non-mainstream places get a small bonus (SDG 8/11 goal)."""
    wanted = set()
    for i in interests:
        wanted |= INTEREST_TAGS.get(i, {i})
    overlap = len(wanted & set(poi.tags))
    if overlap == 0:
        return 0.0
    return 2.0 * overlap + poi.rating / 5.0 + (0.4 if not poi.mainstream else 0.0)


def select_candidates(brief: TripBrief, pois: List[POI]) -> List[POI]:
    scored = [(score_poi(p, brief.interests), p) for p in pois]
    scored = [sp for sp in scored if sp[0] > 0]
    if not scored:
        raise InfeasiblePlanError(
            f"Nothing in the dataset matches interests: {', '.join(brief.interests)}.")
    scored.sort(key=lambda sp: (-sp[0], sp[1].id))          # id keeps ordering stable
    pool_size = brief.days * STOPS_PER_DAY[brief.pace] + brief.days
    chosen = [p for _, p in scored[:pool_size]]
    log.info("selected %d candidates from %d matching POIs", len(chosen), len(scored))
    return chosen


def day_end_for(brief: TripBrief) -> int:
    return NIGHT_DAY_END if "nightlife" in brief.interests else DAY_END


def sequence_day(cluster: List[POI], budget: int, pace: str,
                 day_end: int = DAY_END) -> Tuple[List[Stop], List[str]]:
    """Order one day's stops, respecting opening hours, day end, budget, pace cap."""
    cap = STOPS_PER_DAY[pace]
    remaining = list(cluster)
    stops: List[Stop] = []
    dropped: List[str] = []
    spent = 0
    now, here = DAY_START, None

    while remaining and len(stops) < cap:
        best = None
        for p in remaining:
            km = 0.0 if here is None else road_km(here.lat, here.lon, p.lat, p.lon)
            arrive = max(now + travel_minutes(km), p.open_min)    # wait if not yet open
            depart = arrive + p.duration
            if depart > p.close_min or depart > day_end:
                continue                                           # would overrun
            if spent + p.cost > budget:
                continue                                           # over the day budget
            wait = max(0, p.open_min - (now + travel_minutes(km)))
            cost_key = (travel_minutes(km) + wait, p.id)           # prefer short hops, little waiting
            if best is None or cost_key < best[0]:
                best = (cost_key, p, arrive, depart, km)
        if best is None:
            break
        _, p, arrive, depart, km = best
        stops.append(Stop(p, arrive, depart, round(km, 2)))
        spent += p.cost
        now, here = depart, p
        remaining.remove(p)
        log.debug("  + %-34s arrive %s depart %s (%.1f km)", p.name, arrive, depart, km)

    dropped = [p.name for p in remaining]
    return stops, dropped


def _centroid(c: List[POI]) -> Tuple[float, float]:
    return sum(p.lat for p in c) / len(c), sum(p.lon for p in c) / len(c)


def rebalance(clusters: List[List[POI]], capacity: int) -> List[List[POI]]:
    """k-means can return lopsided groups (e.g. 8/3/1). Move the member that sits
    furthest from an over-full group's centre into the nearest group with space."""
    clusters = [list(c) for c in clusters]
    moved = True
    while moved:
        moved = False
        for c in clusters:
            if len(c) <= capacity:
                continue
            cen = _centroid(c)
            far = max(c, key=lambda p: haversine_km(p.lat, p.lon, cen[0], cen[1]))
            targets = [t for t in clusters if t is not c and len(t) < capacity]
            if not targets:
                continue
            best = min(targets, key=lambda t: haversine_km(
                far.lat, far.lon, *(_centroid(t) if t else (far.lat, far.lon))))
            c.remove(far)
            best.append(far)
            moved = True
    return clusters


def plan_trip(brief: TripBrief, pois: List[POI], seed: int = 7) -> Itinerary:
    log.info("planning %d day(s) in %s | budget %d/day | interests=%s | pace=%s",
             brief.days, brief.destination, brief.budget_per_day,
             ",".join(brief.interests), brief.pace)
    candidates = select_candidates(brief, pois)
    labels = kmeans([(p.lat, p.lon) for p in candidates], brief.days, seed=seed)

    clusters: Dict[int, List[POI]] = {}
    for p, lab in zip(candidates, labels):
        clusters.setdefault(lab, []).append(p)
    cap = STOPS_PER_DAY[brief.pace]
    groups = rebalance(list(clusters.values()), cap + 1)
    groups = [g for g in groups if g]
    # north-to-south ordering keeps the day numbering deterministic
    ordered = sorted(groups, key=lambda c: -_centroid(c)[0])
    log.info("clustered into %d day-groups of sizes %s (after balancing)",
             len(ordered), [len(c) for c in ordered])

    days, leftovers = [], []
    for i, cluster in enumerate(ordered, start=1):
        stops, dropped = sequence_day(cluster, brief.budget_per_day, brief.pace, day_end_for(brief))
        days.append(DayPlan(i, stops, []))
        leftovers += [p for p in cluster if p.name in dropped]
        log.info("day %d: %d stops, %.1f km, cost %d", i, len(stops), days[-1].distance_km, days[-1].cost)

    # second pass: offer leftovers to days that still have room
    for d in days:
        if not leftovers or len(d.stops) >= cap:
            continue
        pool = [s.poi for s in d.stops] + leftovers
        stops, dropped = sequence_day(pool, brief.budget_per_day, brief.pace, day_end_for(brief))
        if len(stops) > len(d.stops):
            log.info("day %d: re-sequenced with leftovers (%d -> %d stops)", d.day, len(d.stops), len(stops))
            d.stops = stops
            leftovers = [p for p in pool if p.name in dropped]
    unscheduled = [p.name for p in leftovers]

    if all(not d.stops for d in days):
        raise InfeasiblePlanError("No stop could be scheduled within budget and opening hours.")
    return Itinerary(brief, days, unscheduled)


def baseline_km(brief: TripBrief, pois: List[POI]) -> float:
    """Naive comparison plan: same candidates, visited in score order, split into
    days without looking at geography. Used only for the SDG distance metric."""
    cand = select_candidates(brief, pois)
    cap = STOPS_PER_DAY[brief.pace]
    total = 0.0
    for d in range(brief.days):
        chunk = cand[d * cap:(d + 1) * cap]
        for a, b in zip(chunk, chunk[1:]):
            total += road_km(a.lat, a.lon, b.lat, b.lon)
    return round(total, 2)
