"""Live place data: Google Places API (New) and OpenStreetMap Overpass.

Both clients turn provider data into the same POI objects the planner already uses.
Results are kept in memory only (short TTL). Google's terms restrict storing Places
content, so nothing from Google is written to disk or to the database.
"""
import logging
import re
import time
import urllib.parse
from typing import Callable, Dict, List, Optional, Tuple

from .errors import CloudServiceError
from .http import Fetch, request_json
from .models import POI

log = logging.getLogger("tripplanner")

GOOGLE_URL = "https://places.googleapis.com/v1/places:searchText"
# ask only for what the planner needs: each extra field can raise the billing tier
GOOGLE_FIELDS = ",".join("places." + f for f in (
    "id", "displayName", "location", "types", "rating", "userRatingCount",
    "priceLevel", "regularOpeningHours"))
OVERPASS_URLS = [  # public mirrors, tried in order: any one of them can be busy
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
SEARCH_RADIUS_DEG = 0.05      # about 5-6 km each way: the busy centre of a city, and a light query
OVERPASS_TIMEOUT = 45

MAINSTREAM_RATING_COUNT = 5000     # many reviews -> well-known, high-footfall place
DEFAULT_OPEN, DEFAULT_CLOSE = 9 * 60, 18 * 60
RESULTS_PER_QUERY = 10

INTEREST_QUERIES = {
    "heritage": "historical landmarks and forts",
    "food": "popular local restaurants and sweet shops",
    "shopping": "markets and bazaars",
    "nature": "parks and gardens",
    "art": "museums and art galleries",
    "spiritual": "temples and places of worship",
    "photography": "scenic viewpoints",
    "nightlife": "cafes and nightlife",
}
GOOGLE_TYPE_TAGS = {
    "tourist_attraction": ["heritage"], "historical_landmark": ["heritage", "history"],
    "historical_place": ["heritage", "history"], "monument": ["heritage", "history"],
    "museum": ["art", "culture", "history"], "art_gallery": ["art", "culture"],
    "hindu_temple": ["spiritual"], "place_of_worship": ["spiritual"], "church": ["spiritual"],
    "mosque": ["spiritual"], "park": ["nature", "relaxation"], "garden": ["nature", "relaxation"],
    "national_park": ["nature"], "restaurant": ["food", "local"], "cafe": ["food", "cafe"],
    "bakery": ["food", "sweets"], "shopping_mall": ["shopping"], "market": ["shopping", "culture"],
    "night_club": ["nightlife", "entertainment"], "bar": ["nightlife"],
    "movie_theater": ["entertainment"], "amusement_park": ["entertainment"],
}
# which OSM selectors each interest needs, so we only ask the server for what the traveller wants
INTEREST_OSM = {
    "heritage": [("historic", "fort|castle|monument|memorial|archaeological_site|ruins"), ("tourism", "attraction")],
    "food": [("amenity", "restaurant|cafe")],
    "shopping": [("amenity", "marketplace"), ("shop", "mall|department_store|gift|handicraft|jewelry")],
    "nature": [("leisure", "park|garden")],
    "art": [("tourism", "museum|gallery")],
    "spiritual": [("amenity", "place_of_worship")],
    "photography": [("tourism", "viewpoint|attraction")],
    "nightlife": [("amenity", "nightclub|bar|cinema|cafe")],
}
OSM_TAGS = [  # (osm key, value regex, planner tags)
    ("historic", r"fort|castle|monument|memorial|archaeological_site|ruins", ["heritage", "history"]),
    ("tourism", r"museum|gallery", ["art", "culture"]),
    ("tourism", r"attraction", ["heritage"]),
    ("tourism", r"viewpoint", ["photography", "views"]),
    ("amenity", r"place_of_worship", ["spiritual"]),
    ("leisure", r"park|garden", ["nature", "relaxation"]),
    ("amenity", r"restaurant", ["food", "local"]),
    ("amenity", r"cafe", ["food", "cafe"]),
    ("amenity", r"marketplace", ["shopping", "culture"]),
    ("shop", r"mall|department_store|gift|handicraft|jewelry", ["shopping"]),
    ("amenity", r"nightclub|bar|cinema", ["nightlife", "entertainment"]),
]
DURATION_BY_TAG = [("heritage", 90), ("art", 75), ("spiritual", 40), ("nature", 50),
                   ("shopping", 75), ("nightlife", 120), ("food", 60)]
PRICE_INR = {"PRICE_LEVEL_FREE": 0, "PRICE_LEVEL_INEXPENSIVE": 200, "PRICE_LEVEL_MODERATE": 500,
             "PRICE_LEVEL_EXPENSIVE": 1200, "PRICE_LEVEL_VERY_EXPENSIVE": 2500}
# spend is shown in INR because the planner's budget is in INR; for other currencies, scale PRICE_INR
FREE_TAGS = {"spiritual", "nature"}


def _duration(tags) -> int:
    return next((d for t, d in DURATION_BY_TAG if t in tags), 60)


def google_hours(opening: Optional[dict], weekday: int) -> Optional[Tuple[int, int]]:
    """(open, close) minutes for a Google weekday (0 = Sunday), None if closed that day.
    Missing hours data falls back to 09:00-18:00 so a place is not silently lost."""
    periods = (opening or {}).get("periods")
    if not periods:
        return DEFAULT_OPEN, DEFAULT_CLOSE
    for p in periods:
        o = p.get("open") or {}
        if o.get("day") != weekday:
            continue
        start = o.get("hour", 0) * 60 + o.get("minute", 0)
        c = p.get("close")
        if not c:
            return 0, 24 * 60                     # open all day
        end = c.get("hour", 0) * 60 + c.get("minute", 0)
        if c.get("day") != weekday or end <= start:
            end = 24 * 60                         # runs past midnight: cap at end of day
        return start, end
    return None


def google_place_to_poi(raw: dict, weekday: int) -> Optional[POI]:
    loc = raw.get("location") or {}
    name = (raw.get("displayName") or {}).get("text")
    if not name or "latitude" not in loc or "longitude" not in loc or not raw.get("id"):
        return None
    tags: List[str] = []
    for t in raw.get("types", []):
        for tag in GOOGLE_TYPE_TAGS.get(t, []):
            if tag not in tags:
                tags.append(tag)
    if not tags:
        return None                               # a type we cannot place (hotel, atm, ...)
    hours = google_hours(raw.get("regularOpeningHours"), weekday)
    if hours is None:
        return None
    cost = PRICE_INR.get(raw.get("priceLevel"))
    if cost is None:
        cost = 0 if FREE_TAGS & set(tags) else 300
    return POI("g_" + raw["id"], name, float(loc["latitude"]), float(loc["longitude"]), tuple(tags),
               hours[0], hours[1], _duration(tags), cost, float(raw.get("rating", 4.0)),
               int(raw.get("userRatingCount", 0)) >= MAINSTREAM_RATING_COUNT)


class GooglePlacesClient:
    def __init__(self, api_key: str, fetch: Fetch = request_json):
        if not api_key:
            raise CloudServiceError("Google Places API key is not configured.")
        self._key, self._fetch = api_key, fetch

    def search(self, destination: str, interests: List[str], weekday: int) -> List[POI]:
        found: Dict[str, POI] = {}
        queries = [INTEREST_QUERIES[i] for i in interests if i in INTEREST_QUERIES] or ["tourist attractions"]
        for q in queries:
            data = self._fetch("POST", GOOGLE_URL,
                               {"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": GOOGLE_FIELDS},
                               {"textQuery": f"{q} in {destination}", "maxResultCount": RESULTS_PER_QUERY})
            for raw in data.get("places", []):
                poi = google_place_to_poi(raw, weekday)
                if poi:
                    found.setdefault(poi.id, poi)
        log.info("google places: %d usable places for %s", len(found), destination)
        return list(found.values())


# ------------------------------------------------------------------ OpenStreetMap
def osm_hours(text: Optional[str]) -> Tuple[int, int]:
    """Understands the simple form 'Mo-Su 09:00-17:00' (or '09:00-17:00'); anything else -> default."""
    m = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", text or "")
    if not m:
        return DEFAULT_OPEN, DEFAULT_CLOSE
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    return (a, b) if b > a else (a, 24 * 60)


def osm_element_to_poi(el: dict) -> Optional[POI]:
    tags = el.get("tags") or {}
    name = tags.get("name:en") or tags.get("name")
    lat = el.get("lat") if "lat" in el else (el.get("center") or {}).get("lat")
    lon = el.get("lon") if "lon" in el else (el.get("center") or {}).get("lon")
    if not name or lat is None or lon is None:
        return None
    mapped: List[str] = []
    for key, pattern, ptags in OSM_TAGS:
        value = tags.get(key)
        if value and re.search(pattern, value, re.I):
            mapped += [t for t in ptags if t not in mapped]
    if not mapped:
        return None
    o, c = osm_hours(tags.get("opening_hours"))
    famous = "wikipedia" in tags or "wikidata" in tags      # crude stand-in for footfall
    cost = 0 if FREE_TAGS & set(mapped) else 300
    return POI(f"osm_{el.get('type', 'x')}{el.get('id')}", name, float(lat), float(lon), tuple(mapped),
               o, c, _duration(mapped), cost, 4.0, famous)


class OverpassClient:
    """Free, no key needed. Rating and price are unknown, so the planner relies on tags only.

    Steps: find the city centre with Nominatim, then ask Overpass for places in a box around it.
    A bounding box is far lighter than 'everything inside the city boundary', which is what
    made the first version time out on large cities."""

    def __init__(self, fetch: Fetch = request_json):
        self._fetch = fetch

    def locate(self, destination: str):
        query = f"{destination}, India" if "india" not in destination.lower() else destination
        url = NOMINATIM_URL + "?" + urllib.parse.urlencode({"q": query, "format": "json", "limit": 1})
        data = self._fetch("GET", url)
        if not isinstance(data, list) or not data:
            url = NOMINATIM_URL + "?" + urllib.parse.urlencode({"q": destination, "format": "json", "limit": 1})
            data = self._fetch("GET", url)
        if not isinstance(data, list) or not data:
            raise CloudServiceError(f"Could not find '{destination}' on the map.")
        try:
            return float(data[0]["lat"]), float(data[0]["lon"])
        except (KeyError, ValueError, TypeError) as exc:
            raise CloudServiceError("Map lookup returned an unexpected reply.") from exc

    def search(self, destination: str, interests: List[str], weekday: int = 0) -> List[POI]:
        lat, lon = self.locate(destination)
        s, n, w, e = (lat - SEARCH_RADIUS_DEG, lat + SEARCH_RADIUS_DEG,
                      lon - SEARCH_RADIUS_DEG, lon + SEARCH_RADIUS_DEG)
        box = f"({s:.5f},{w:.5f},{n:.5f},{e:.5f})"
        wanted = []
        for i in (interests or INTEREST_OSM):
            for sel in INTEREST_OSM.get(i, []):
                if sel not in wanted:
                    wanted.append(sel)
        wanted = wanted or [s for sels in INTEREST_OSM.values() for s in sels]
        parts = "".join(f'nwr["name"]["{k}"~"{v}"]{box};' for k, v in wanted)
        q = f"[out:json][timeout:40];({parts});out center 150 qt;"
        body = "data=" + urllib.parse.quote(q)
        data, last = None, None
        for url in OVERPASS_URLS:
            try:
                data = self._fetch("POST", url, {"Content-Type": "application/x-www-form-urlencoded"}, body,
                                   timeout=OVERPASS_TIMEOUT, retries=0)
                break
            except CloudServiceError as exc:
                last = exc
                log.warning("overpass mirror failed, trying next: %s", exc)
        if data is None:
            raise last
        found: Dict[str, POI] = {}
        for el in data.get("elements", []):
            poi = osm_element_to_poi(el)
            if poi:
                found.setdefault(poi.id, poi)
        log.info("overpass: %d usable places near %s", len(found), destination)
        return list(found.values())


# ------------------------------------------------------------------ OpenTripMap
OTM_URL = "https://api.opentripmap.com/0.1/en/places"
OTM_RADIUS_M = 6000
OTM_LIMIT = 60
# interest -> (OpenTripMap "kinds" categories, minimum popularity rate to ask for)
INTEREST_OTM = {
    "heritage": ("historic,architecture", 2),
    "art": ("cultural", 2),
    "spiritual": ("religion", 2),
    "nature": ("natural", 2),
    "photography": ("natural,historic", 2),
    "food": ("foods", 1),
    "shopping": ("shops", 1),
    "nightlife": ("amusements", 1),
}
# words that appear in an OpenTripMap "kinds" string -> planner tags
OTM_KIND_TAGS = {
    "fortifications": ["heritage", "history"], "castles": ["heritage", "history"],
    "historic": ["heritage", "history"], "monuments_and_memorials": ["heritage", "history"],
    "archaeology": ["heritage", "history"], "historic_architecture": ["heritage", "architecture"],
    "architecture": ["architecture"], "museums": ["art", "culture"], "cultural": ["culture"],
    "religion": ["spiritual"], "churches": ["spiritual"], "temples": ["spiritual"],
    "hindu_temples": ["spiritual"], "mosques": ["spiritual"], "other_temples": ["spiritual"],
    "natural": ["nature", "relaxation"], "gardens_and_parks": ["nature", "relaxation"],
    "view_points": ["photography", "views"], "foods": ["food", "local"], "restaurants": ["food", "local"],
    "cafes": ["food", "cafe"], "bars": ["nightlife"], "pubs": ["nightlife"],
    "shops": ["shopping"], "marketplaces": ["shopping", "culture"], "malls": ["shopping"],
    "amusements": ["entertainment"], "theatres_and_entertainments": ["entertainment", "culture"],
}
OTM_RATING = {0: 3.8, 1: 4.0, 2: 4.3, 3: 4.6}


def otm_item_to_poi(item: dict) -> Optional[POI]:
    pt = item.get("point") or {}
    if not pt and isinstance(item.get("geometry"), dict):          # GeoJSON-shaped item
        coords = item["geometry"].get("coordinates") or []
        pt = {"lon": coords[0], "lat": coords[1]} if len(coords) >= 2 else {}
        item = {**item, **(item.get("properties") or {})}
    name = (item.get("name") or "").strip()
    if not name or not item.get("xid") or "lat" not in pt or "lon" not in pt:
        return None
    tags: List[str] = []
    for kind in str(item.get("kinds", "")).split(","):
        for tag in OTM_KIND_TAGS.get(kind.strip(), []):
            if tag not in tags:
                tags.append(tag)
    if not tags:
        return None
    digits = re.sub(r"\D", "", str(item.get("rate", "0"))) or "0"
    rate = min(int(digits), 3)
    cost = 0 if FREE_TAGS & set(tags) else 300
    return POI("otm_" + str(item["xid"]), name, float(pt["lat"]), float(pt["lon"]), tuple(tags),
               DEFAULT_OPEN, DEFAULT_CLOSE, _duration(tags), cost, OTM_RATING[rate], rate >= 3)


class OpenTripMapClient:
    """Free key from dev.opentripmap.org. The place list has no opening hours or prices, so
    default hours (09:00-18:00) and tag-based costs are used."""

    def __init__(self, api_key: str, fetch: Fetch = request_json):
        if not api_key:
            raise CloudServiceError("OpenTripMap API key is not configured.")
        self._key, self._fetch = api_key, fetch

    def _get(self, path: str, **params) -> dict:
        qs = urllib.parse.urlencode({**params, "apikey": self._key})
        return self._fetch("GET", f"{OTM_URL}/{path}?{qs}")

    def locate(self, destination: str):
        data = self._get("geoname", name=destination)
        if not isinstance(data, dict) or data.get("status") != "OK" or "lat" not in data:
            raise CloudServiceError(f"OpenTripMap could not find '{destination}'.")
        return float(data["lat"]), float(data["lon"])

    def search(self, destination: str, interests: List[str], weekday: int = 0) -> List[POI]:
        lat, lon = self.locate(destination)
        wanted = [INTEREST_OTM[i] for i in (interests or INTEREST_OTM) if i in INTEREST_OTM] \
            or list(INTEREST_OTM.values())
        found: Dict[str, POI] = {}
        seen = set()
        for kinds, rate in wanted:
            if (kinds, rate) in seen:
                continue
            seen.add((kinds, rate))
            items = self._get("radius", radius=OTM_RADIUS_M, lon=lon, lat=lat, kinds=kinds, rate=rate,
                              format="json", limit=OTM_LIMIT)
            for item in (items if isinstance(items, list) else []):
                poi = otm_item_to_poi(item)
                if poi:
                    found.setdefault(poi.id, poi)
        log.info("opentripmap: %d usable places near %s", len(found), destination)
        return list(found.values())


class MemoryCache:
    """Tiny in-memory TTL cache; lost on restart by design (see module docstring)."""

    def __init__(self, ttl_seconds: float = 3600, clock: Callable[[], float] = time.time):
        self._ttl, self._clock, self._d = ttl_seconds, clock, {}

    def get(self, key):
        hit = self._d.get(key)
        if hit and self._clock() - hit[0] < self._ttl:
            return hit[1]
        self._d.pop(key, None)
        return None

    def put(self, key, value):
        self._d[key] = (self._clock(), value)
