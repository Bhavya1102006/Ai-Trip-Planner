"""Loads and validates point-of-interest data from JSON files."""
import json
import os
from typing import List

from .errors import UnknownDestinationError, PlannerError
from .models import POI

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

ALIASES = {
    # City & Region aliases
    "bangalore": "bengaluru",
    "bengaluru": "bengaluru",
    "bombay": "mumbai",
    "mumbai": "mumbai",
    "calcutta": "kolkata",
    "kolkata": "kolkata",
    "madras": "chennai",
    "chennai": "chennai",
    "cochin": "kochi",
    "kochi": "kochi",
    "mysore": "mysuru",
    "mysuru": "mysuru",
    "banaras": "varanasi",
    "kashi": "varanasi",
    "varanasi": "varanasi",
    "new delhi": "delhi",
    "delhi": "delhi",
    "panaji": "goa",
    "north goa": "goa",
    "south goa": "goa",
    "kashmir": "srinagar",
    "gulmarg": "srinagar",
    "pahalgam": "srinagar",
    "ladakh": "leh",
    "nubra": "leh",
    "pangong": "leh",
    "mussoorie": "nainital",
    "dehradun": "rishikesh",
    "haridwar": "rishikesh",
    "ayodhya": "lucknow",
    "coorg": "hampi",
    "kodagu": "hampi",
    "kodaikanal": "ooty",
    "rameswaram": "madurai",
    "kanyakumari": "madurai",
    "alappuzha": "alleppey",
    "wayanad": "alleppey",
    "varkala": "alleppey",
    "thekkady": "alleppey",
    "bhubaneswar": "puri",
    "konark": "puri",
    "bodh gaya": "bodhgaya",
    "nalanda": "bodhgaya",
    "rajgir": "bodhgaya",
    "patna": "bodhgaya",
    "guwahati": "shillong",
    "cherrapunji": "shillong",
    "kaziranga": "shillong",
    "puducherry": "pondicherry",
    "port blair": "andaman",
    "havelock": "andaman",
    "andaman and nicobar": "andaman",
    "andaman & nicobar": "andaman",
    "bhopal": "indore",
    "ujjain": "indore",
    "sanchi": "indore",
    "khajuraho": "indore",
    "mcleodganj": "dharamshala",
    "mcleod ganj": "dharamshala",

    # All 28 States & 8 Union Territories of India
    "andhra pradesh": "hyderabad",
    "andhra": "hyderabad",
    "arunachal pradesh": "shillong",
    "arunachal": "shillong",
    "assam": "shillong",
    "bihar": "bodhgaya",
    "chhattisgarh": "indore",
    "goa": "goa",
    "gujarat": "ahmedabad",
    "haryana": "chandigarh",
    "himachal pradesh": "shimla",
    "himachal": "manali",
    "jharkhand": "bodhgaya",
    "karnataka": "bengaluru",
    "kerala": "kochi",
    "madhya pradesh": "indore",
    "mp": "indore",
    "maharashtra": "mumbai",
    "manipur": "shillong",
    "meghalaya": "shillong",
    "mizoram": "shillong",
    "nagaland": "shillong",
    "odisha": "puri",
    "orissa": "puri",
    "punjab": "amritsar",
    "rajasthan": "jaipur",
    "sikkim": "gangtok",
    "tamil nadu": "chennai",
    "tamilnadu": "chennai",
    "telangana": "hyderabad",
    "tripura": "shillong",
    "uttar pradesh": "agra",
    "up": "lucknow",
    "uttarakhand": "rishikesh",
    "west bengal": "kolkata",
    "bengal": "kolkata",
    # UTs
    "chandigarh": "chandigarh",
    "dadra and nagar haveli": "goa",
    "daman and diu": "goa",
    "jammu and kashmir": "srinagar",
    "jammu & kashmir": "srinagar",
    "lakshadweep": "kochi",
    "puducherry": "pondicherry",
}


def _discover_available():
    """Discover all *_pois.json files in data directory and add aliases."""
    avail = {}
    if os.path.exists(DATA_DIR):
        for fname in os.listdir(DATA_DIR):
            if fname.endswith("_pois.json"):
                city = fname[:-10].lower()
                avail[city] = fname
    for alias, target in ALIASES.items():
        target_file = f"{target}_pois.json"
        if target in avail or (os.path.exists(os.path.join(DATA_DIR, target_file))):
            avail[alias] = target_file
    return avail


AVAILABLE = _discover_available()


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def fmt_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def normalize_destination(destination: str) -> str:
    cleaned = destination.strip().lower()
    # Check direct match
    if cleaned in AVAILABLE:
        return cleaned
    # Check alias
    if cleaned in ALIASES and ALIASES[cleaned] in AVAILABLE:
        return ALIASES[cleaned]
    # Check substring match (e.g. "south goa" -> "goa", "pune city" -> "pune")
    for key in sorted(AVAILABLE.keys(), key=len, reverse=True):
        if key in cleaned or cleaned in key:
            return key
    return cleaned


def load_pois(destination: str) -> List[POI]:
    key = normalize_destination(destination)
    if key not in AVAILABLE:
        curated_cities = sorted(set(fname[:-10].title() for fname in os.listdir(DATA_DIR) if fname.endswith("_pois.json")))
        raise UnknownDestinationError(
            f"No local data for '{destination}'. Available curated destinations in India: {', '.join(curated_cities)}")
    path = os.path.join(DATA_DIR, AVAILABLE[key])
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise PlannerError(f"Could not read POI data file: {exc}") from exc
    pois = []
    for r in raw:
        if not (-90 <= r["lat"] <= 90 and -180 <= r["lon"] <= 180):
            continue                       # skip rows with impossible coordinates
        if _to_minutes(r["close"]) <= _to_minutes(r["open"]):
            continue                       # skip rows with inverted opening hours
        pois.append(POI(r["id"], r["name"], r["lat"], r["lon"], tuple(r["tags"]),
                        _to_minutes(r["open"]), _to_minutes(r["close"]),
                        r["duration"], r["cost"], r["rating"], r["mainstream"]))
    return pois

