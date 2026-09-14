"""Mock external supplier system.

Real suppliers confirm less than you ordered, go quiet, or come back with a
different lead time. The failure profile below is what lets us prove the
agent handles the gap between what it intended and what actually happened.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Scenario-scoped overrides, keyed by supplier id. Set by the scenario runner
# or the API so a demo can force a partial fill deterministically.
FAILURE_PROFILE: dict[str, dict] = {}


@dataclass
class SupplierResponse:
    supplier_id: str
    requested_quantity: int
    confirmed_quantity: int
    lead_time_days: int
    message: str

    @property
    def is_partial(self) -> bool:
        return 0 < self.confirmed_quantity < self.requested_quantity

    @property
    def is_rejected(self) -> bool:
        return self.confirmed_quantity == 0

    def to_dict(self) -> dict:
        return {
            "supplier_id": self.supplier_id,
            "requested_quantity": self.requested_quantity,
            "confirmed_quantity": self.confirmed_quantity,
            "lead_time_days": self.lead_time_days,
            "message": self.message,
            "is_partial": self.is_partial,
            "is_rejected": self.is_rejected,
        }


def set_failure_profile(profile: dict[str, dict]) -> None:
    """Install a deterministic failure profile, e.g.

    {"SUP-ANDINA": {"fill_rate": 0.5, "message": "Plant maintenance"}}
    """
    FAILURE_PROFILE.clear()
    FAILURE_PROFILE.update(profile or {})


def clear_failure_profile() -> None:
    FAILURE_PROFILE.clear()


def submit_order(
    supplier_id: str,
    sku: str,
    quantity: int,
    lead_time_days: int,
    reliability_score: float = 1.0,
    deterministic: bool = True,
) -> SupplierResponse:
    profile = FAILURE_PROFILE.get(supplier_id)

    if profile is not None:
        fill_rate = float(profile.get("fill_rate", 1.0))
        confirmed = int(quantity * fill_rate)
        lot = int(profile.get("round_to", 1))
        if lot > 1:
            confirmed = (confirmed // lot) * lot
        return SupplierResponse(
            supplier_id=supplier_id,
            requested_quantity=quantity,
            confirmed_quantity=max(confirmed, 0),
            lead_time_days=int(profile.get("lead_time_days", lead_time_days)),
            message=profile.get("message", "Partial allocation confirmed."),
        )

    if deterministic:
        return SupplierResponse(
            supplier_id=supplier_id,
            requested_quantity=quantity,
            confirmed_quantity=quantity,
            lead_time_days=lead_time_days,
            message="Order confirmed in full.",
        )

    if random.random() > reliability_score:
        confirmed = int(quantity * random.uniform(0.4, 0.85))
        return SupplierResponse(
            supplier_id=supplier_id,
            requested_quantity=quantity,
            confirmed_quantity=confirmed,
            lead_time_days=lead_time_days + 2,
            message="Partial allocation only; remainder unavailable this cycle.",
        )

    return SupplierResponse(
        supplier_id=supplier_id,
        requested_quantity=quantity,
        confirmed_quantity=quantity,
        lead_time_days=lead_time_days,
        message="Order confirmed in full.",
    )
