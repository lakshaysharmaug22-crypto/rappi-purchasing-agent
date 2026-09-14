from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..domain.reorder import ReorderResult
from . import llm
from .prompts import DECIDER_SYSTEM

VALID_ACTIONS = {"ACCEPT", "MODIFY", "REJECT", "INVESTIGATE"}


class Decision(BaseModel):
    action: str
    quantity: int = 0
    supplier_id: str | None = None
    reasoning: str = ""
    key_factors: list[str] = Field(default_factory=list)
    binding_constraint: str | None = None
    confidence: float = 0.5
    open_questions: list[str] = Field(default_factory=list)
    source: str = "llm"

    @field_validator("action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if v not in VALID_ACTIONS:
            raise ValueError(f"action must be one of {sorted(VALID_ACTIONS)}")
        return v

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @field_validator("quantity")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        return max(0, int(v))


def build_decision_payload(
    *,
    request: dict,
    evidence: dict,
    computed: ReorderResult,
    supplier: dict | None,
    prior_attempt: dict | None = None,
) -> str:
    payload = {
        "situation": request,
        "evidence": {
            "product": evidence["product"],
            "inventory": evidence["inventory"],
            "demand": evidence["demand"],
            "open_purchase_orders": evidence["open_pos"],
            "budget": evidence["budget"],
            "storage": evidence["storage"],
            "supplier_options": evidence["suppliers"]["suppliers"],
        },
        "policy_engine_output": computed.to_dict(),
        "preferred_supplier": supplier,
    }
    if prior_attempt:
        payload["previous_attempt"] = prior_attempt
        payload["instruction"] = (
            "Your previous action did not produce the intended outcome. Decide what "
            "to do now given what actually happened."
        )
    return json.dumps(payload, indent=2, default=str)


def _fallback_decision(
    *, request: dict, computed: ReorderResult, supplier: dict | None
) -> Decision:
    """Policy-only decision used when no LLM key is configured.

    It is intentionally conservative: it mirrors the policy engine and never
    claims high confidence, so an offline run is honest about being offline.
    """
    recommended = int(request.get("recommended_quantity") or 0)
    target = computed.recommended_quantity
    binding = computed.binding_constraints[0] if computed.binding_constraints else None
    open_questions: list[str] = []

    if target == 0 and computed.raw_need > 0:
        # There is real demand, but every route to buying it is blocked. That is
        # a buyer's problem, not a quiet "no order this cycle".
        action = "INVESTIGATE"
        open_questions.append(
            f"A need of {computed.raw_need} units cannot be sourced from this supplier: "
            f"{'; '.join(c.detail for c in computed.constraints if c.binding)}. "
            "Release budget, free storage, or approve an alternate supplier?"
        )
    elif target == 0:
        action = "REJECT"
    elif recommended and abs(recommended - target) <= 0.05 * max(target, 1):
        action = "ACCEPT"
    else:
        action = "MODIFY"

    factors = [f"Policy quantity {target} against recommendation {recommended}."]
    factors.extend(computed.notes)
    factors.extend(c.detail for c in computed.constraints if c.binding)

    # The fallback *is* the policy engine, so a clean, unconstrained agreement with
    # policy is genuinely high-confidence. Anything the constraints touched is not.
    if action == "INVESTIGATE":
        confidence = 0.4
    elif binding or computed.notes:
        confidence = 0.7
    else:
        confidence = 0.85

    return Decision(
        action=action,
        quantity=target,
        supplier_id=supplier["supplier_id"] if supplier else None,
        reasoning=(
            f"Policy engine computes a need of {computed.raw_need} units against a target "
            f"position of {computed.target_position} and a current position of "
            f"{computed.current_position}. After constraints the orderable quantity is "
            f"{target}. " + " ".join(computed.notes)
        ).strip(),
        key_factors=factors[:5],
        binding_constraint=binding,
        confidence=confidence,
        open_questions=open_questions,
        source="policy_fallback",
    )


def decide(
    *,
    request: dict,
    evidence: dict,
    computed: ReorderResult,
    supplier: dict | None,
    prior_attempt: dict | None = None,
) -> tuple[Decision, dict]:
    """Return the decision and a trace entry describing how it was produced."""
    if not llm.is_available():
        decision = _fallback_decision(request=request, computed=computed, supplier=supplier)
        return decision, {
            "node": "decide",
            "mode": "policy_fallback",
            "detail": "No ANTHROPIC_API_KEY configured; used the deterministic policy reasoner.",
        }

    user = build_decision_payload(
        request=request, evidence=evidence, computed=computed,
        supplier=supplier, prior_attempt=prior_attempt,
    )
    try:
        raw = llm.complete_json(DECIDER_SYSTEM, user)
        decision = Decision(**raw)
        return decision, {"node": "decide", "mode": "llm", "detail": "Model returned a valid decision."}
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        decision = _fallback_decision(request=request, computed=computed, supplier=supplier)
        decision.confidence = min(decision.confidence, 0.5)
        decision.open_questions.append(
            "The model returned an unusable decision; a human should confirm the fallback."
        )
        return decision, {
            "node": "decide",
            "mode": "fallback_after_error",
            "detail": f"Model output rejected: {exc}",
        }
    except Exception as exc:  # transport, auth, rate limit
        decision = _fallback_decision(request=request, computed=computed, supplier=supplier)
        decision.confidence = min(decision.confidence, 0.5)
        decision.open_questions.append("LLM unavailable; fallback decision needs review.")
        return decision, {
            "node": "decide",
            "mode": "fallback_after_transport_error",
            "detail": str(exc)[:200],
        }
