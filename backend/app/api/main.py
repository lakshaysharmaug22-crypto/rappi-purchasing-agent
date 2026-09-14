from __future__ import annotations

import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent import llm
from ..agent.graph import run_agent
from ..db.models import AgentRun, Inventory, Node, Product, PurchaseOrder, Supplier
from ..db.seed import reset_and_seed
from ..db.session import get_session, init_db
from ..domain.evidence import gather_evidence, load_policy, load_rubric, pick_supplier, run_policy_math
from ..tools import read_tools, supplier_api
from ..tools.write_tools import read_purchase_order
from . import approvals

app = FastAPI(title="Turbo Purchasing Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


class RunRequest(BaseModel):
    sku: str
    node_id: str
    recommended_quantity: int = Field(ge=0)
    supplier_id: str | None = None
    period: str = "2026-09"
    scenario: str = "S1"
    failure_profile: dict | None = None


class ApprovalRequest(BaseModel):
    approved: bool
    quantity: int | None = None
    approver: str = "buyer"
    note: str = ""


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "llm_configured": llm.is_available(),
        "mode": "llm" if llm.is_available() else "policy_fallback",
        "policy_version": load_policy()["version"],
        "rubric_version": load_rubric()["version"],
    }


@app.post("/api/seed")
def seed(db: Session = Depends(get_session)) -> dict:
    reset_and_seed(db)
    supplier_api.clear_failure_profile()
    return {"ok": True, "message": "Database reset to seed state."}


@app.get("/api/catalogue")
def catalogue(db: Session = Depends(get_session)) -> dict:
    return {
        "nodes": [
            {"id": n.id, "name": n.name, "city": n.city, "country": n.country,
             "storage_capacity_units": n.storage_capacity_units}
            for n in db.scalars(select(Node)).all()
        ],
        "products": [
            {"sku": p.sku, "name": p.name, "category": p.category,
             "unit_volume": p.unit_volume, "shelf_life_days": p.shelf_life_days,
             "is_perishable": p.is_perishable}
            for p in db.scalars(select(Product)).all()
        ],
        "suppliers": [
            {"id": s.id, "name": s.name, "reliability_score": s.reliability_score,
             "notes": s.notes}
            for s in db.scalars(select(Supplier)).all()
        ],
    }


@app.get("/api/evidence")
def evidence(sku: str, node_id: str, period: str = "2026-09",
             supplier_id: str | None = None,
             db: Session = Depends(get_session)) -> dict:
    ev = gather_evidence(db, sku, node_id, period)
    if ev["missing"]:
        raise HTTPException(404, f"Missing evidence: {', '.join(ev['missing'])}")
    supplier = pick_supplier(ev, supplier_id)
    return {"evidence": ev, "supplier": supplier,
            "policy_math": run_policy_math(ev, supplier).to_dict()}


@app.post("/api/runs")
def create_run(payload: RunRequest, db: Session = Depends(get_session)) -> dict:
    if payload.failure_profile is not None:
        supplier_api.set_failure_profile(payload.failure_profile)
    else:
        supplier_api.clear_failure_profile()
    try:
        return run_agent(db, payload.model_dump(exclude={"failure_profile"}))
    finally:
        supplier_api.clear_failure_profile()


@app.get("/api/runs")
def list_runs(limit: int = 25, db: Session = Depends(get_session)) -> list[dict]:
    rows = db.scalars(
        select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "run_id": r.id,
            "scenario": r.scenario,
            "request": r.request,
            "status": r.status.value,
            "action": (r.decision or {}).get("action"),
            "quantity": (r.decision or {}).get("quantity"),
            "confidence": (r.verdict or {}).get("effective_confidence"),
            "attempts": r.attempt_count,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_session)) -> dict:
    r = db.get(AgentRun, run_id)
    if not r:
        raise HTTPException(404, f"No run {run_id}")
    return {
        "run_id": r.id,
        "scenario": r.scenario,
        "request": r.request,
        "status": r.status.value,
        "evidence": r.evidence,
        "decision": r.decision,
        "verdict": r.verdict,
        "execution": r.execution,
        "verification": r.verification,
        "trace": r.trace,
        "attempts": r.attempt_count,
        "created_at": r.created_at.isoformat(),
    }


@app.get("/api/approvals")
def get_approvals(status: str | None = "OPEN",
                  db: Session = Depends(get_session)) -> list[dict]:
    return approvals.list_tasks(db, status)


@app.post("/api/approvals/{task_id}")
def resolve_approval(task_id: str, payload: ApprovalRequest,
                     db: Session = Depends(get_session)) -> dict:
    result = approvals.resolve(
        db, task_id, approved=payload.approved, quantity=payload.quantity,
        approver=payload.approver, note=payload.note,
    )
    if not result.get("ok"):
        raise HTTPException(400, result)
    return result


@app.get("/api/purchase-orders")
def purchase_orders(node_id: str | None = None,
                    db: Session = Depends(get_session)) -> list[dict]:
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc())
    if node_id:
        stmt = stmt.where(PurchaseOrder.node_id == node_id)
    return [read_purchase_order(db, po.id) for po in db.scalars(stmt).all()]


@app.get("/api/inventory")
def inventory(node_id: str, db: Session = Depends(get_session)) -> list[dict]:
    rows = db.scalars(select(Inventory).where(Inventory.node_id == node_id)).all()
    return [read_tools.get_inventory(db, r.sku, node_id) for r in rows]


@app.get("/api/policy")
def policy() -> dict:
    return {"policy": load_policy(), "rubric": load_rubric()}
