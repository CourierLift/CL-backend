"""Single source of truth for Courier Lifts pricing and ETA estimates."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TransportSpec:
    base_fee: float
    per_mile: float
    speed_mph: float
    included_weight_lb: float
    excess_weight_rate: float
    environmental_adjustment: float
    max_weight_lb: float
    max_length_in: float
    max_width_in: float
    max_height_in: float
    max_volume_cu_ft: float


@dataclass(frozen=True)
class QuoteResult:
    price_total: float
    eta_min: int
    miles: float
    tier: str
    estimated: bool
    distance_source: str
    transportation_mode: str
    breakdown: dict[str, float]


TRANSPORT_SPECS: dict[str, TransportSpec] = {
    "foot": TransportSpec(3.00, 1.15, 3.0, 5, 0.14, -0.06, 15, 24, 18, 12, 2),
    "bike": TransportSpec(3.25, 1.25, 12, 8, 0.13, -0.05, 25, 30, 20, 16, 4),
    "cargo_bike": TransportSpec(4.00, 1.40, 11, 25, 0.11, -0.05, 150, 60, 36, 36, 20),
    "e_bike": TransportSpec(3.75, 1.35, 15, 12, 0.12, -0.04, 50, 36, 24, 20, 7),
    "scooter": TransportSpec(3.75, 1.40, 18, 10, 0.12, -0.02, 35, 32, 22, 18, 5),
    "motorcycle": TransportSpec(4.25, 1.55, 28, 20, 0.10, 0.00, 75, 40, 28, 24, 9),
    "car": TransportSpec(4.50, 1.70, 24, 35, 0.08, 0.00, 300, 72, 48, 40, 55),
    "ev": TransportSpec(4.50, 1.65, 24, 35, 0.08, -0.03, 300, 72, 48, 40, 55),
    "suv": TransportSpec(5.25, 1.95, 22, 75, 0.07, 0.01, 800, 84, 50, 48, 95),
    "van": TransportSpec(6.00, 2.20, 21, 150, 0.06, 0.02, 3000, 120, 70, 70, 400),
    "light_truck": TransportSpec(6.50, 2.45, 20, 200, 0.055, 0.04, 2500, 96, 72, 60, 300),
    "box_truck": TransportSpec(9.00, 3.10, 19, 500, 0.045, 0.07, 10000, 288, 96, 96, 1600),
}

TRANSPORT_ALIASES = {
    "foot": "foot",
    "walk": "foot",
    "walking": "foot",
    "bike": "bike",
    "bicycle": "bike",
    "cargo bike": "cargo_bike",
    "cargo bicycle": "cargo_bike",
    "e bike": "e_bike",
    "electric bike": "e_bike",
    "scooter": "scooter",
    "motor scooter": "scooter",
    "motorcycle": "motorcycle",
    "motorbike": "motorcycle",
    "car": "car",
    "sedan": "car",
    "ev": "ev",
    "electric vehicle": "ev",
    "electric car": "ev",
    "ev compact": "ev",
    "ev sedan": "ev",
    "suv": "suv",
    "sport utility vehicle": "suv",
    "ev suv": "suv",
    "van": "van",
    "cargo van": "van",
    "ev van": "van",
    "light truck": "light_truck",
    "pickup truck": "light_truck",
    "pickup": "light_truck",
    "truck": "light_truck",
    "box truck": "box_truck",
}

ITEM_MULTIPLIERS = {
    "standard": 1.00,
    "general": 1.00,
    "electronics": 1.12,
    "fragile": 1.18,
    "perishable": 1.10,
    "food": 1.10,
    "oversize": 1.25,
    "hazardous": 1.35,
}
WEATHER_MULTIPLIERS = {
    "clear": 1.00,
    "rain": 1.08,
    "snow": 1.18,
    "wind": 1.06,
    "extreme": 1.35,
}
TRAFFIC_MULTIPLIERS = {
    "low": 1.00,
    "light": 1.00,
    "med": 1.12,
    "medium": 1.12,
    "high": 1.30,
    "heavy": 1.30,
}


def normalize_transport_mode(value: str) -> str:
    key = (
        str(value)
        .strip()
        .lower()
        .replace("_", " ")
        .replace("-", " ")
    )
    key = " ".join(key.split())
    mode = TRANSPORT_ALIASES.get(key)
    if mode is None:
        supported = ", ".join(TRANSPORT_SPECS)
        raise ValueError(f"Unsupported transportation mode. Use one of: {supported}")
    return mode


def get_transport_spec(value: str) -> TransportSpec:
    return TRANSPORT_SPECS[normalize_transport_mode(value)]


def haversine_miles(
    pickup: tuple[float, float],
    dropoff: tuple[float, float],
) -> float:
    lat1, lon1 = pickup
    lat2, lon2 = dropoff
    radius_miles = 3958.7613
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2) ** 2
    )
    return radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def estimate_quote(
    *,
    transportation_mode: str,
    item_type: str,
    quantity: int,
    weight_lb: float,
    length_in: float,
    width_in: float,
    height_in: float,
    weather: str,
    traffic: str,
    surge: float,
    pickup: tuple[float, float] | None = None,
    dropoff: tuple[float, float] | None = None,
    development_fallback_miles: float | None = None,
) -> QuoteResult:
    """Estimate one delivery without calling maps or any external service.

    Coordinate requests use Haversine distance. Address-only development requests
    must pass an explicit fixed fallback distance; address text is never converted
    into fabricated coordinates or a price signal.
    """

    mode = normalize_transport_mode(transportation_mode)
    spec = TRANSPORT_SPECS[mode]

    if pickup is not None and dropoff is not None:
        miles = haversine_miles(pickup, dropoff)
        estimated = False
        distance_source = "coordinate_haversine"
    elif development_fallback_miles is not None and development_fallback_miles > 0:
        miles = float(development_fallback_miles)
        estimated = True
        distance_source = "development_fallback"
    else:
        raise ValueError("Coordinates or an explicit development fallback are required")

    quantity = max(1, int(quantity))
    weight_lb = max(0.0, float(weight_lb))
    length_in = max(0.1, float(length_in))
    width_in = max(0.1, float(width_in))
    height_in = max(0.1, float(height_in))
    surge = max(1.0, min(3.0, float(surge)))
    miles = round(max(0.0, miles), 2)

    item_multiplier = ITEM_MULTIPLIERS.get(item_type.strip().lower(), 1.00)
    weather_multiplier = WEATHER_MULTIPLIERS.get(weather.strip().lower(), 1.00)
    traffic_multiplier = TRAFFIC_MULTIPLIERS.get(traffic.strip().lower(), 1.12)

    total_volume_cu_ft = (
        length_in * width_in * height_in * quantity / 1728.0
    )
    distance_charge = miles * spec.per_mile
    weight_charge = (
        max(0.0, weight_lb - spec.included_weight_lb)
        * spec.excess_weight_rate
    )
    volume_charge = max(0.0, total_volume_cu_ft - 1.0) * 0.75
    quantity_charge = max(0, quantity - 1) * 0.65
    pre_conditions = (
        spec.base_fee
        + distance_charge
        + weight_charge
        + volume_charge
        + quantity_charge
    )
    conditioned = (
        pre_conditions
        * item_multiplier
        * weather_multiplier
        * traffic_multiplier
        * surge
    )
    environmental_adjustment = conditioned * spec.environmental_adjustment
    price_total = round(
        max(4.50, min(9999.00, conditioned + environmental_adjustment)),
        2,
    )
    effective_speed = max(
        2.0,
        spec.speed_mph / (weather_multiplier * traffic_multiplier),
    )
    handling_minutes = 6 + min(30, quantity * 2)
    eta_min = max(5, math.ceil((miles / effective_speed) * 60 + handling_minutes))
    tier = (
        "Saver"
        if price_total < 12
        else "Standard"
        if price_total < 35
        else "Priority"
        if price_total < 100
        else "Pro Load"
    )

    return QuoteResult(
        price_total=price_total,
        eta_min=eta_min,
        miles=miles,
        tier=tier,
        estimated=estimated,
        distance_source=distance_source,
        transportation_mode=mode,
        breakdown={
            "base_fee": round(spec.base_fee, 2),
            "distance_charge": round(distance_charge, 2),
            "weight_charge": round(weight_charge, 2),
            "volume_charge": round(volume_charge, 2),
            "quantity_charge": round(quantity_charge, 2),
            "item_multiplier": round(item_multiplier, 3),
            "weather_multiplier": round(weather_multiplier, 3),
            "traffic_multiplier": round(traffic_multiplier, 3),
            "surge_multiplier": round(surge, 3),
            "environmental_adjustment": round(environmental_adjustment, 2),
        },
    )
