"""Server-side authorisation for write actions.

This is deliberately *not* part of the agent. The agent proposes; this layer
disposes. Even a compromised prompt, a hallucinated quantity or a future
refactor that forgets to call the judge cannot get an illegal PO into the
database, because every write goes through here and here alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..domain.evidence import gather_evidence, load_policy, pick_supplier


@dataclass
class GuardDecision:
    allowed: bool
    violations: list[str] = field(default_factory=list)
    requires_human: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "violations": self.violations,
            "requires_human": self.requires_human,
            "reasons": self.reasons,
        }


def authorise_purchase(
    db: Session,
    *,
    sku: str,
    node_id: str,
    supplier_id: str,
    quantity: int,
    period: str,
    confidence: float,
    rubric_all_pass: bool,
    open_questions: list[str] | None = None,
) -> GuardDecision:
    policy = load_policy()
    auto = policy["autonomy"]["auto_execute"]
    decision = GuardDecision(allowed=True)

    if quantity <= 0:
        decision.allowed = False
        decision.violations.append("quantity_must_be_positive")
        return decision

    evidence = gather_evidence(db, sku, node_id, period)
    if evidence["missing"]:
        decision.allowed = False
        decision.violations.append(f"missing_evidence:{','.join(evidence['missing'])}")
        return decision

    supplier = pick_supplier(evidence, supplier_id)
    if supplier is None or supplier["supplier_id"] != supplier_id:
        decision.allowed = False
        decision.violations.append("supplier_inactive_or_does_not_list_sku")
        return decision

    order_value = quantity * supplier["unit_price"]

    if quantity < supplier["moq"]:
        decision.allowed = False
        decision.violations.append(f"below_moq:{supplier['moq']}")
    if supplier["lot_size"] > 1 and quantity % supplier["lot_size"] != 0:
        decision.allowed = False
        decision.violations.append(f"not_multiple_of_lot_size:{supplier['lot_size']}")
    if quantity > supplier["max_units_per_order"]:
        decision.allowed = False
        decision.violations.append(f"above_supplier_max:{supplier['max_units_per_order']}")
    if order_value > evidence["budget"]["remaining_amount"]:
        decision.allowed = False
        decision.violations.append(
            f"exceeds_budget:{evidence['budget']['remaining_amount']:.2f}"
        )

    buffer = policy["constraints"]["storage_buffer_fraction"]
    usable = evidence["storage"]["free_units"] * (1 - buffer)
    needed = quantity * evidence["product"]["unit_volume"]
    if needed > usable:
        decision.allowed = False
        decision.violations.append(f"exceeds_storage:{usable:.0f}")

    # Autonomy gate: legal is not the same as unsupervised.
    if order_value > policy["autonomy"]["always_escalate_above_usd"]:
        decision.requires_human = True
        decision.reasons.append("order value above the always-escalate ceiling")
    if order_value > auto["max_order_value_usd"]:
        decision.requires_human = True
        decision.reasons.append(
            f"order value {order_value:,.2f} exceeds the autonomous limit "
            f"{auto['max_order_value_usd']:,.2f}"
        )
    if confidence < auto["min_confidence"]:
        decision.requires_human = True
        decision.reasons.append(f"confidence {confidence:.2f} below {auto['min_confidence']}")
    if auto["require_all_rubric_checks_pass"] and not rubric_all_pass:
        decision.requires_human = True
        decision.reasons.append("one or more rubric checks did not pass")
    if auto["require_no_open_questions"] and open_questions:
        decision.requires_human = True
        decision.reasons.append(f"agent raised {len(open_questions)} open question(s)")

    return decision
