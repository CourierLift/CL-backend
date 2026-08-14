"""Courier eligibility rules for marketplace discovery and claims."""

from dataclasses import dataclass

from ..models import CourierProfile, Order, User, UserRole
from ..quote_engine import get_transport_spec, normalize_transport_mode


COMPATIBLE_COURIER_MODES: dict[str, set[str]] = {
    "foot": {"foot"},
    "bike": {"bike", "cargo_bike", "e_bike"},
    "cargo_bike": {"cargo_bike"},
    "e_bike": {"e_bike", "cargo_bike"},
    "scooter": {"scooter", "motorcycle"},
    "motorcycle": {"motorcycle"},
    "car": {"car", "ev", "suv", "van", "light_truck", "box_truck"},
    "ev": {"ev"},
    "suv": {"suv", "van", "light_truck", "box_truck"},
    "van": {"van", "box_truck"},
    "light_truck": {"light_truck", "box_truck"},
    "box_truck": {"box_truck"},
}

ITEM_REQUIREMENTS = {
    "fragile": "fragile",
    "glass": "fragile",
    "art": "fragile",
    "perishable": "perishable",
    "food": "perishable",
    "grocery": "perishable",
    "hazardous": "hazardous",
    "oversize": "oversize",
}


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reasons: tuple[str, ...]


def courier_profile_defaults(mode: str) -> dict[str, float | str]:
    normalized_mode = normalize_transport_mode(mode)
    spec = get_transport_spec(normalized_mode)
    return {
        "transportation_mode": normalized_mode,
        "max_weight_lb": spec.max_weight_lb,
        "max_length_in": spec.max_length_in,
        "max_width_in": spec.max_width_in,
        "max_height_in": spec.max_height_in,
        "max_volume_cu_ft": spec.max_volume_cu_ft,
    }


def _required_capabilities(order: Order) -> set[str]:
    requirements = {
        str(value).strip().lower().replace(" ", "_")
        for value in (order.delivery_requirements or [])
    }
    item_type = (order.item_type or "").lower()
    for marker, requirement in ITEM_REQUIREMENTS.items():
        if marker in item_type:
            requirements.add(requirement)
    return requirements


def evaluate_courier_eligibility(courier: User, order: Order) -> EligibilityResult:
    reasons: list[str] = []
    if courier.role != UserRole.courier:
        reasons.append("Only couriers may claim deliveries")
        return EligibilityResult(False, tuple(reasons))

    profile: CourierProfile | None = courier.courier_profile
    if profile is None or not profile.is_active:
        reasons.append("Courier profile is missing or inactive")
        return EligibilityResult(False, tuple(reasons))

    courier_mode = normalize_transport_mode(profile.transportation_mode)
    required_mode = normalize_transport_mode(order.vehicle)
    if courier_mode not in COMPATIBLE_COURIER_MODES[required_mode]:
        reasons.append(
            f"Transportation mode {courier_mode} cannot serve {required_mode} deliveries"
        )

    if order.weight_lb > profile.max_weight_lb:
        reasons.append(
            f"Weight {order.weight_lb:g} lb exceeds courier limit {profile.max_weight_lb:g} lb"
        )
    if order.length_in > profile.max_length_in:
        reasons.append("Item length exceeds courier capacity")
    if order.width_in > profile.max_width_in:
        reasons.append("Item width exceeds courier capacity")
    if order.height_in > profile.max_height_in:
        reasons.append("Item height exceeds courier capacity")

    total_volume_cu_ft = (
        order.length_in * order.width_in * order.height_in * order.quantity / 1728.0
    )
    if total_volume_cu_ft > profile.max_volume_cu_ft:
        reasons.append("Total delivery volume exceeds courier capacity")

    required_capabilities = _required_capabilities(order)
    courier_capabilities = {
        str(value).strip().lower().replace(" ", "_")
        for value in (profile.capabilities or [])
    }
    missing = sorted(required_capabilities - courier_capabilities)
    if missing:
        reasons.append(f"Missing delivery capabilities: {', '.join(missing)}")

    return EligibilityResult(not reasons, tuple(reasons))
