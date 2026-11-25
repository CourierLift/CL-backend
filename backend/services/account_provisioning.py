"""Private account creation shared by the operator CLI and explicit public mode.

This is not an HTTP invitation endpoint. Email ownership is not verified.
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth_jwt import hash_password
from ..models import CourierProfile, User, UserRole
from ..schemas import RegisterIn
from .eligibility import courier_profile_defaults


class DuplicateAccountError(ValueError):
    pass


def provision_account(db: Session, payload: RegisterIn) -> User:
    """Create one validated non-admin account; never replace existing accounts."""
    # Revalidate even a constructed/mutated model; role links/extra client fields
    # are not authority. Password input is never logged or included in errors.
    payload = RegisterIn.model_validate(payload.model_dump())
    email = str(payload.email).lower()
    if db.query(User).filter(User.email == email).first() is not None:
        raise DuplicateAccountError("Email already registered")

    password_hash = hash_password(payload.password)
    user = User(email=email, password_hash=password_hash, role=UserRole(payload.role))
    db.add(user)
    if user.role == UserRole.courier:
        defaults = courier_profile_defaults(payload.transportation_mode or "car")
        db.add(CourierProfile(
            user=user,
            transportation_mode=str(defaults["transportation_mode"]),
            max_weight_lb=payload.max_weight_lb or float(defaults["max_weight_lb"]),
            max_length_in=payload.max_length_in or float(defaults["max_length_in"]),
            max_width_in=payload.max_width_in or float(defaults["max_width_in"]),
            max_height_in=payload.max_height_in or float(defaults["max_height_in"]),
            max_volume_cu_ft=payload.max_volume_cu_ft or float(defaults["max_volume_cu_ft"]),
            capabilities=payload.capabilities,
        ))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateAccountError("Email already registered") from exc
    db.refresh(user)
    return user
