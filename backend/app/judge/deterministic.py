"""Deterministic half of the judge.

These checks run in Python against the evidence and the proposed action.
An LLM cannot argue its way past them, and they produce the same verdict
on the same inputs every time — which is what makes the evaluation set
meaningful rather than decorative.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.evidence import load_policy, load_rubric
from ..domain.reorder import ReorderResult


@dataclass
class CheckResult:
    id: str
    passed: bool
    severity: str
    detail: str

    def to_dict(self) -> dict:
        return {"id": self.id, "passed": self.passed, "severity": self.severity,
                "detail": self.detail}


@dataclass
class DeterministicVerdict:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def blocking_failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == "blocking"]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == "warning"]

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "all_passed": self.all_passed,
            "blocking_failures": [c.id for c in self.blocking_failures],
            "warnings": [c.id for c in self.warnings],
        }


REQUIRED_EVIDENCE = ("product", "inventory", "demand", "open_pos", "suppliers",
                     "budget", "storage")


def evaluate(
    *,
    evidence: dict,
    supplier: dict | None,
    quantity: int,
    computed: ReorderResult,
    existing_open_pos: list[dict] | None = None,
) -> DeterministicVerdict:
    rubric = load_rubric()
    policy = load_policy()
    severities = {c["id"]: c["severity"] for c in rubric["deterministic"]}
    tolerances = {c["id"]: c.get("tolerance") for c in rubric["deterministic"]}
    verdict = DeterministicVerdict()

    def add(check_id: str, passed: bool, detail: str) -> None:
        verdict.checks.append(
            CheckResult(check_id, passed, severities.get(check_id, "blocking"), detail)
        )

    present = [k for k in REQUIRED_EVIDENCE if k in evidence and not evidence[k].get("error")]
    add(
        "evidence_complete",
        len(present) == len(REQUIRED_EVIDENCE),
        f"{len(present)}/{len(REQUIRED_EVIDENCE)} evidence sources retrieved.",
    )

    add(
        "quantity_non_negative",
        isinstance(quantity, int) and quantity >= 0,
        f"Quantity is {quantity}.",
    )

    if supplier is None:
        add("supplier_active", False, "No active supplier lists this SKU.")
        return verdict

    add(
        "supplier_active",
        True,
        f"{supplier['supplier_name']} is active and lists the SKU.",
    )

    if quantity == 0:
        add("moq_respected", True, "No order placed, so MOQ does not apply.")
        add("lot_size_respected", True, "No order placed.")
        add("supplier_max_respected", True, "No order placed.")
        add("budget_respected", True, "No spend committed.")
        add("storage_respected", True, "No volume added.")
        add("perishable_cover_respected", True, "No stock added.")
    else:
        add(
            "moq_respected",
            quantity >= supplier["moq"],
            f"Quantity {quantity} against MOQ {supplier['moq']}.",
        )
        lot = supplier["lot_size"]
        add(
            "lot_size_respected",
            lot <= 1 or quantity % lot == 0,
            f"Quantity {quantity} against lot size {lot}.",
        )
        add(
            "supplier_max_respected",
            quantity <= supplier["max_units_per_order"],
            f"Quantity {quantity} against supplier cap {supplier['max_units_per_order']}.",
        )

        order_value = quantity * supplier["unit_price"]
        remaining = evidence["budget"]["remaining_amount"]
        add(
            "budget_respected",
            order_value <= remaining,
            f"Order value {order_value:,.2f} against remaining budget {remaining:,.2f}.",
        )

        buffer = policy["constraints"]["storage_buffer_fraction"]
        usable = evidence["storage"]["free_units"] * (1 - buffer)
        needed = quantity * evidence["product"]["unit_volume"]
        add(
            "storage_respected",
            needed <= usable,
            f"Requires {needed:,.0f} storage units against {usable:,.0f} usable.",
        )

        product = evidence["product"]
        if product["is_perishable"]:
            rate = computed.demand_rate_used
            max_days = product["shelf_life_days"] * policy["replenishment"]["perishable_cover_fraction"]
            resulting_cover = (computed.current_position + quantity) / rate if rate else 0
            add(
                "perishable_cover_respected",
                resulting_cover <= max_days + 1e-6,
                f"Resulting cover {resulting_cover:.1f}d against {max_days:.1f}d allowed.",
            )
        else:
            add("perishable_cover_respected", True, "SKU is not perishable.")

    tol = tolerances.get("quantity_matches_computed_need") or 0.15
    target = computed.recommended_quantity
    if target == 0:
        matches = quantity == 0
        detail = f"Policy computes no order; agent chose {quantity}."
    else:
        matches = abs(quantity - target) <= tol * target
        detail = f"Agent chose {quantity}; policy computes {target} (tolerance {tol:.0%})."
    add("quantity_matches_computed_need", matches, detail)

    open_pos = existing_open_pos if existing_open_pos is not None else \
        evidence["open_pos"]["open_orders"]
    same_cycle = [
        p for p in open_pos
        if p.get("expected_arrival_days", 99) <= computed.cover_horizon_days
        and p.get("status") in ("DRAFT", "PENDING_APPROVAL")
    ]
    add(
        "no_duplicate_open_po",
        len(same_cycle) == 0,
        f"{len(same_cycle)} unapproved order(s) already cover this cycle."
        if same_cycle else "No overlapping unapproved order.",
    )

    return verdict
