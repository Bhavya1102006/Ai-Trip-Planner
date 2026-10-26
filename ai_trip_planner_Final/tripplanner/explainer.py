"""Turns a finished itinerary into readable commentary.

TemplateExplainer is fully offline. LLMExplainer shows where a language model
plugs in: it only *phrases* a plan the solver already produced, it never decides
which places are visited (this is the grounding rule from the Stage 1 report).
"""
import logging
from typing import Callable, Optional

from .data_loader import fmt_time
from .models import DayPlan, TripBrief

log = logging.getLogger("tripplanner")


class TemplateExplainer:
    def explain(self, brief: TripBrief, day: DayPlan) -> str:
        if not day.stops:
            return "No stops fit this day within the budget and opening hours."
        first, last = day.stops[0], day.stops[-1]
        tags = sorted({t for s in day.stops for t in s.poi.tags})[:4]
        text = (f"{len(day.stops)} stops between {fmt_time(first.arrive)} and "
                f"{fmt_time(last.depart)}, {day.distance_km} km of travel in total. "
                f"Spend is {day.cost} of {brief.budget_per_day} INR. Themes: {', '.join(tags)}.")
        return text


class LLMExplainer:
    """`complete` is any function (prompt, system) -> str, e.g. AnthropicClient.complete."""

    SYSTEM = ("You write short travel-day summaries. Use ONLY the facts given. Do not add, remove "
              "or rename places, times, prices or distances. Two sentences, plain language.")

    def __init__(self, complete: Optional[Callable[..., str]] = None, max_chars: int = 400):
        self.complete = complete
        self.max_chars = max_chars

    def explain(self, brief: TripBrief, day: DayPlan) -> str:
        if self.complete is None:
            raise RuntimeError("LLMExplainer needs a completion function; none configured.")
        if not day.stops:
            return TemplateExplainer().explain(brief, day)
        facts = "; ".join(f"{fmt_time(s.arrive)}-{fmt_time(s.depart)} {s.poi.name} ({s.poi.cost} INR)"
                          for s in day.stops)
        prompt = (f"Traveller interests: {', '.join(brief.interests)}. Daily budget {brief.budget_per_day} INR. "
                  f"Day {day.day} schedule: {facts}. Total travel {day.distance_km} km, spend {day.cost} INR.")
        return self.complete(prompt, system=self.SYSTEM)[: self.max_chars]


class FallbackExplainer:
    """Tries the language model; on any failure uses the offline template instead."""

    def __init__(self, primary, fallback=None):
        self.primary = primary
        self.fallback = fallback or TemplateExplainer()
        self.used_fallback = False

    def explain(self, brief: TripBrief, day: DayPlan) -> str:
        try:
            return self.primary.explain(brief, day)
        except Exception as exc:                     # network, quota, bad reply: never break the plan
            self.used_fallback = True
            log.warning("LLM explanation failed (%s); using template", type(exc).__name__)
            return self.fallback.explain(brief, day)
