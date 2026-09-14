"""Judgement half of the judge.

The LLM judge only ever reduces confidence. It cannot rescue a decision that
failed a deterministic check, and it cannot raise a decision above what the
rules allow. That asymmetry is deliberate: a second model is useful for
catching reasoning that is unsupported or self-contradictory, and dangerous
as a source of permission.
"""

from __future__ import annotations

import json

from ..agent import llm
from ..agent.prompts import JUDGE_SYSTEM
from ..domain.evidence import load_rubric

JUDGEMENT_IDS = {
    "reasoning_cites_evidence",
    "no_unsupported_claims",
    "constraint_tradeoff_explained",
    "decision_matches_reasoning",
}


def review(*, decision: dict, evidence: dict, computed: dict) -> dict:
    if not llm.is_available():
        return {
            "available": False,
            "checks": [],
            "confidence_adjustment": 0.0,
            "summary": "LLM judge skipped; no API key configured.",
        }

    payload = json.dumps(
        {
            "agent_decision": decision,
            "policy_engine_output": computed,
            "evidence": {
                "product": evidence["product"],
                "inventory": evidence["inventory"],
                "demand": evidence["demand"],
                "open_purchase_orders": evidence["open_pos"],
                "budget": evidence["budget"],
                "storage": evidence["storage"],
                "supplier_options": evidence["suppliers"]["suppliers"],
            },
        },
        indent=2,
        default=str,
    )

    try:
        raw = llm.complete_json(JUDGE_SYSTEM, payload)
    except Exception as exc:
        return {
            "available": False,
            "checks": [],
            "confidence_adjustment": 0.0,
            "summary": f"LLM judge unavailable: {str(exc)[:160]}",
        }

    checks = []
    for c in raw.get("checks", []):
        if c.get("id") in JUDGEMENT_IDS:
            checks.append(
                {
                    "id": c["id"],
                    "passed": bool(c.get("passed")),
                    "detail": str(c.get("detail", ""))[:300],
                }
            )

    penalty = load_rubric()["scoring"]["judgement_failure_penalty"]
    failures = [c for c in checks if not c["passed"]]
    adjustment = float(raw.get("confidence_adjustment", 0.0))
    adjustment = max(-0.4, min(0.1, adjustment))
    if failures:
        adjustment = min(adjustment, -penalty * len(failures))

    return {
        "available": True,
        "checks": checks,
        "failures": [c["id"] for c in failures],
        "confidence_adjustment": round(adjustment, 3),
        "summary": str(raw.get("summary", ""))[:300],
    }
