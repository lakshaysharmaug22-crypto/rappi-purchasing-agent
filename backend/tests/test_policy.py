"""Unit tests for the parts that must never be wrong.

The agent is allowed to be judged probabilistically. The arithmetic and the
authorisation layer are not — if these break, every decision above them is
built on sand.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.db.seed import reset_and_seed  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.domain.evidence import load_policy  # noqa: E402
from app.domain.reorder import DemandView, SupplierTerms, compute_reorder  # noqa: E402
from app.tools.guardrail import authorise_purchase  # noqa: E402


@pytest.fixture()
def db():
    session = SessionLocal()
    reset_and_seed(session)
    yield session
    session.close()


def _terms(**kw) -> SupplierTerms:
    base = dict(supplier_id="SUP-TEST", unit_price=2.0, moq=100, lot_size=50,
                lead_time_days=5, max_units_per_order=10000)
    base.update(kw)
    return SupplierTerms(**base)


def _compute(**kw):
    base = dict(
        on_hand=0, reserved=0, incoming=0, safety_stock=0,
        demand=DemandView(forecast_daily=100, actual_daily_7d=100, actual_daily_28d=100),
        terms=_terms(), policy=load_policy(),
        budget_remaining=1_000_000, storage_free_units=1_000_000,
    )
    base.update(kw)
    return compute_reorder(**base)


def test_covered_position_orders_nothing():
    r = _compute(on_hand=5000)
    assert r.recommended_quantity == 0
    assert r.raw_need < 0


def test_incoming_stock_counts_toward_position():
    without = _compute(on_hand=500)
    with_incoming = _compute(on_hand=500, incoming=400)
    assert with_incoming.raw_need == without.raw_need - 400


def test_reserved_stock_does_not_count():
    free = _compute(on_hand=1000)
    reserved = _compute(on_hand=1000, reserved=300)
    assert reserved.raw_need == free.raw_need + 300


def test_plans_against_the_higher_of_forecast_and_actuals():
    spiking = _compute(
        demand=DemandView(forecast_daily=100, actual_daily_7d=250, actual_daily_28d=110)
    )
    assert spiking.demand_rate_used == 250
    assert any("above forecast" in n for n in spiking.notes)


def test_quantity_is_always_a_multiple_of_lot_size():
    r = _compute(on_hand=137, terms=_terms(lot_size=50, moq=0))
    assert r.recommended_quantity % 50 == 0


def test_small_need_is_lifted_to_moq():
    r = _compute(on_hand=950, terms=_terms(moq=400, lot_size=100))
    assert r.raw_need > 0
    assert r.recommended_quantity == 400
    assert "moq_lift" in r.binding_constraints


def test_budget_caps_the_order_and_is_reported():
    r = _compute(budget_remaining=500.0, terms=_terms(unit_price=2.0, moq=0, lot_size=1))
    assert r.recommended_quantity <= 250
    assert "budget" in r.binding_constraints


def test_storage_cap_accounts_for_unit_volume():
    bulky = _compute(storage_free_units=1000, unit_volume=4.0,
                     terms=_terms(moq=0, lot_size=1))
    slim = _compute(storage_free_units=1000, unit_volume=1.0,
                    terms=_terms(moq=0, lot_size=1))
    assert bulky.recommended_quantity < slim.recommended_quantity
    assert "storage" in bulky.binding_constraints


def test_perishable_cover_is_capped_before_moq():
    r = _compute(
        demand=DemandView(forecast_daily=100, actual_daily_7d=100, actual_daily_28d=100),
        is_perishable=True, shelf_life_days=10, terms=_terms(moq=0, lot_size=1),
    )
    # 10 days shelf life at 60% cover allows 6 days of demand.
    assert r.recommended_quantity <= 600
    assert "perishable_cover" in r.binding_constraints


def test_unreachable_moq_returns_zero_rather_than_an_illegal_order():
    r = _compute(budget_remaining=100.0, terms=_terms(unit_price=2.0, moq=500, lot_size=50))
    assert r.recommended_quantity == 0
    assert "moq_unreachable" in r.binding_constraints


def test_tiny_gap_is_deferred_not_ordered():
    r = _compute(on_hand=1195, terms=_terms(moq=0, lot_size=1))
    assert r.recommended_quantity == 0
    assert any("minimum order threshold" in n for n in r.notes)


# --------------------------------------------------------------------- guardrail


def _authorise(db, **kw):
    base = dict(sku="SKU-CAF-003", node_id="TURBO-BOG-01", supplier_id="SUP-ANDINA",
                quantity=550, period="2026-09", confidence=0.95, rubric_all_pass=True)
    base.update(kw)
    return authorise_purchase(db, **base)


def test_guardrail_allows_a_legal_order(db):
    assert _authorise(db).allowed is True


def test_guardrail_blocks_below_moq(db):
    guard = _authorise(db, quantity=50)
    assert guard.allowed is False
    assert any(v.startswith("below_moq") for v in guard.violations)


def test_guardrail_blocks_off_lot_quantities(db):
    guard = _authorise(db, quantity=555)
    assert guard.allowed is False
    assert any("lot_size" in v for v in guard.violations)


def test_guardrail_blocks_orders_beyond_budget(db):
    guard = _authorise(db, quantity=3000, node_id="TURBO-MDE-02")
    assert guard.allowed is False
    assert any(v.startswith("exceeds_budget") for v in guard.violations)


def test_guardrail_rejects_a_supplier_that_does_not_list_the_sku(db):
    guard = _authorise(db, sku="SKU-LEC-004", supplier_id="SUP-CARIBE", quantity=300)
    assert guard.allowed is False
    assert "supplier_inactive_or_does_not_list_sku" in guard.violations


def test_low_confidence_is_legal_but_never_autonomous(db):
    guard = _authorise(db, confidence=0.4)
    assert guard.allowed is True
    assert guard.requires_human is True


def test_failed_rubric_forces_human_review(db):
    guard = _authorise(db, rubric_all_pass=False)
    assert guard.requires_human is True


def test_large_orders_are_never_autonomous(db):
    guard = _authorise(db, quantity=1000, confidence=0.99)
    assert guard.requires_human is True
    assert any("autonomous limit" in r for r in guard.reasons)
