"""Compares the planner against a naive baseline on several sample briefs.

Outputs distance saved, estimated CO2 saved, budget use and the share of
non-mainstream (smaller / lower-footfall) places. Used for the SDG metrics table.
"""
import json
import sys
from tripplanner.data_loader import load_pois
from tripplanner.parser import parse_brief
from tripplanner.planner import baseline_km, plan_trip

CO2_KG_PER_KM = 0.15      # assumption: small petrol car / cab, approx. 150 g CO2 per km
BRIEFS = [
    "2 days in Jaipur, budget 2500 per day, heritage and photography",
    "3 days in Jaipur, budget 3000 per day, heritage and food, relaxed pace",
    "3 days in Jaipur, budget 2000 per day, nature, spiritual and art",
    "4 days in Jaipur, budget 3500 per day, food, shopping and nightlife, packed pace",
    "2 days in Jaipur, budget 1500 per day, art and culture, relaxed pace",
    "5 days in Jaipur, budget 4000 per day, heritage, food and nature",
]


def main():
    pois = load_pois("jaipur")
    rows = []
    for text in BRIEFS:
        b = parse_brief(text)
        it = plan_trip(b, pois)
        base = baseline_km(b, pois)
        stops = [s.poi for d in it.days for s in d.stops]
        non_main = sum(1 for p in stops if not p.mainstream)
        rows.append({
            "brief": text, "stops": len(stops), "planner_km": it.total_km, "baseline_km": base,
            "saved_km": round(base - it.total_km, 2),
            "saved_pct": round(100 * (base - it.total_km) / base, 1) if base else 0.0,
            "co2_saved_kg": round((base - it.total_km) * CO2_KG_PER_KM, 2),
            "non_mainstream_pct": round(100 * non_main / len(stops), 1) if stops else 0.0,
            "avg_budget_use_pct": round(100 * sum(d.cost for d in it.days) / (b.budget_per_day * b.days), 1),
        })
    print(json.dumps(rows, indent=2))
    n = len(rows)
    print("\nMEAN saved_pct = %.1f | MEAN non_mainstream_pct = %.1f | TOTAL co2_saved_kg = %.2f" % (
        sum(r["saved_pct"] for r in rows) / n,
        sum(r["non_mainstream_pct"] for r in rows) / n,
        sum(r["co2_saved_kg"] for r in rows)), file=sys.stderr)


if __name__ == "__main__":
    main()
