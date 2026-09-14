"""Assembles the full evidence pack for a SKU/node and runs the policy maths.

The agent can call read tools one at a time, but every decision path ends up
here so that the numbers behind a decision are produced exactly once, the
same way, whether the caller is the LLM, the judge, or a test.
"""

from __future__ import annotations

import functools
import pathlib

import yaml
from sqlalchemy.orm import Session

from ..tools import read_tools
from .reorder import DemandView, ReorderResult, SupplierTerms, compute_reorder

POLICY_DIR = pathlib.Path(__file__).resolve().parent.parent / "policy"


@functools.lru_cache(maxsize=1)
def load_policy() -> dict:
    return yaml.safe_load((POLICY_DIR / "policy.yaml").read_text())


@functools.lru_cache(maxsize=1)
def load_rubric() -> dict:
    return yaml.safe_load((POLICY_DIR / "rubric.yaml").read_text())


def gather_evidence(db: Session, sku: str, node_id: str, period: str) -> dict:
    """Call every read tool. Missing pieces are recorded, not silently skipped."""
    evidence = {
        "product": read_tools.get_product(db, sku),
        "inventory": read_tools.get_inventory(db, sku, node_id),
        "demand": read_tools.get_demand_signal(db, sku, node_id),
        "open_pos": read_tools.get_open_purchase_orders(db, sku, node_id),
        "suppliers": read_tools.get_supplier_options(db, sku),
        "budget": read_tools.get_budget(db, node_id, period),
        "storage": read_tools.get_storage_capacity(db, node_id),
    }
    evidence["missing"] = [k for k, v in evidence.items() if isinstance(v, dict) and v.get("error")]
    return evidence


def pick_supplier(evidence: dict, supplier_id: str | None) -> dict | None:
    options = evidence.get("suppliers", {}).get("suppliers", [])
    if not options:
        return None
    if supplier_id:
        for o in options:
            if o["supplier_id"] == supplier_id:
                return o
    return options[0]


def run_policy_math(evidence: dict, supplier: dict) -> ReorderResult:
    policy = load_policy()
    inv = evidence["inventory"]
    dem = evidence["demand"]
    prod = evidence["product"]

    return compute_reorder(
        on_hand=inv["on_hand"],
        reserved=inv["reserved"],
        incoming=evidence["open_pos"]["total_incoming"],
        safety_stock=inv["safety_stock"],
        demand=DemandView(
            forecast_daily=dem["forecast_daily_units"],
            actual_daily_7d=dem["actual_daily_units_7d"],
            actual_daily_28d=dem["actual_daily_units_28d"],
            std_dev=dem["demand_std_dev"],
        ),
        terms=SupplierTerms(
            supplier_id=supplier["supplier_id"],
            unit_price=supplier["unit_price"],
            moq=supplier["moq"],
            lot_size=supplier["lot_size"],
            lead_time_days=supplier["lead_time_days"],
            max_units_per_order=supplier["max_units_per_order"],
            reliability_score=supplier["reliability_score"],
        ),
        policy=policy,
        budget_remaining=evidence["budget"]["remaining_amount"],
        storage_free_units=evidence["storage"]["free_units"],
        unit_volume=prod["unit_volume"],
        is_perishable=prod["is_perishable"],
        shelf_life_days=prod["shelf_life_days"],
    )
