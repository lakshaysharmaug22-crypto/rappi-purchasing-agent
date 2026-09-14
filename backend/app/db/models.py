"""Domain models for the supply-chain planning mock.

Deliberately close to a real Turbo-style setup: SKUs live at dark-store
nodes, suppliers quote per-SKU terms, budgets and storage are per-node
and per-period, and every agent run is persisted for replay.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class POStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    CONFIRMED = "CONFIRMED"
    PARTIALLY_CONFIRMED = "PARTIALLY_CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    RECEIVED = "RECEIVED"


class RunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    AUTO_EXECUTED = "AUTO_EXECUTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ESCALATED = "ESCALATED"
    REJECTED_BY_JUDGE = "REJECTED_BY_JUDGE"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class Node(Base):
    """A dark store / micro-fulfilment centre."""

    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    city: Mapped[str] = mapped_column(String)
    country: Mapped[str] = mapped_column(String)
    storage_capacity_units: Mapped[int] = mapped_column(Integer)


class Product(Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    unit_volume: Mapped[float] = mapped_column(Float, default=1.0)
    shelf_life_days: Mapped[int] = mapped_column(Integer, default=365)
    is_perishable: Mapped[bool] = mapped_column(Boolean, default=False)


class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    on_hand: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    safety_stock: Mapped[int] = mapped_column(Integer, default=0)


class DemandSignal(Base):
    """Forecast plus recent actuals, so the agent can detect forecast drift."""

    __tablename__ = "demand_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    forecast_daily_units: Mapped[float] = mapped_column(Float)
    actual_daily_units_7d: Mapped[float] = mapped_column(Float)
    actual_daily_units_28d: Mapped[float] = mapped_column(Float)
    demand_std_dev: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str] = mapped_column(String, default="")


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    country: Mapped[str] = mapped_column(String)
    reliability_score: Mapped[float] = mapped_column(Float, default=1.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(String, default="")


class SupplierProduct(Base):
    """Per-supplier commercial terms for a SKU."""

    __tablename__ = "supplier_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    unit_price: Mapped[float] = mapped_column(Float)
    moq: Mapped[int] = mapped_column(Integer, default=0)
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=7)
    max_units_per_order: Mapped[int] = mapped_column(Integer, default=100000)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"))
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    confirmed_quantity: Mapped[int] = mapped_column(Integer, default=0)
    unit_price: Mapped[float] = mapped_column(Float)
    status: Mapped[POStatus] = mapped_column(Enum(POStatus), default=POStatus.DRAFT)
    expected_arrival_days: Mapped[int] = mapped_column(Integer, default=7)
    created_by: Mapped[str] = mapped_column(String, default="agent")
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class Budget(Base):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    period: Mapped[str] = mapped_column(String)
    allocated_amount: Mapped[float] = mapped_column(Float)
    committed_amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String, default="USD")


class AgentRun(Base):
    """One end-to-end agent invocation: evidence, decision, verdict, outcome.

    This table is the agent's memory and the evaluation dataset at once.
    """

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    scenario: Mapped[str] = mapped_column(String)
    request: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[dict] = mapped_column(JSON, default=dict)
    verdict: Mapped[dict] = mapped_column(JSON, default=dict)
    execution: Mapped[dict] = mapped_column(JSON, default=dict)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.RUNNING)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class ApprovalTask(Base):
    """Human-in-the-loop queue item."""

    __tablename__ = "approval_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    reason: Mapped[str] = mapped_column(String)
    proposed_action: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="OPEN")
    resolution: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
