"""The agent graph.

    investigate -> decide -> judge -> route
                                        |-- execute -> verify -> (replan | finalise)
                                        |-- request_approval -> finalise
                                        |-- escalate -> finalise
                                        `-- no_action -> finalise

Two properties are worth stating plainly, because they are what the exercise
is really asking about:

1. Nothing is written without passing the deterministic rubric *and* the
   server-side guardrail. The judge and the guard are separate on purpose —
   the judge decides whether the reasoning is sound, the guard decides
   whether the write is legal, and neither trusts the other.

2. After a write, the agent re-reads the record it actually created and
   re-scores it. If what came back differs from what it intended — a partial
   supplier confirmation, say — that is a first-class outcome that triggers a
   replan, not an exception to swallow.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from ..db.models import AgentRun, ApprovalTask, RunStatus
from ..domain.evidence import (
    gather_evidence,
    load_policy,
    load_rubric,
    pick_supplier,
    run_policy_math,
)
from ..judge import deterministic, llm_judge
from ..tools import write_tools
from .decider import Decision, decide


class AgentState(TypedDict, total=False):
    run_id: str
    request: dict
    db: Any
    evidence: dict
    computed: dict
    computed_obj: Any
    supplier: dict | None
    decision: dict
    verdict: dict
    effective_confidence: float
    next_step: str
    execution: dict
    verification: dict
    prior_attempt: dict | None
    attempt: int
    trace: list
    status: str


def _trace(state: AgentState, node: str, summary: str, data: dict | None = None) -> None:
    state.setdefault("trace", []).append(
        {
            "step": len(state.get("trace", [])) + 1,
            "node": node,
            "summary": summary,
            "data": data or {},
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
    )


# --------------------------------------------------------------------------- nodes


def investigate(state: AgentState) -> AgentState:
    db: Session = state["db"]
    req = state["request"]
    evidence = gather_evidence(db, req["sku"], req["node_id"], req["period"])
    state["evidence"] = evidence

    if evidence["missing"]:
        _trace(state, "investigate", f"Missing evidence: {', '.join(evidence['missing'])}",
               {"missing": evidence["missing"]})
        state["status"] = RunStatus.ESCALATED.value
        state["next_step"] = "escalate"
        return state

    supplier = pick_supplier(evidence, req.get("supplier_id"))
    computed = run_policy_math(evidence, supplier)
    state["supplier"] = supplier
    state["computed_obj"] = computed
    state["computed"] = computed.to_dict()

    _trace(
        state,
        "investigate",
        (
            f"Read 7 sources. Position {computed.current_position} against target "
            f"{computed.target_position} over a {computed.cover_horizon_days}-day horizon; "
            f"policy quantity {computed.recommended_quantity}."
        ),
        {
            "on_hand": evidence["inventory"]["on_hand"],
            "reserved": evidence["inventory"]["reserved"],
            "incoming": evidence["open_pos"]["total_incoming"],
            "demand_rate_used": computed.demand_rate_used,
            "forecast_drift_pct": evidence["demand"]["forecast_drift_pct"],
            "budget_remaining": evidence["budget"]["remaining_amount"],
            "storage_free": evidence["storage"]["free_units"],
            "binding_constraints": computed.binding_constraints,
        },
    )
    return state


def decide_node(state: AgentState) -> AgentState:
    decision, note = decide(
        request=state["request"],
        evidence=state["evidence"],
        computed=state["computed_obj"],
        supplier=state["supplier"],
        prior_attempt=state.get("prior_attempt"),
    )
    state["decision"] = decision.model_dump()
    _trace(
        state,
        "decide",
        f"{decision.action} {decision.quantity} units "
        f"(confidence {decision.confidence:.2f}, via {note['mode']}).",
        {"reasoning": decision.reasoning, "key_factors": decision.key_factors,
         "binding_constraint": decision.binding_constraint, "mode": note["mode"]},
    )
    return state


def judge_node(state: AgentState) -> AgentState:
    rubric = load_rubric()
    det = deterministic.evaluate(
        evidence=state["evidence"],
        supplier=state["supplier"],
        quantity=state["decision"]["quantity"],
        computed=state["computed_obj"],
    )
    judged = llm_judge.review(
        decision=state["decision"],
        evidence=state["evidence"],
        computed=state["computed"],
    )

    confidence = float(state["decision"]["confidence"])
    confidence -= rubric["scoring"]["warning_confidence_penalty"] * len(det.warnings)
    confidence += judged["confidence_adjustment"]
    confidence = max(0.0, min(1.0, confidence))

    state["verdict"] = {
        "deterministic": det.to_dict(),
        "judgement": judged,
        "effective_confidence": round(confidence, 3),
    }
    state["effective_confidence"] = confidence

    _trace(
        state,
        "judge",
        (
            f"{len(det.checks) - len(det.blocking_failures) - len(det.warnings)}"
            f"/{len(det.checks)} deterministic checks clean; "
            f"{len(det.blocking_failures)} blocking, {len(det.warnings)} warning. "
            f"Effective confidence {confidence:.2f}."
        ),
        {
            "blocking_failures": [c.id for c in det.blocking_failures],
            "warnings": [c.id for c in det.warnings],
            "judgement_failures": judged.get("failures", []),
            "judge_summary": judged.get("summary", ""),
        },
    )
    return state


def route_node(state: AgentState) -> AgentState:
    det = state["verdict"]["deterministic"]
    action = state["decision"]["action"]

    if det["blocking_failures"]:
        state["next_step"] = "escalate"
        state["status"] = RunStatus.REJECTED_BY_JUDGE.value
        _trace(state, "route", "Blocking rubric failure; the decision cannot be executed.",
               {"blocking": det["blocking_failures"]})
        return state

    if action in ("REJECT", "INVESTIGATE") or state["decision"]["quantity"] == 0:
        state["next_step"] = "escalate" if action == "INVESTIGATE" else "no_action"
        state["status"] = (
            RunStatus.ESCALATED.value if action == "INVESTIGATE" else RunStatus.COMPLETED.value
        )
        _trace(state, "route", f"{action}: no purchase order will be raised.", {})
        return state

    state["next_step"] = "execute"
    _trace(state, "route", "Decision is clean; proceeding to the write path.", {})
    return state


def execute_node(state: AgentState) -> AgentState:
    db: Session = state["db"]
    req = state["request"]
    dec = state["decision"]
    det = state["verdict"]["deterministic"]

    result = write_tools.create_purchase_order(
        db,
        sku=req["sku"],
        node_id=req["node_id"],
        supplier_id=dec["supplier_id"] or state["supplier"]["supplier_id"],
        quantity=dec["quantity"],
        period=req["period"],
        run_id=state["run_id"],
        confidence=state["effective_confidence"],
        rubric_all_pass=det["all_passed"],
        open_questions=dec.get("open_questions"),
    )
    state["execution"] = result

    if not result["ok"]:
        _trace(state, "execute", "Guardrail refused the write.", result.get("guard", {}))
        state["status"] = RunStatus.REJECTED_BY_JUDGE.value
        return state

    if result["stage"] == "queued_for_approval":
        task = ApprovalTask(
            id=f"APR-{uuid.uuid4().hex[:8].upper()}",
            run_id=state["run_id"],
            reason="; ".join(result["guard"]["reasons"]) or "Requires buyer sign-off.",
            proposed_action={
                "sku": req["sku"],
                "node_id": req["node_id"],
                "supplier_id": dec["supplier_id"],
                "quantity": dec["quantity"],
                "po_id": result["purchase_order"]["po_id"],
                "order_value": result["purchase_order"]["quantity"]
                * result["purchase_order"]["unit_price"],
                "reasoning": dec["reasoning"],
            },
        )
        db.add(task)
        db.commit()
        state["status"] = RunStatus.AWAITING_APPROVAL.value
        _trace(
            state,
            "execute",
            f"Queued {result['purchase_order']['po_id']} for buyer approval.",
            {"reasons": result["guard"]["reasons"], "approval_task": task.id},
        )
        return state

    state["status"] = RunStatus.AUTO_EXECUTED.value
    _trace(
        state,
        "execute",
        (
            f"Raised {result['purchase_order']['po_id']} for "
            f"{result['purchase_order']['quantity']} units; supplier confirmed "
            f"{result['purchase_order']['confirmed_quantity']}."
        ),
        {"supplier_response": result.get("supplier_response", {})},
    )
    return state


def verify_node(state: AgentState) -> AgentState:
    """Re-read the written record and score it, rather than trusting the write."""
    db: Session = state["db"]
    req = state["request"]
    execution = state.get("execution") or {}
    po_ref = execution.get("purchase_order")

    if not po_ref or "po_id" not in po_ref:
        state["verification"] = {"verified": False, "reason": "No purchase order to verify."}
        return state

    actual = write_tools.read_purchase_order(db, po_ref["po_id"])
    intended = state["decision"]["quantity"]
    confirmed = actual["confirmed_quantity"]

    if actual["status"] == "PENDING_APPROVAL":
        # Nothing has been sent to the supplier yet, so there is no outcome to
        # reconcile. Verify only that the queued record matches what was decided.
        state["verification"] = {
            "verified": True,
            "po_id": actual["po_id"],
            "intended_quantity": intended,
            "confirmed_quantity": 0,
            "shortfall": 0,
            "status": actual["status"],
            "matched_intent": actual["quantity"] == intended,
            "awaiting_approval": True,
            "note": "Held for buyer approval; not transmitted to the supplier.",
        }
        _trace(
            state,
            "verify",
            f"{actual['po_id']} is queued at {actual['quantity']} units, matching the decision. "
            "Nothing sent to the supplier yet.",
            state["verification"],
        )
        return state

    shortfall = intended - confirmed

    fresh_evidence = gather_evidence(db, req["sku"], req["node_id"], req["period"])
    fresh_computed = run_policy_math(fresh_evidence, state["supplier"])
    post_det = deterministic.evaluate(
        evidence=fresh_evidence,
        supplier=state["supplier"],
        quantity=0,
        computed=fresh_computed,
    )

    covered = fresh_computed.raw_need <= 0
    matched = shortfall <= 0

    state["verification"] = {
        "verified": True,
        "po_id": actual["po_id"],
        "intended_quantity": intended,
        "confirmed_quantity": confirmed,
        "shortfall": max(shortfall, 0),
        "status": actual["status"],
        "matched_intent": matched,
        "position_after": fresh_computed.current_position,
        "residual_need": max(fresh_computed.raw_need, 0),
        "need_now_covered": covered,
        "post_write_checks_clean": post_det.all_passed,
        "supplier_message": execution.get("supplier_response", {}).get("message", ""),
    }

    if matched:
        summary = (
            f"Supplier confirmed all {confirmed} units; residual need is "
            f"{max(fresh_computed.raw_need, 0)}."
        )
    else:
        summary = (
            f"Supplier confirmed {confirmed} of {intended}. Shortfall {shortfall}; "
            f"residual need {max(fresh_computed.raw_need, 0)} units."
        )
    _trace(state, "verify", summary, state["verification"])
    return state


def replan_node(state: AgentState) -> AgentState:
    """Feed the actual outcome back in and decide again."""
    state["attempt"] = state.get("attempt", 1) + 1
    state["prior_attempt"] = {
        "decision": state["decision"],
        "execution": {
            "po_id": state["verification"].get("po_id"),
            "confirmed_quantity": state["verification"].get("confirmed_quantity"),
            "supplier_message": state["verification"].get("supplier_message"),
        },
        "outcome": "supplier could not fulfil the order in full",
        "residual_need": state["verification"].get("residual_need"),
        "already_used_supplier": state["decision"].get("supplier_id"),
    }

    # Exclude the supplier that just failed us, so the replan genuinely looks elsewhere.
    failed = state["decision"].get("supplier_id")
    alternatives = [
        s for s in state["evidence"]["suppliers"]["suppliers"] if s["supplier_id"] != failed
    ]
    req = state["request"]
    fresh_evidence = gather_evidence(req_db := state["db"], req["sku"], req["node_id"], req["period"])
    fresh_evidence["suppliers"]["suppliers"] = alternatives or \
        fresh_evidence["suppliers"]["suppliers"]
    state["evidence"] = fresh_evidence

    supplier = alternatives[0] if alternatives else state["supplier"]
    state["supplier"] = supplier
    computed = run_policy_math(fresh_evidence, supplier)
    state["computed_obj"] = computed
    state["computed"] = computed.to_dict()
    state["request"] = {**req, "recommended_quantity": max(computed.raw_need, 0),
                        "supplier_id": supplier["supplier_id"]}

    _trace(
        state,
        "replan",
        (
            f"Attempt {state['attempt']}: re-sourcing {max(computed.raw_need, 0)} residual units "
            f"from {supplier['supplier_name']}."
        ),
        {"excluded_supplier": failed, "residual_need": max(computed.raw_need, 0)},
    )
    return state


def escalate_node(state: AgentState) -> AgentState:
    db: Session = state["db"]
    dec = state.get("decision", {})
    reasons = []
    verdict = state.get("verdict", {})
    if verdict.get("deterministic", {}).get("blocking_failures"):
        reasons.append(
            "blocking checks: " + ", ".join(verdict["deterministic"]["blocking_failures"])
        )
    if dec.get("open_questions"):
        reasons.extend(dec["open_questions"])
    if state["evidence"].get("missing"):
        reasons.append("missing evidence: " + ", ".join(state["evidence"]["missing"]))
    if state.get("verification", {}).get("shortfall"):
        reasons.append(
            f"unresolved shortfall of {state['verification']['shortfall']} units after replanning"
        )

    task = ApprovalTask(
        id=f"ESC-{uuid.uuid4().hex[:8].upper()}",
        run_id=state["run_id"],
        reason="; ".join(reasons) or "Agent escalated without an executable action.",
        proposed_action={
            "sku": state["request"]["sku"],
            "node_id": state["request"]["node_id"],
            "suggested_quantity": dec.get("quantity", 0),
            "supplier_id": dec.get("supplier_id"),
            "reasoning": dec.get("reasoning", ""),
        },
        status="OPEN",
    )
    db.add(task)
    db.commit()

    state["status"] = RunStatus.ESCALATED.value
    _trace(state, "escalate", f"Escalated to a buyer as {task.id}.", {"reasons": reasons})
    return state


def no_action_node(state: AgentState) -> AgentState:
    state["status"] = RunStatus.COMPLETED.value
    _trace(state, "no_action", "Closed with no purchase order; the recommendation was not sound.", {})
    return state


def finalise(state: AgentState) -> AgentState:
    db: Session = state["db"]
    run = db.get(AgentRun, state["run_id"])
    if run is None:
        run = AgentRun(id=state["run_id"], scenario=state["request"].get("scenario", "S1"),
                       request=state["request"])
        db.add(run)
    run.evidence = state.get("evidence", {})
    run.decision = state.get("decision", {})
    run.verdict = state.get("verdict", {})
    run.execution = {k: v for k, v in (state.get("execution") or {}).items()}
    run.verification = state.get("verification", {})
    run.trace = state.get("trace", [])
    run.attempt_count = state.get("attempt", 1)
    run.status = RunStatus(state.get("status", RunStatus.COMPLETED.value))
    db.commit()
    return state


# --------------------------------------------------------------------------- edges


def _after_investigate(state: AgentState) -> str:
    return "escalate" if state.get("next_step") == "escalate" else "decide"


def _after_route(state: AgentState) -> str:
    return state["next_step"]


def _after_verify(state: AgentState) -> str:
    policy = load_policy()
    verification = state.get("verification", {})
    if not verification.get("verified"):
        return "finalise"
    if verification.get("matched_intent"):
        return "finalise"
    if state.get("attempt", 1) > policy["retry"]["max_replan_attempts"]:
        return "escalate"
    if verification.get("need_now_covered"):
        return "finalise"
    return "replan"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("investigate", investigate)
    g.add_node("decide", decide_node)
    g.add_node("judge", judge_node)
    g.add_node("route_decision", route_node)
    g.add_node("execute", execute_node)
    g.add_node("verify", verify_node)
    g.add_node("replan", replan_node)
    g.add_node("escalate", escalate_node)
    g.add_node("no_action", no_action_node)
    g.add_node("finalise", finalise)

    g.set_entry_point("investigate")
    g.add_conditional_edges("investigate", _after_investigate,
                            {"decide": "decide", "escalate": "escalate"})
    g.add_edge("decide", "judge")
    g.add_edge("judge", "route_decision")
    g.add_conditional_edges(
        "route_decision",
        _after_route,
        {"execute": "execute", "escalate": "escalate", "no_action": "no_action"},
    )
    g.add_edge("execute", "verify")
    g.add_conditional_edges(
        "verify", _after_verify,
        {"replan": "replan", "escalate": "escalate", "finalise": "finalise"},
    )
    g.add_edge("replan", "decide")
    g.add_edge("escalate", "finalise")
    g.add_edge("no_action", "finalise")
    g.add_edge("finalise", END)
    return g.compile()


GRAPH = build_graph()


def run_agent(db: Session, request: dict) -> dict:
    run_id = request.get("run_id") or f"RUN-{uuid.uuid4().hex[:8].upper()}"
    run = AgentRun(id=run_id, scenario=request.get("scenario", "S1"), request=request)
    db.add(run)
    db.commit()

    state: AgentState = {
        "run_id": run_id,
        "request": request,
        "db": db,
        "attempt": 1,
        "trace": [],
        "status": RunStatus.RUNNING.value,
    }
    final = GRAPH.invoke(state, {"recursion_limit": 40})

    return {
        "run_id": run_id,
        "status": final.get("status"),
        "request": request,
        "computed": final.get("computed", {}),
        "decision": final.get("decision", {}),
        "verdict": final.get("verdict", {}),
        "execution": final.get("execution", {}),
        "verification": final.get("verification", {}),
        "evidence": final.get("evidence", {}),
        "trace": final.get("trace", []),
        "attempts": final.get("attempt", 1),
    }
