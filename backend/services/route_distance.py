"""Production route-distance providers for address-based Courier Lifts quotes."""

from dataclasses import dataclass

import httpx

from ..quote_engine import normalize_transport_mode


GOOGLE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
METERS_PER_MILE = 1609.344


class RouteDistanceError(RuntimeError):
    """Raised when a configured route-distance provider cannot return a route."""


@dataclass(frozen=True)
class RouteDistance:
    miles: float
    source: str


def google_travel_mode(transportation_mode: str) -> str:
    """Map Courier Lifts transport modes to Google Routes travel modes."""
    mode = normalize_transport_mode(transportation_mode)
    if mode == "foot":
        return "WALK"
    if mode in {"bike", "cargo_bike", "e_bike"}:
        return "BICYCLE"
    if mode in {"scooter", "motorcycle"}:
        return "TWO_WHEELER"
    return "DRIVE"


async def google_route_distance(
    *,
    origin: str,
    destination: str,
    transportation_mode: str,
    api_key: str,
    timeout_seconds: float = 8.0,
) -> RouteDistance:
    """Return route distance from Google without changing Courier Lifts pricing."""
    origin = origin.strip()
    destination = destination.strip()
    api_key = api_key.strip()
    if not origin or not destination:
        raise RouteDistanceError("origin and destination are required")
    if not api_key:
        raise RouteDistanceError("Google Routes API key is not configured")

    travel_mode = google_travel_mode(transportation_mode)
    payload = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": travel_mode,
        "computeAlternativeRoutes": False,
        "languageCode": "en-US",
        "units": "IMPERIAL",
    }
    if travel_mode in {"DRIVE", "TWO_WHEELER"}:
        payload["routingPreference"] = "TRAFFIC_UNAWARE"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.distanceMeters",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(GOOGLE_ROUTES_URL, json=payload, headers=headers)
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        raise RouteDistanceError("Google Routes request failed") from exc

    try:
        data = response.json()
        distance_meters = float(data["routes"][0]["distanceMeters"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RouteDistanceError("Google Routes returned no usable route") from exc

    if distance_meters <= 0:
        raise RouteDistanceError("Google Routes returned an invalid route distance")

    return RouteDistance(
        miles=round(distance_meters / METERS_PER_MILE, 2),
        source="google_routes",
    )
