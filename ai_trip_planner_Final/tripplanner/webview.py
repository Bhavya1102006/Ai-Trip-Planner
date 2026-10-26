"""Renders an itinerary as one self-contained HTML page (the 'web client' preview)."""
from html import escape

from .data_loader import fmt_time

CSS = """
body{font-family:Segoe UI,Arial,sans-serif;background:#f4f6fb;color:#1a2942;margin:0;padding:24px}
h1{font-size:22px;margin:0 0 4px} .sub{color:#555;margin-bottom:18px;font-size:14px}
.day{background:#fff;border-radius:10px;padding:14px 18px;margin-bottom:14px;box-shadow:0 1px 4px #0002}
.day h2{font-size:16px;margin:0 0 8px;color:#2f6fed}
.stop{display:flex;gap:12px;padding:6px 0;border-bottom:1px solid #eef;font-size:14px}
.time{width:112px;color:#555;font-variant-numeric:tabular-nums}.name{flex:1;font-weight:600}
.meta{color:#555;font-size:13px}.note{margin-top:8px;font-size:13px;color:#444;font-style:italic}
.badge{background:#e6f4ea;color:#1e7e34;border-radius:8px;padding:1px 7px;font-size:11px;margin-left:6px}
"""


def render_html(itinerary, notes) -> str:
    b = itinerary.brief
    parts = [f"<!doctype html><meta charset='utf-8'><title>Trip plan</title><style>{CSS}</style>",
             f"<h1>{b.days}-day trip to {escape(b.destination.title())}</h1>",
             f"<div class='sub'>Budget {b.budget_per_day} INR/day &middot; interests: "
             f"{escape(', '.join(b.interests))} &middot; pace: {escape(b.pace)} &middot; "
             f"{itinerary.stop_count} stops, {itinerary.total_km} km</div>"]
    for d, note in zip(itinerary.days, notes):
        parts.append(f"<div class='day'><h2>Day {d.day}</h2>")
        for s in d.stops:
            badge = "" if s.poi.mainstream else "<span class='badge'>local pick</span>"
            parts.append(f"<div class='stop'><div class='time'>{fmt_time(s.arrive)}&ndash;{fmt_time(s.depart)}</div>"
                         f"<div class='name'>{escape(s.poi.name)}{badge}</div>"
                         f"<div class='meta'>{s.poi.cost} INR &middot; +{s.travel_from_prev_km} km</div></div>")
        parts.append(f"<div class='note'>{escape(note)}</div></div>")
    return "".join(parts)
