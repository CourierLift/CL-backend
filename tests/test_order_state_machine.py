import itertools

import pytest

from backend.models import OrderStatus
from backend.orders import ALLOWED_TRANSITIONS, can_transition


LEGAL_TRANSITIONS = {
    (OrderStatus.pending, OrderStatus.assigned),
    (OrderStatus.pending, OrderStatus.canceled),
    (OrderStatus.assigned, OrderStatus.picked_up),
    (OrderStatus.assigned, OrderStatus.canceled),
    (OrderStatus.picked_up, OrderStatus.in_transit),
    (OrderStatus.in_transit, OrderStatus.delivered),
}


@pytest.mark.parametrize(
    ("current", "next_status"),
    list(itertools.product(OrderStatus, repeat=2)),
)
def test_transition_matrix_is_explicit_and_exhaustive(current, next_status):
    assert can_transition(current, next_status) is ((current, next_status) in LEGAL_TRANSITIONS)


def test_transition_table_matches_contract():
    flattened = {
        (current, next_status)
        for current, allowed in ALLOWED_TRANSITIONS.items()
        for next_status in allowed
    }
    assert flattened == LEGAL_TRANSITIONS
    assert set(ALLOWED_TRANSITIONS) == set(OrderStatus)
