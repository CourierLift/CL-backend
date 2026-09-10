import asyncio

import httpx
import pytest

from backend.quote_engine import estimate_quote
from backend.services.route_distance import (
    GOOGLE_ROUTES_URL,
    RouteDistanceError,
    google_route_distance,
    google_travel_mode,
)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("foot", "WALK"),
        ("bike", "BICYCLE"),
        ("e-bike", "BICYCLE"),
        ("motorcycle", "TWO_WHEELER"),
        ("scooter", "TWO_WHEELER"),
        ("car", "DRIVE"),
        ("suv", "DRIVE"),
        ("cargo van", "DRIVE"),
        ("pickup truck", "DRIVE"),
        ("box truck", "DRIVE"),
    ],
)
def test_google_travel_mode_maps_existing_transport_modes(mode, expected):
    assert google_travel_mode(mode) == expected


def test_google_route_distance_requests_only_distance(monkeypatch):
    captured = {}

    async def fake_post(self, url, *, json, headers):
        captured.update(url=url, json=json, headers=headers)
        return httpx.Response(
            200,
            json={"routes": [{"distanceMeters": 16093.44}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    route = asyncio.run(
        google_route_distance(
            origin="100 Main Street, Austin, TX",
            destination="200 Oak Avenue, Bastrop, TX",
            transportation_mode="car",
            api_key="test-key",
        )
    )

    assert route.miles == 10.0
    assert route.source == "google_routes"
    assert captured["url"] == GOOGLE_ROUTES_URL
    assert captured["json"]["origin"] == {"address": "100 Main Street, Austin, TX"}
    assert captured["json"]["destination"] == {"address": "200 Oak Avenue, Bastrop, TX"}
    assert captured["json"]["travelMode"] == "DRIVE"
    assert captured["json"]["routingPreference"] == "TRAFFIC_UNAWARE"
    assert captured["headers"]["X-Goog-Api-Key"] == "test-key"
    assert captured["headers"]["X-Goog-FieldMask"] == "routes.distanceMeters"


def test_google_route_distance_rejects_provider_failure(monkeypatch):
    async def fake_post(self, url, *, json, headers):
        return httpx.Response(
            403,
            json={"error": {"message": "forbidden"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(RouteDistanceError, match="request failed"):
        asyncio.run(
            google_route_distance(
                origin="100 Main Street",
                destination="200 Oak Avenue",
                transportation_mode="car",
                api_key="test-key",
            )
        )


def test_canonical_pricing_accepts_google_distance_without_changing_formula():
    quote = estimate_quote(
        transportation_mode="car",
        item_type="standard",
        quantity=1,
        weight_lb=10,
        length_in=12,
        width_in=8,
        height_in=6,
        weather="clear",
        traffic="low",
        surge=1.0,
        authoritative_distance_miles=10.0,
        authoritative_distance_source="google_routes",
    )

    assert quote.miles == 10.0
    assert quote.distance_source == "google_routes"
    assert quote.estimated is False
    assert quote.breakdown["distance_charge"] == 17.0
