"""Turns a free-text trip brief into a validated TripBrief.

This is the rule-based stand-in for the LLM preference parser described in
Stage 1. It is deliberately simple and deterministic so it can be unit tested;
an LLM-backed parser can be swapped in later behind the same function signature.
"""
import logging
import re
from .data_loader import AVAILABLE
from .errors import InvalidBriefError
from .models import TripBrief

MAX_DAYS = 7
MIN_BUDGET = 500          # INR per day, below this nothing sensible fits

# canonical interest -> words a user might type
INTEREST_WORDS = {
    "heritage": ["heritage", "history", "historical", "forts", "palace", "palaces"],
    "food": ["food", "foodie", "eat", "eating", "street food", "cuisine", "sweets"],
    "shopping": ["shopping", "shop", "bazaar", "market", "markets", "handicraft"],
    "nature": ["nature", "garden", "gardens", "outdoors", "park"],
    "art": ["art", "museum", "museums", "culture", "cultural"],
    "spiritual": ["spiritual", "temple", "temples", "religious"],
    "photography": ["photography", "photos", "photo", "scenic", "views"],
    "nightlife": ["nightlife", "night", "cafe", "cafes"],
}
PACE_WORDS = {"relaxed": ["relaxed", "slow", "easy", "chill"],
              "packed": ["packed", "fast", "busy", "maximum", "intense"]}

log = logging.getLogger("tripplanner")


def parse_brief(text: str) -> TripBrief:
    if not isinstance(text, str) or not text.strip():
        raise InvalidBriefError("The trip brief is empty.")
    low = text.lower()

    dest = None
    # Check known destinations first, longest names first
    for d in sorted(AVAILABLE.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(d)}\b", low):
            dest = d
            break

    if dest is None:
        m = re.search(r"\bin\s+([a-z][a-z ]{2,30}?)(?:,|\.|\bwith\b|\bbudget\b|$)", low)
        dest = m.group(1).strip() if m else None
    if not dest:
        raise InvalidBriefError("Could not find a destination (try 'N days in <city>').")

    m = re.search(r"(\d+)\s*(?:-)?\s*day", low)
    if not m:
        raise InvalidBriefError("Could not find trip length (try '3 days').")
    days = int(m.group(1))
    if not 1 <= days <= MAX_DAYS:
        raise InvalidBriefError(f"Trip length must be 1-{MAX_DAYS} days, got {days}.")

    m = re.search(r"(?:budget|rs\.?|inr|₹)\s*(?:of|is|:)?\s*(?:rs\.?|inr|₹)?\s*(\d[\d,]*)", low)
    if not m:
        m = re.search(r"(\d[\d,]*)\s*(?:rs|inr|rupees|₹)", low)
    if not m:
        raise InvalidBriefError("Could not find a budget (try 'budget 3000 per day').")
    budget = int(m.group(1).replace(",", ""))
    if re.search(r"total|overall|in all", low):     # user gave a trip total, not per day
        budget = budget // days

    interests = [k for k, words in INTEREST_WORDS.items()
                 if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words)]
    pace = "moderate"
    for p, words in PACE_WORDS.items():
        if any(w in low for w in words):
            pace = p
    return build_brief(dest, days, budget, interests, pace)


PACES = ("relaxed", "moderate", "packed")


def build_brief(destination, days, budget, interests, pace="moderate") -> TripBrief:
    """Single place where a brief is validated, whether it came from the rule parser
    or from a language model. Nothing reaches the planner without passing through here."""
    if not isinstance(destination, str) or not destination.strip() or len(destination) > 60:
        raise InvalidBriefError("Destination is missing or too long.")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise InvalidBriefError(f"Trip length must be 1-{MAX_DAYS} days, got {days}.")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < MIN_BUDGET:
        raise InvalidBriefError(f"Budget {budget} INR/day is too low (minimum {MIN_BUDGET}).")
    known = [i for i in (interests or []) if i in INTEREST_WORDS]
    if not known:
        log.warning("no recognised interests in the brief; defaulting to heritage + food")
        known = ["heritage", "food"]                # sensible default, documented in report
    if pace not in PACES:
        pace = "moderate"
    return TripBrief(destination.strip().lower(), days, budget, list(dict.fromkeys(known)), pace)
