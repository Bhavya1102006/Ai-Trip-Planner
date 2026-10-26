"""Checks YOUR real keys against the live services and prints PASS / FAIL for each.

    export ANTHROPIC_API_KEY=... GOOGLE_PLACES_API_KEY=... DATABASE_URL=...
    python -m scripts.check_cloud

Run this once before demoing or deploying. It makes a handful of small requests
(a few cents at most) and never prints a key.
"""
import json
import sys

from tripplanner.config import Settings
from tripplanner.errors import PlannerError
from tripplanner.llm import client_from_settings, parse_brief_llm
from tripplanner.models import TripBrief
from tripplanner.places import GOOGLE_FIELDS, GOOGLE_URL, GooglePlacesClient, OverpassClient, google_place_to_poi
from tripplanner.http import request_json
from tripplanner.sources import default_weekday
from tripplanner.storage import open_store


def check(name, fn):
    try:
        detail = fn()
        print(f"PASS  {name}: {detail}")
        return True
    except PlannerError as exc:
        print(f"FAIL  {name}: {exc}")
    except Exception as exc:                          # keep going so every service gets checked
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    return False


def main() -> int:
    s = Settings.from_env()
    results = []

    def llm():
        c = client_from_settings(s)
        if c is None:
            raise PlannerError("no key set: add GROQ_API_KEY (or ANTHROPIC_API_KEY) to .env")
        try:
            b = parse_brief_llm("3 days in Jaipur, around 3000 rupees a day, love forts and street food, easy pace", c)
        except PlannerError as exc:
            if hasattr(c, "list_models"):                 # Groq: show which model names your key can use
                try:
                    exc = PlannerError(f"{exc}. Models your key can use (set GROQ_MODEL to one): {', '.join(c.list_models()[:12])}")
                except PlannerError:
                    pass
            raise exc
        return f"{s.llm_provider} parsed -> {b.days} days, {b.budget_per_day}/day, {b.interests}, {b.pace}"

    def google():
        if not s.google_key:
            raise PlannerError("GOOGLE_PLACES_API_KEY not set")
        raw = request_json("POST", GOOGLE_URL, {"X-Goog-Api-Key": s.google_key, "X-Goog-FieldMask": GOOGLE_FIELDS},
                           {"textQuery": "historical landmarks in Jaipur", "maxResultCount": 5})
        places = raw.get("places", [])
        if not places:
            raise PlannerError("request worked but returned no places; check the API is enabled and billing is on")
        first = places[0]
        missing = [k for k in ("id", "displayName", "location", "types") if k not in first]
        if missing:
            raise PlannerError(f"response is missing expected fields {missing}; response keys: {sorted(first)}")
        usable = [p for p in (google_place_to_poi(r, default_weekday()) for r in places) if p]
        return f"{len(places)} places returned, {len(usable)} usable by the planner; fields seen: {sorted(first)}"

    def opentripmap():
        import time
        from tripplanner.places import OpenTripMapClient
        if not s.opentripmap_key:
            raise PlannerError("OPENTRIPMAP_API_KEY not set (optional backup source)")
        t0 = time.time()
        pois = OpenTripMapClient(s.opentripmap_key).search("Pune", ["heritage", "food"], 1)
        return f"{len(pois)} places for Pune (heritage + food) in {time.time() - t0:.0f}s"

    def overpass():
        import time
        t0 = time.time()
        pois = OverpassClient().search("Pune", ["food", "nature"], 1)
        return f"{len(pois)} places for Pune (food + nature) in {time.time() - t0:.0f}s"

    def db():
        store = open_store(s.database_url)
        tid = store.save_trip("check_cloud", "test", {"ok": True})
        ok = store.get_trip(tid, "check_cloud")["plan"] == {"ok": True}
        return f"{store.name}: write + read {'ok' if ok else 'MISMATCH'}"

    for name, fn in (("language model", llm), ("Google Places", google), ("OpenTripMap", opentripmap), ("OpenStreetMap", overpass), ("database", db)):
        results.append(check(name, fn))
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
