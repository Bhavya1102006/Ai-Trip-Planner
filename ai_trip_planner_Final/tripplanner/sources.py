"""Chooses where place data comes from and falls back gracefully.

mode "local": the bundled JSON file only.
mode "live" : Google Places if a key is set, else OpenStreetMap; falls back to the
              local file (when one exists for the city) if live data is thin or fails.
"""
import datetime
import logging
from typing import List, Optional, Tuple

from .config import Settings
from .data_loader import AVAILABLE, load_pois, normalize_destination
from .errors import CloudServiceError, UnknownDestinationError
from .http import Fetch, request_json
from .models import POI, TripBrief
from .places import GooglePlacesClient, MemoryCache, OpenTripMapClient, OverpassClient

log = logging.getLogger("tripplanner")
MIN_LIVE_POIS = 8
_cache = MemoryCache()


def default_weekday() -> int:
    """Google numbering (0 = Sunday) for tomorrow; used until the brief carries a date."""
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    return (tomorrow.weekday() + 1) % 7


def get_pois(brief: TripBrief, settings: Settings, mode: str = "local", fetch: Fetch = request_json,
             weekday: Optional[int] = None) -> Tuple[List[POI], str]:
    """Returns (places, source name)."""
    norm = normalize_destination(brief.destination)
    if mode == "local" and norm in AVAILABLE:
        return load_pois(norm), "local file"
    if mode not in ("local", "live"):
        raise ValueError(f"unknown places mode: {mode}")

    wd = default_weekday() if weekday is None else weekday
    key = (brief.destination.lower(), tuple(sorted(brief.interests)), wd,
           bool(settings.google_key), bool(settings.opentripmap_key))
    cached = _cache.get(key)
    if cached:
        return cached

    # best provider first; each one is skipped on failure or when it returns too little
    providers = []
    if settings.google_key:
        providers.append(("Google Places", GooglePlacesClient(settings.google_key, fetch)))
    if settings.opentripmap_key:
        providers.append(("OpenTripMap", OpenTripMapClient(settings.opentripmap_key, fetch)))
    providers.append(("OpenStreetMap", OverpassClient(fetch)))

    pois: List[POI] = []
    source = ""
    for name, client in providers:
        try:
            found = client.search(brief.destination, brief.interests, wd)
        except CloudServiceError as exc:
            log.warning("%s failed: %s", name, exc)
            continue
        if len(found) > len(pois):
            pois, source = found, name
        if len(found) >= MIN_LIVE_POIS:
            break
        log.warning("%s returned only %d places; trying the next source", name, len(found))

    if len(pois) < MIN_LIVE_POIS:
        if norm in AVAILABLE:
            log.warning("only %d live places; using the local dataset instead", len(pois))
            return load_pois(norm), "local file (fallback)"
        if not pois:
            raise UnknownDestinationError(
                f"No place data found for '{brief.destination}'. The free map services may be busy, "
                "so try again in a minute (an OPENTRIPMAP_API_KEY gives a second source).")
    _cache.put(key, (pois, source))
    return pois, source
