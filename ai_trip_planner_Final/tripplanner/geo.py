"""Geometry helpers: distance, travel-time estimate and a small k-means."""
import math
import random
from typing import List, Sequence, Tuple

EARTH_RADIUS_KM = 6371.0
ROAD_FACTOR = 1.3        # straight line -> rough road distance
CITY_SPEED_KMH = 22.0    # average door-to-door speed in dense city traffic


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def road_km(lat1, lon1, lat2, lon2) -> float:
    return haversine_km(lat1, lon1, lat2, lon2) * ROAD_FACTOR


def travel_minutes(km: float) -> int:
    """Convert a road distance to minutes, rounded up to a 5-minute step."""
    raw = km / CITY_SPEED_KMH * 60
    return int(math.ceil(raw / 5.0) * 5) if raw > 0 else 0


def kmeans(points: Sequence[Tuple[float, float]], k: int, seed: int = 7,
           max_iter: int = 100) -> List[int]:
    """Cluster (lat, lon) points into k groups; returns a label per point.

    Uses k-means++ style seeding with a fixed seed so results are repeatable.
    Distances use haversine, so clusters stay sensible away from the equator.
    """
    n = len(points)
    if k <= 0:
        raise ValueError("k must be positive")
    if k >= n:
        return list(range(n))
    rng = random.Random(seed)
    centres = [points[rng.randrange(n)]]
    while len(centres) < k:
        d2 = [min(haversine_km(p[0], p[1], c[0], c[1]) ** 2 for c in centres) for p in points]
        total = sum(d2)
        if total == 0:
            centres.append(points[rng.randrange(n)])
            continue
        r, acc = rng.random() * total, 0.0
        for p, d in zip(points, d2):
            acc += d
            if acc >= r:
                centres.append(p)
                break
    labels = [-1] * n
    for _ in range(max_iter):
        new = [min(range(k), key=lambda c: haversine_km(p[0], p[1], centres[c][0], centres[c][1]))
               for p in points]
        if new == labels:
            break
        labels = new
        for c in range(k):
            members = [points[i] for i in range(n) if labels[i] == c]
            if members:
                centres[c] = (sum(m[0] for m in members) / len(members),
                              sum(m[1] for m in members) / len(members))
    return labels
