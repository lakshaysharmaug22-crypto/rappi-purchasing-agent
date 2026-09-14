"""Write tools.

Every mutation is guarded, transmitted to the (mock) supplier system, and
persisted with whatever the supplier actually confirmed — not with what we
asked for. That distinction is the whole point of the verification step.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Budget, POStatus, PurchaseOrder
from ..domain.evidence import gather_evidence, pick_supplier
from . import supplier_api
from .guardrail import authorise_purchase


def _new_po_id() -> str:
    return f"PO-{uuid.uuid4().hex[:8].upper()}"


def create_purchase_order(
    db: Session,
    *,
    sku: str,
    node_id: str,
    supplier_id: str,
    quantity: int,
    period: str,
    run_id: str,
    confidence: float = 1.0,
    rubric_all_pass: bool = True,
    open_questions: list[str] | None = None,
    created_by: str = "agent",
) -> dict:
    guard = authorise_purchase(
        db,
        sku=sku,
        node_id=node_id,
        supplier_id=supplier_id,
        quantity=quantity,
        period=period,
        confidence=confidence,
        rubric_all_pass=rubric_all_pass,
        open_questions=open_questions,
    )
    if not guard.allowed:
        return {"ok": False, "stage": "guardrail", "guard": guard.to_dict()}

    evidence = gather_evidence(db, sku, node_id, period)
    supplier = pick_supplier(evidence, supplier_id)

    if guard.requires_human:
        po = PurchaseOrder(
            id=_new_po_id(),
            sku=sku,
            node_id=node_id,
            supplier_id=supplier_id,
            quantity=quantity,
            confirmed_quantity=0,
            unit_price=supplier["unit_price"],
            status=POStatus.PENDING_APPROVAL,
            expected_arrival_days=supplier["lead_time_days"],
            created_by=created_by,
            run_id=run_id,
        )
        db.add(po)
        db.commit()
        return {
            "ok": True,
            "stage": "queued_for_approval",
            "guard": guard.to_dict(),
            "purchase_order": read_purchase_order(db, po.id),
        }

    response = supplier_api.submit_order(
        supplier_id=supplier_id,
        sku=sku,
        quantity=quantity,
        lead_time_days=supplier["lead_time_days"],
        reliability_score=supplier["reliability_score"],
    )

    if response.is_rejected:
        status = POStatus.REJECTED
    elif response.is_partial:
        status = POStatus.PARTIALLY_CONFIRMED
    else:
        status = POStatus.CONFIRMED

    po = PurchaseOrder(
        id=_new_po_id(),
        sku=sku,
        node_id=node_id,
        supplier_id=supplier_id,
        quantity=quantity,
        confirmed_quantity=response.confirmed_quantity,
        unit_price=supplier["unit_price"],
        status=status,
        expected_arrival_days=response.lead_time_days,
        created_by=created_by,
        run_id=run_id,
    )
    db.add(po)

    # Commit budget against what the supplier actually confirmed.
    budget = db.scalar(select(Budget).where(Budget.node_id == node_id, Budget.period == period))
    if budget:
        budget.committed_amount += response.confirmed_quantity * supplier["unit_price"]

    db.commit()

    return {
        "ok": True,
        "stage": "executed",
        "guard": guard.to_dict(),
        "supplier_response": response.to_dict(),
        "purchase_order": read_purchase_order(db, po.id),
    }


def read_purchase_order(db: Session, po_id: str) -> dict:
    po = db.get(PurchaseOrder, po_id)
    if not po:
        return {"error": f"No purchase order {po_id}"}
    return {
        "po_id": po.id,
        "sku": po.sku,
        "node_id": po.node_id,
        "supplier_id": po.supplier_id,
        "quantity": po.quantity,
        "confirmed_quantity": po.confirmed_quantity,
        "unit_price": po.unit_price,
        "order_value": round(po.confirmed_quantity * po.unit_price, 2),
        "status": po.status.value,
        "expected_arrival_days": po.expected_arrival_days,
        "created_by": po.created_by,
        "run_id": po.run_id,
    }


def amend_purchase_order(db: Session, po_id: str, new_quantity: int, period: str) -> dict:
    po = db.get(PurchaseOrder, po_id)
    if not po:
        return {"ok": False, "error": f"No purchase order {po_id}"}
    if po.status in (POStatus.RECEIVED, POStatus.CANCELLED):
        return {"ok": False, "error": f"Cannot amend a {po.status.value} order"}

    guard = authorise_purchase(
        db,
        sku=po.sku,
        node_id=po.node_id,
        supplier_id=po.supplier_id,
        quantity=new_quantity,
        period=period,
        confidence=1.0,
        rubric_all_pass=True,
    )
    if not guard.allowed:
        return {"ok": False, "stage": "guardrail", "guard": guard.to_dict()}

    po.quantity = new_quantity
    db.commit()
    return {"ok": True, "purchase_order": read_purchase_order(db, po_id)}


def cancel_purchase_order(db: Session, po_id: str, period: str, reason: str = "") -> dict:
    po = db.get(PurchaseOrder, po_id)
    if not po:
        return {"ok": False, "error": f"No purchase order {po_id}"}

    released = po.confirmed_quantity * po.unit_price
    po.status = POStatus.CANCELLED
    budget = db.scalar(
        select(Budget).where(Budget.node_id == po.node_id, Budget.period == period)
    )
    if budget:
        budget.committed_amount = max(0.0, budget.committed_amount - released)
    db.commit()
    return {
        "ok": True,
        "released_budget": round(released, 2),
        "reason": reason,
        "purchase_order": read_purchase_order(db, po_id),
    }
