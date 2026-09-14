"""Deterministic replenishment mathematics.

The LLM never does arithmetic here. It decides *what to do*; this module
decides *what the numbers say*. Keeping the two apart is what makes the
agent's output checkable — and it means a wrong recommendation from the
upstream planner gets caught by computation, not by vibes.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field


@dataclass
class SupplierTerms:
    supplier_id: str
    unit_price: float
    moq: int
    lot_size: int
    lead_time_days: int
    max_units_per_order: int
    reliability_score: float = 1.0


@dataclass
class DemandView:
    forecast_daily: float
    actual_daily_7d: float
    actual_daily_28d: float
    std_dev: float = 0.0

    @property
    def drift(self) -> float:
        """Relative deviation of recent actuals from the forecast."""
        if self.forecast_daily <= 0:
            return 0.0
        return (self.actual_daily_7d - self.forecast_daily) / self.forecast_daily


@dataclass
class ConstraintHit:
    name: str
    binding: bool
    detail: str
    limit_units: int | None = None


@dataclass
class ReorderResult:
    demand_rate_used: float
    cover_horizon_days: int
    target_position: int
    current_position: int
    raw_need: int
    recommended_quantity: int
    order_value: float
    constraints: list[ConstraintHit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def binding_constraints(self) -> list[str]:
        return [c.name for c in self.constraints if c.binding]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["binding_constraints"] = self.binding_constraints
        return payload


def _round_to_lot(qty: int, lot_size: int) -> int:
    if lot_size <= 1:
        return max(0, int(qty))
    return int(math.ceil(qty / lot_size) * lot_size)


def _floor_to_lot(qty: int, lot_size: int) -> int:
    if lot_size <= 1:
        return max(0, int(qty))
    return int(math.floor(qty / lot_size) * lot_size)


def demand_rate(demand: DemandView, basis: str) -> float:
    if basis == "max_of_forecast_and_recent_7d":
        return max(demand.forecast_daily, demand.actual_daily_7d)
    if basis == "forecast_only":
        return demand.forecast_daily
    if basis == "recent_7d_only":
        return demand.actual_daily_7d
    return max(demand.forecast_daily, demand.actual_daily_7d)


def compute_reorder(
    *,
    on_hand: int,
    reserved: int,
    incoming: int,
    safety_stock: int,
    demand: DemandView,
    terms: SupplierTerms,
    policy: dict,
    budget_remaining: float,
    storage_free_units: float,
    unit_volume: float = 1.0,
    is_perishable: bool = False,
    shelf_life_days: int = 365,
) -> ReorderResult:
    """Compute the quantity policy says should be ordered, then clamp it.

    Order of operations matters. We compute the unconstrained need first so
    the agent can always explain the gap between what was needed and what is
    actually orderable — that gap is the thing a buyer wants to see.
    """
    repl = policy["replenishment"]
    cons = policy["constraints"]

    rate = demand_rate(demand, repl["demand_basis"])
    horizon = terms.lead_time_days + repl["review_period_days"]

    target_position = int(round(rate * horizon + safety_stock))
    current_position = on_hand - reserved + incoming
    raw_need = target_position - current_position

    result = ReorderResult(
        demand_rate_used=round(rate, 2),
        cover_horizon_days=horizon,
        target_position=target_position,
        current_position=current_position,
        raw_need=raw_need,
        recommended_quantity=0,
        order_value=0.0,
    )

    if abs(demand.drift) >= repl["forecast_drift_threshold"]:
        direction = "above" if demand.drift > 0 else "below"
        result.notes.append(
            f"Recent 7d actuals run {abs(demand.drift) * 100:.0f}% {direction} forecast; "
            f"planning against {rate:.1f} units/day."
        )

    if raw_need <= 0:
        result.notes.append(
            "Current position already covers the horizon; no purchase is required."
        )
        return result

    min_gap = rate * repl["min_order_days_of_cover"]
    if raw_need < min_gap:
        result.notes.append(
            f"Gap of {raw_need} units is under the {repl['min_order_days_of_cover']}-day "
            "minimum order threshold; defer to the next cycle."
        )
        return result

    qty = raw_need

    # Perishability cap comes before MOQ: buying a legal MOQ of milk that
    # expires on the shelf is still the wrong answer.
    if is_perishable:
        max_cover_days = shelf_life_days * repl["perishable_cover_fraction"]
        perishable_cap = int(rate * max_cover_days) - max(current_position, 0)
        perishable_cap = max(perishable_cap, 0)
        binding = perishable_cap < qty
        result.constraints.append(
            ConstraintHit(
                name="perishable_cover",
                binding=binding,
                detail=(
                    f"Shelf life {shelf_life_days}d allows at most "
                    f"{max_cover_days:.0f}d of cover ({perishable_cap} units)."
                ),
                limit_units=perishable_cap,
            )
        )
        qty = min(qty, perishable_cap)

    if cons["respect_supplier_max_order"]:
        binding = terms.max_units_per_order < qty
        result.constraints.append(
            ConstraintHit(
                name="supplier_max_order",
                binding=binding,
                detail=f"Supplier caps a single order at {terms.max_units_per_order} units.",
                limit_units=terms.max_units_per_order,
            )
        )
        qty = min(qty, terms.max_units_per_order)

    if cons["respect_budget"] and terms.unit_price > 0:
        budget_cap = int(budget_remaining // terms.unit_price)
        binding = budget_cap < qty
        result.constraints.append(
            ConstraintHit(
                name="budget",
                binding=binding,
                detail=(
                    f"Remaining budget {budget_remaining:,.2f} at {terms.unit_price:.2f}/unit "
                    f"affords {budget_cap} units."
                ),
                limit_units=budget_cap,
            )
        )
        qty = min(qty, budget_cap)

    if cons["respect_storage"] and unit_volume > 0:
        usable_storage = storage_free_units * (1 - cons["storage_buffer_fraction"])
        storage_cap = int(usable_storage // unit_volume)
        binding = storage_cap < qty
        result.constraints.append(
            ConstraintHit(
                name="storage",
                binding=binding,
                detail=(
                    f"{storage_free_units:,.0f} free units of storage after buffer "
                    f"allows {storage_cap} units of this SKU."
                ),
                limit_units=storage_cap,
            )
        )
        qty = min(qty, storage_cap)

    qty = max(int(qty), 0)

    # Lot rounding: round up toward need, but never past a binding cap.
    if cons["respect_lot_size"] and terms.lot_size > 1:
        rounded_up = _round_to_lot(qty, terms.lot_size)
        caps = [c.limit_units for c in result.constraints if c.binding and c.limit_units is not None]
        ceiling = min(caps) if caps else None
        if ceiling is not None and rounded_up > ceiling:
            qty = _floor_to_lot(qty, terms.lot_size)
        else:
            qty = rounded_up

    if cons["respect_moq"] and 0 < qty < terms.moq:
        # Only lift to MOQ if the caps still allow it; otherwise this SKU
        # cannot be bought from this supplier this cycle.
        caps = [c.limit_units for c in result.constraints if c.binding and c.limit_units is not None]
        ceiling = min(caps) if caps else None
        if ceiling is not None and terms.moq > ceiling:
            result.constraints.append(
                ConstraintHit(
                    name="moq_unreachable",
                    binding=True,
                    detail=(
                        f"Supplier MOQ of {terms.moq} exceeds the binding cap of {ceiling} units; "
                        "this supplier cannot be used this cycle."
                    ),
                    limit_units=0,
                )
            )
            qty = 0
        else:
            result.constraints.append(
                ConstraintHit(
                    name="moq_lift",
                    binding=True,
                    detail=f"Need of {raw_need} lifted to supplier MOQ of {terms.moq}.",
                    limit_units=terms.moq,
                )
            )
            qty = terms.moq

    result.recommended_quantity = int(qty)
    result.order_value = round(qty * terms.unit_price, 2)
    return result
