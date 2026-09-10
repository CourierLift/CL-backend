"""Route-distance provider boundary for production address quotes."""

from dataclasses import dataclass


METERS_PER_MILE = 1609.344


class RouteDistanceError(RuntimeError):
    """Raised when a route-distance provider cannot return a usable route."""


@dataclass(frozen=True)
class RouteDistance:
    miles: float
    source: str
