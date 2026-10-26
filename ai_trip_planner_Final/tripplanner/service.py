"""One function that runs the whole flow, shared by the CLI and the web API."""
import logging
from dataclasses import dataclass
from typing import List, Optional

from .config import Settings
from .explainer import FallbackExplainer, LLMExplainer, TemplateExplainer
from .http import Fetch, request_json
from .llm import client_from_settings, parse_brief_smart
from .planner import Itinerary, baseline_km, plan_trip
from .sources import get_pois

log = logging.getLogger("tripplanner")


@dataclass
class PlanResult:
    itinerary: Itinerary
    explanations: List[str]
    parser: str
    places_source: str
    explainer: str
    baseline_km: float = 0.0


def run_plan(text: str, settings: Settings, use_llm: bool = False, places_mode: str = "local",
             fetch: Fetch = request_json) -> PlanResult:
    client = client_from_settings(settings, fetch) if use_llm else None
    if use_llm and client is None:
        log.warning("LLM requested but no GROQ_API_KEY or ANTHROPIC_API_KEY is set; using offline text")
    brief, parser_used = parse_brief_smart(text, client)
    pois, source = get_pois(brief, settings, places_mode, fetch)
    itinerary = plan_trip(brief, pois)

    explainer = FallbackExplainer(LLMExplainer(client.complete)) if client else TemplateExplainer()
    notes = [explainer.explain(brief, d) for d in itinerary.days]
    used = "template" if client is None or getattr(explainer, "used_fallback", False) else "llm"
    return PlanResult(itinerary, notes, parser_used, source, used, baseline_km(brief, pois))
