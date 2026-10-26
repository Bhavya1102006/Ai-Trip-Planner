"""Custom exceptions so callers can tell user mistakes from internal bugs."""


class PlannerError(Exception):
    """Base class for every error this package raises on purpose."""


class InvalidBriefError(PlannerError):
    """The trip brief is missing something essential or has impossible values."""


class UnknownDestinationError(PlannerError):
    """No point-of-interest data is available for the requested destination."""


class InfeasiblePlanError(PlannerError):
    """Constraints (budget, hours, pace) leave nothing that can be scheduled."""


class CloudServiceError(PlannerError):
    """A cloud service (LLM, places API, database) failed or returned something unusable."""
