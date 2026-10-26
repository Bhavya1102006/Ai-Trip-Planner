"""Command-line entry point:  python -m tripplanner "3 days in Jaipur, budget 3000 ..." """
import argparse
import json
import logging
import sys

from .config import Settings
from .data_loader import fmt_time
from .errors import PlannerError
from .service import run_plan
from .webview import render_html


def render(it, notes) -> str:
    b = it.brief
    out = [f"TRIP: {b.days} day(s) in {b.destination.title()} | budget {b.budget_per_day} INR/day"
           f" | interests: {', '.join(b.interests)} | pace: {b.pace}", ""]
    for d, note in zip(it.days, notes):
        out.append(f"DAY {d.day}")
        for s in d.stops:
            out.append(f"  {fmt_time(s.arrive)}-{fmt_time(s.depart)}  {s.poi.name:<34} "
                       f"{s.poi.cost:>5} INR   +{s.travel_from_prev_km:>4} km")
        out.append(f"  -> {note}")
        out.append("")
    empty = [d.day for d in it.days if not d.stops]
    if empty:
        out.append(f"NOTE: day(s) {', '.join(map(str, empty))} have nothing left that matches your "
                   f"interests - try adding more interests or shortening the trip.")
    if it.unscheduled:
        out.append(f"NOT SCHEDULED (no room within hours/budget/pace): {', '.join(it.unscheduled)}")
    out.append(f"TOTAL: {it.stop_count} stops, {it.total_km} km")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tripplanner")
    ap.add_argument("brief", help="free-text trip brief")
    ap.add_argument("--json", action="store_true", help="print machine-readable output")
    ap.add_argument("--live", action="store_true", help="fetch places from Google Places / OpenStreetMap")
    ap.add_argument("--llm", action="store_true", help="use the language model to parse the brief and write summaries")
    ap.add_argument("--html", metavar="FILE", help="also write an HTML preview page")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args(argv)

    level = logging.WARNING - 10 * min(args.verbose, 2)
    logging.basicConfig(level=level, stream=sys.stderr,
                        format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    if args.live:
        print("Fetching live places (the free map service can take up to a minute)...", file=sys.stderr)
    try:
        result = run_plan(args.brief, Settings.from_env(), use_llm=args.llm,
                          places_mode="live" if args.live else "local")
        it, brief = result.itinerary, result.itinerary.brief
        print(f"[parser: {result.parser} | places: {result.places_source}]", file=sys.stderr)
    except PlannerError as exc:
        print(f"Could not plan trip: {exc}", file=sys.stderr)
        return 2

    if args.html:
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(render_html(it, result.explanations))

    if args.json:
        print(json.dumps({"days": [{"day": d.day, "km": d.distance_km, "cost": d.cost,
                                    "stops": [s.poi.name for s in d.stops]} for d in it.days],
                          "baseline_km": result.baseline_km}, indent=2))
    else:
        print(render(it, result.explanations))
    return 0


if __name__ == "__main__":
    sys.exit(main())
