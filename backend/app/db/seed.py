"""Deterministic seed data for a small Turbo-style network.

Three dark stores, five SKUs, three suppliers with genuinely different
commercial terms. The numbers are chosen so that each evaluation scenario
has exactly one defensible answer — see eval/scenarios.py.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import (
    Base,
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
from .session import engine

PERIOD = "2026-09"

NODES = [
    ("TURBO-BOG-01", "Turbo Chapinero", "Bogotá", "CO", 15000),
    ("TURBO-MDE-02", "Turbo El Poblado", "Medellín", "CO", 9000),
    ("TURBO-SAO-03", "Turbo Pinheiros", "São Paulo", "BR", 12000),
]

PRODUCTS = [
    ("SKU-ARE-001", "Arequipe spread 250g", "pantry", 1.0, 180, False),
    ("SKU-AGU-002", "Agua mineral 600ml 6-pack", "beverages", 3.0, 365, False),
    ("SKU-CAF-003", "Café tostado molido 500g", "pantry", 1.2, 270, False),
    ("SKU-LEC-004", "Leche entera 1L", "dairy", 1.5, 14, True),
    ("SKU-SNK-005", "Snack mix multipack", "snacks", 1.0, 120, False),
]

SUPPLIERS = [
    ("SUP-ANDINA", "Andina Distribuidora", "CO", 0.93, True,
     "Primary regional distributor. Strong fill rate, slower lead times."),
    ("SUP-CARIBE", "Caribe Foods", "CO", 0.86, True,
     "Secondary source. Faster, smaller minimums, roughly 10% dearer."),
    ("SUP-EXPRESS", "Express Mayorista", "CO", 0.72, True,
     "Emergency backup. Next-day capable, small lots, premium pricing."),
]

# supplier_id, sku, unit_price, moq, lot_size, lead_time_days, max_units_per_order
SUPPLIER_PRODUCTS = [
    ("SUP-ANDINA", "SKU-ARE-001", 2.40, 500, 50, 6, 5000),
    ("SUP-ANDINA", "SKU-AGU-002", 1.80, 400, 100, 4, 6000),
    ("SUP-ANDINA", "SKU-CAF-003", 6.50, 200, 50, 8, 3000),
    ("SUP-ANDINA", "SKU-LEC-004", 1.10, 300, 100, 2, 4000),
    ("SUP-ANDINA", "SKU-SNK-005", 0.95, 600, 100, 5, 8000),
    ("SUP-CARIBE", "SKU-ARE-001", 2.65, 200, 25, 4, 3000),
    ("SUP-CARIBE", "SKU-AGU-002", 1.95, 200, 50, 3, 4000),
    ("SUP-CARIBE", "SKU-CAF-003", 6.90, 100, 25, 5, 2000),
    ("SUP-CARIBE", "SKU-SNK-005", 1.05, 300, 50, 3, 5000),
    ("SUP-EXPRESS", "SKU-ARE-001", 3.10, 50, 10, 2, 1000),
    ("SUP-EXPRESS", "SKU-AGU-002", 2.30, 100, 50, 2, 1500),
    ("SUP-EXPRESS", "SKU-LEC-004", 1.35, 100, 50, 1, 1500),
    ("SUP-EXPRESS", "SKU-SNK-005", 1.25, 100, 25, 2, 1200),
]

# sku, node, on_hand, reserved, safety_stock
INVENTORY = [
    ("SKU-ARE-001", "TURBO-BOG-01", 1200, 100, 300),
    ("SKU-AGU-002", "TURBO-BOG-01", 600, 40, 200),
    ("SKU-CAF-003", "TURBO-BOG-01", 300, 25, 150),
    ("SKU-LEC-004", "TURBO-BOG-01", 400, 0, 200),
    ("SKU-SNK-005", "TURBO-BOG-01", 900, 50, 400),
    ("SKU-ARE-001", "TURBO-MDE-02", 1500, 0, 250),
    ("SKU-AGU-002", "TURBO-MDE-02", 300, 0, 300),
    ("SKU-CAF-003", "TURBO-MDE-02", 400, 20, 250),
    ("SKU-LEC-004", "TURBO-MDE-02", 500, 0, 150),
    ("SKU-SNK-005", "TURBO-MDE-02", 3000, 0, 400),
    ("SKU-ARE-001", "TURBO-SAO-03", 100, 0, 150),
    ("SKU-AGU-002", "TURBO-SAO-03", 800, 0, 200),
    ("SKU-SNK-005", "TURBO-SAO-03", 600, 0, 200),
]

# sku, node, forecast_daily, actual_7d, actual_28d, std_dev, note
DEMAND = [
    ("SKU-ARE-001", "TURBO-BOG-01", 90, 95, 88, 12, "Stable."),
    ("SKU-AGU-002", "TURBO-BOG-01", 60, 58, 61, 8, "Stable."),
    ("SKU-CAF-003", "TURBO-BOG-01", 45, 45, 44, 5, "Stable."),
    ("SKU-LEC-004", "TURBO-BOG-01", 130, 125, 128, 15, "Stable."),
    ("SKU-SNK-005", "TURBO-BOG-01", 80, 210, 90, 40,
     "Sharp uplift since a creator post drove repeat orders."),
    ("SKU-ARE-001", "TURBO-MDE-02", 70, 68, 71, 9, "Stable."),
    ("SKU-AGU-002", "TURBO-MDE-02", 120, 130, 118, 18, "Warm spell lifting water sales."),
    ("SKU-CAF-003", "TURBO-MDE-02", 70, 75, 69, 10, "Mild uplift."),
    ("SKU-LEC-004", "TURBO-MDE-02", 90, 88, 91, 11, "Stable."),
    ("SKU-SNK-005", "TURBO-MDE-02", 110, 105, 112, 14, "Stable."),
    ("SKU-ARE-001", "TURBO-SAO-03", 40, 42, 39, 6, "Stable."),
    ("SKU-AGU-002", "TURBO-SAO-03", 95, 92, 96, 12, "Stable."),
    ("SKU-SNK-005", "TURBO-SAO-03", 60, 62, 58, 8, "Stable."),
]

# id, sku, node, supplier, qty, status, arrival_days
OPEN_POS = [
    ("PO-1001", "SKU-ARE-001", "TURBO-BOG-01", "SUP-ANDINA", 600, POStatus.CONFIRMED, 4),
    ("PO-1002", "SKU-SNK-005", "TURBO-BOG-01", "SUP-ANDINA", 600, POStatus.CONFIRMED, 3),
]

# node, allocated, committed
BUDGETS = [
    ("TURBO-BOG-01", 60000.0, 21000.0),
    ("TURBO-MDE-02", 30000.0, 26800.0),
    ("TURBO-SAO-03", 12000.0, 11600.0),
]


def reset_and_seed(session: Session) -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    for nid, name, city, country, cap in NODES:
        session.add(Node(id=nid, name=name, city=city, country=country,
                         storage_capacity_units=cap))

    for sku, name, cat, vol, shelf, perish in PRODUCTS:
        session.add(Product(sku=sku, name=name, category=cat, unit_volume=vol,
                            shelf_life_days=shelf, is_perishable=perish))

    for sid, name, country, rel, active, notes in SUPPLIERS:
        session.add(Supplier(id=sid, name=name, country=country,
                             reliability_score=rel, is_active=active, notes=notes))

    for sid, sku, price, moq, lot, lead, cap in SUPPLIER_PRODUCTS:
        session.add(SupplierProduct(supplier_id=sid, sku=sku, unit_price=price,
                                    moq=moq, lot_size=lot, lead_time_days=lead,
                                    max_units_per_order=cap))

    for sku, node, on_hand, reserved, safety in INVENTORY:
        session.add(Inventory(sku=sku, node_id=node, on_hand=on_hand,
                              reserved=reserved, safety_stock=safety))

    for sku, node, fc, a7, a28, sd, note in DEMAND:
        session.add(DemandSignal(sku=sku, node_id=node, forecast_daily_units=fc,
                                 actual_daily_units_7d=a7, actual_daily_units_28d=a28,
                                 demand_std_dev=sd, notes=note))

    for pid, sku, node, sup, qty, status, arrival in OPEN_POS:
        price = next(p for s, k, p, *_ in SUPPLIER_PRODUCTS if s == sup and k == sku)
        session.add(PurchaseOrder(id=pid, sku=sku, node_id=node, supplier_id=sup,
                                  quantity=qty, confirmed_quantity=qty, unit_price=price,
                                  status=status, expected_arrival_days=arrival,
                                  created_by="planner"))

    for node, allocated, committed in BUDGETS:
        session.add(Budget(node_id=node, period=PERIOD, allocated_amount=allocated,
                           committed_amount=committed))

    session.commit()


if __name__ == "__main__":
    from .session import SessionLocal

    with SessionLocal() as s:
        reset_and_seed(s)
    print("Seeded purchasing.db")
