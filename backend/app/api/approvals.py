"""Human-in-the-loop resolution.

An approval is not a rubber stamp on a row already in the database. Approving
transmits the order to the supplier for the first time, and the result is
verified exactly as an autonomous write would be — including the case where
the buyer edits the quantity, which is re-authorised from scratch.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AgentRun, ApprovalTask, Budget, POStatus, PurchaseOrder, RunStatus
from ..domain.evidence import gather_evidence, pick_supplier, run_policy_math
from ..tools import supplier_api
from ..tools.guardrail import authorise_purchase
from ..tools.write_tools import read_purchase_order


def list_tasks(db: Session, status: str | None = "OPEN") -> list[dict]:
    stmt = select(ApprovalTask).order_by(ApprovalTask.created_at.desc())
    if status:
        stmt = stmt.where(ApprovalTask.status == status)
    return [
        {
            "id": t.id,
            "run_id": t.run_id,
            "reason": t.reason,
            "proposed_action": t.proposed_action,
            "status": t.status,
            "resolution": t.resolution,
            "created_at": t.created_at.isoformat(),
        }
        for t in db.scalars(stmt).all()
    ]


def resolve(
    db: Session,
    task_id: str,
    *,
    approved: bool,
    quantity: int | None = None,
    approver: str = "buyer",
    note: str = "",
) -> dict:
    task = db.get(ApprovalTask, task_id)
    if not task:
        return {"ok": False, "error": f"No approval task {task_id}"}
    if task.status != "OPEN":
        return {"ok": False, "error": f"Task already {task.status}"}

    run = db.get(AgentRun, task.run_id)
    period = (run.request or {}).get("period", "2026-09") if run else "2026-09"
    po_id = (task.proposed_action or {}).get("po_id")

    if not approved:
        if po_id:
            po = db.get(PurchaseOrder, po_id)
            if po:
                po.status = POStatus.REJECTED
        task.status = "REJECTED"
        task.resolution = {"approver": approver, "note": note, "approved": False}
        if run:
            run.status = RunStatus.COMPLETED
        db.commit()
        return {"ok": True, "task": task_id, "outcome": "rejected",
                "purchase_order": read_purchase_order(db, po_id) if po_id else None}

    if not po_id:
        task.status = "APPROVED"
        task.resolution = {"approver": approver, "note": note, "approved": True,
                           "detail": "Escalation acknowledged; no order attached."}
        db.commit()
        return {"ok": True, "task": task_id, "outcome": "acknowledged"}

    po = db.get(PurchaseOrder, po_id)
    if not po:
        return {"ok": False, "error": f"No purchase order {po_id}"}

    final_qty = int(quantity) if quantity is not None else po.quantity
    edited = final_qty != po.quantity

    # A buyer's edit is re-authorised, not trusted. Humans get MOQs wrong too.
    guard = authorise_purchase(
        db,
        sku=po.sku,
        node_id=po.node_id,
        supplier_id=po.supplier_id,
        quantity=final_qty,
        period=period,
        confidence=1.0,
        rubric_all_pass=True,
    )
    if not guard.allowed:
        return {
            "ok": False,
            "stage": "guardrail",
            "error": "The approved quantity violates a hard constraint.",
            "guard": guard.to_dict(),
        }

    po.quantity = final_qty
    response = supplier_api.submit_order(
        supplier_id=po.supplier_id,
        sku=po.sku,
        quantity=final_qty,
        lead_time_days=po.expected_arrival_days,
    )
    po.confirmed_quantity = response.confirmed_quantity
    po.expected_arrival_days = response.lead_time_days
    if response.is_rejected:
        po.status = POStatus.REJECTED
    elif response.is_partial:
        po.status = POStatus.PARTIALLY_CONFIRMED
    else:
        po.status = POStatus.CONFIRMED

    budget = db.scalar(select(Budget).where(Budget.node_id == po.node_id,
                                            Budget.period == period))
    if budget:
        budget.committed_amount += response.confirmed_quantity * po.unit_price

    task.status = "APPROVED"
    task.resolution = {
        "approver": approver,
        "note": note,
        "approved": True,
        "quantity_approved": final_qty,
        "quantity_proposed": task.proposed_action.get("quantity"),
        "edited_by_human": edited,
    }

    # Verify the post-approval outcome the same way the agent verifies its own.
    evidence = gather_evidence(db, po.sku, po.node_id, period)
    supplier = pick_supplier(evidence, po.supplier_id)
    computed = run_policy_math(evidence, supplier)
    verification = {
        "verified": True,
        "po_id": po.id,
        "intended_quantity": final_qty,
        "confirmed_quantity": response.confirmed_quantity,
        "shortfall": max(final_qty - response.confirmed_quantity, 0),
        "matched_intent": response.confirmed_quantity >= final_qty,
        "residual_need": max(computed.raw_need, 0),
        "need_now_covered": computed.raw_need <= 0,
        "supplier_message": response.message,
    }

    if run:
        run.verification = verification
        run.execution = {**(run.execution or {}), "approved_by": approver,
                         "quantity_approved": final_qty, "edited_by_human": edited}
        trace = list(run.trace or [])
        trace.append({
            "step": len(trace) + 1,
            "node": "human_approval",
            "summary": (
                f"{approver} approved {final_qty} units"
                + (f" (edited from {task.proposed_action.get('quantity')})" if edited else "")
                + f"; supplier confirmed {response.confirmed_quantity}."
            ),
            "data": verification,
        })
        run.trace = trace
        run.status = (
            RunStatus.COMPLETED if verification["matched_intent"] else RunStatus.ESCALATED
        )

    db.commit()
    return {
        "ok": True,
        "task": task_id,
        "outcome": "approved",
        "edited_by_human": edited,
        "purchase_order": read_purchase_order(db, po.id),
        "verification": verification,
    }
