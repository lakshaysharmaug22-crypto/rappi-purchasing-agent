"""Read tools.

Each function is one narrow question a buyer would ask a system. They are
narrow on purpose: the agent's trace then shows exactly which facts it went
and got, and the rubric can check that it got all of them before deciding.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    Budget,
    DemandSignal,
    Inventory,
    Node,
    POStatus,
    Product,
    PurchaseOrder,
    Supplier,
    SupplierProduct,
)

OPEN_STATUSES = (
    POStatus.DRAFT,
    POStatus.PENDING_APPROVAL,
    POStatus.CONFIRMED,
    POStatus.PARTIALLY_CONFIRMED,
)


def get_product(db: Session, sku: str) -> dict:
    p = db.get(Product, sku)
    if not p:
        return {"error": f"Unknown SKU {sku}"}
    return {
        "sku": p.sku,
        "name": p.name,
        "category": p.category,
        "unit_volume": p.unit_volume,
        "shelf_life_days": p.shelf_life_days,
        "is_perishable": p.is_perishable,
    }


def get_inventory(db: Session, sku: str, node_id: str) -> dict:
    inv = db.scalar(
        select(Inventory).where(Inventory.sku == sku, Inventory.node_id == node_id)
    )
    if not inv:
        return {"error": f"No inventory record for {sku} at {node_id}"}
    return {
        "sku": sku,
        "node_id": node_id,
        "on_hand": inv.on_hand,
        "reserved": inv.reserved,
        "available": inv.on_hand - inv.reserved,
        "safety_stock": inv.safety_stock,
    }


def get_demand_signal(db: Session, sku: str, node_id: str) -> dict:
    d = db.scalar(
        select(DemandSignal).where(DemandSignal.sku == sku, DemandSignal.node_id == node_id)
    )
    if not d:
        return {"error": f"No demand signal for {sku} at {node_id}"}
    drift = 0.0
    if d.forecast_daily_units:
        drift = (d.actual_daily_units_7d - d.forecast_daily_units) / d.forecast_daily_units
    return {
        "sku": sku,
        "node_id": node_id,
        "forecast_daily_units": d.forecast_daily_units,
        "actual_daily_units_7d": d.actual_daily_units_7d,
        "actual_daily_units_28d": d.actual_daily_units_28d,
        "demand_std_dev": d.demand_std_dev,
        "forecast_drift_pct": round(drift * 100, 1),
        "notes": d.notes,
    }


def get_open_purchase_orders(db: Session, sku: str, node_id: str) -> dict:
    rows = db.scalars(
        select(PurchaseOrder).where(
            PurchaseOrder.sku == sku,
            PurchaseOrder.node_id == node_id,
            PurchaseOrder.status.in_(OPEN_STATUSES),
        )
    ).all()
    orders = [
        {
            "po_id": r.id,
            "supplier_id": r.supplier_id,
            "quantity": r.quantity,
            "confirmed_quantity": r.confirmed_quantity,
            "status": r.status.value,
            "expected_arrival_days": r.expected_arrival_days,
            "unit_price": r.unit_price,
        }
        for r in rows
    ]
    incoming = sum(
        o["confirmed_quantity"] if o["status"] in ("CONFIRMED", "PARTIALLY_CONFIRMED")
        else o["quantity"]
        for o in orders
    )
    return {"sku": sku, "node_id": node_id, "open_orders": orders, "total_incoming": incoming}


def get_supplier_options(db: Session, sku: str) -> dict:
    rows = db.scalars(select(SupplierProduct).where(SupplierProduct.sku == sku)).all()
    options = []
    for r in rows:
        s = db.get(Supplier, r.supplier_id)
        if not s or not s.is_active:
            continue
        options.append(
            {
                "supplier_id": s.id,
                "supplier_name": s.name,
                "unit_price": r.unit_price,
                "moq": r.moq,
                "lot_size": r.lot_size,
                "lead_time_days": r.lead_time_days,
                "max_units_per_order": r.max_units_per_order,
                "reliability_score": s.reliability_score,
                "notes": s.notes,
            }
        )
    options.sort(key=lambda o: o["unit_price"])
    return {"sku": sku, "suppliers": options}


def get_budget(db: Session, node_id: str, period: str) -> dict:
    b = db.scalar(select(Budget).where(Budget.node_id == node_id, Budget.period == period))
    if not b:
        return {"error": f"No budget for {node_id} in {period}"}
    return {
        "node_id": node_id,
        "period": period,
        "allocated_amount": b.allocated_amount,
        "committed_amount": b.committed_amount,
        "remaining_amount": round(b.allocated_amount - b.committed_amount, 2),
        "currency": b.currency,
    }


def get_storage_capacity(db: Session, node_id: str) -> dict:
    node = db.get(Node, node_id)
    if not node:
        return {"error": f"Unknown node {node_id}"}

    used = 0.0
    for inv in db.scalars(select(Inventory).where(Inventory.node_id == node_id)).all():
        product = db.get(Product, inv.sku)
        used += inv.on_hand * (product.unit_volume if product else 1.0)

    inbound = 0.0
    for po in db.scalars(
        select(PurchaseOrder).where(
            PurchaseOrder.node_id == node_id, PurchaseOrder.status.in_(OPEN_STATUSES)
        )
    ).all():
        product = db.get(Product, po.sku)
        qty = po.confirmed_quantity or po.quantity
        inbound += qty * (product.unit_volume if product else 1.0)

    free = node.storage_capacity_units - used - inbound
    return {
        "node_id": node_id,
        "node_name": node.name,
        "city": node.city,
        "capacity_units": node.storage_capacity_units,
        "used_units": round(used, 1),
        "inbound_units": round(inbound, 1),
        "free_units": round(free, 1),
    }


READ_TOOLS = {
    "get_product": get_product,
    "get_inventory": get_inventory,
    "get_demand_signal": get_demand_signal,
    "get_open_purchase_orders": get_open_purchase_orders,
    "get_supplier_options": get_supplier_options,
    "get_budget": get_budget,
    "get_storage_capacity": get_storage_capacity,
}
