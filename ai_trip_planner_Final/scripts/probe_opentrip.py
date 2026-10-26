"""Shows what OpenTripMap really returns for a city, so the category filters can be tuned.

    python -m scripts.probe_opentripmap            (uses Pune)
    python -m scripts.probe_opentripmap Jaipur

Needs OPENTRIPMAP_API_KEY in .env. Makes about 25 small requests. Never prints your key.
"""
import sys
from collections import Counter

from tripplanner.config import Settings
from tripplanner.errors import PlannerError
from tripplanner.places import OTM_KIND_TAGS, OpenTripMapClient, otm_item_to_poi

CATEGORIES = ["historic", "architecture", "cultural", "religion", "natural", "foods", "shops", "amusements"]


def main() -> int:
    city = sys.argv[1] if len(sys.argv) > 1 else "Pune"
    key = Settings.from_env().opentripmap_key
    if not key:
        print("OPENTRIPMAP_API_KEY is not set in .env")
        return 1
    client = OpenTripMapClient(key)
    try:
        lat, lon = client.locate(city)
    except PlannerError as exc:
        print("FAIL locating city:", exc)
        return 1
    print(f"{city}: centre {lat:.3f}, {lon:.3f}\n")
    print(f"{'category':<14}{'rate':<8}{'returned':>9}{'usable':>9}")
    unmapped, samples = Counter(), {}
    for kinds in CATEGORIES:
        for rate in (None, 1, 2):
            params = dict(radius=6000, lon=lon, lat=lat, kinds=kinds, format="json", limit=60)
            if rate:
                params["rate"] = rate
            try:
                items = client._get("radius", **params)
            except PlannerError as exc:
                print(f"{kinds:<14}{str(rate or 'any'):<8}  error: {exc}")
                continue
            items = items if isinstance(items, list) else []
            usable = [otm_item_to_poi(i) for i in items]
            print(f"{kinds:<14}{str(rate or 'any'):<8}{len(items):>9}{sum(1 for u in usable if u):>9}")
            if rate is None:
                samples[kinds] = [i.get("name") for i in items if i.get("name")][:3]
                for i, u in zip(items, usable):
                    if u is None and i.get("name"):
                        for k in str(i.get("kinds", "")).split(","):
                            if k.strip() and k.strip() not in OTM_KIND_TAGS:
                                unmapped[k.strip()] += 1
    print("\nSample names with no rate filter:")
    for k, names in samples.items():
        print(f"  {k:<14}{names}")
    print("\nCategory words my code does not recognise yet (word: count):")
    print("  " + (", ".join(f"{w}: {n}" for w, n in unmapped.most_common(25)) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())