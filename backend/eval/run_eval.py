"""Evaluation harness.

The interesting question is not "did the agent output the right number" — a
lookup table does that. It is whether the agent got the information it needed,
respected the constraints, took the action its decision implied, checked the
result, and recovered when the result was not what it expected. Each scenario
is scored on all six, independently, so a run that gets the right answer for
the wrong reason still shows up as a partial pass.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.agent import llm  # noqa: E402
from app.agent.graph import run_agent  # noqa: E402
from app.db.seed import reset_and_seed  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.tools import supplier_api  # noqa: E402
from eval.scenarios import SCENARIOS, Scenario  # noqa: E402

DIMENSIONS = [
    "decision_correct",
    "evidence_complete",
    "constraints_respected",
    "action_appropriate",
    "result_validated",
    "recovered_on_failure",
]


def _grade(scenario: Scenario, result: dict) -> dict:
    decision = result.get("decision", {})
    verdict = result.get("verdict", {})
    verification = result.get("verification", {})
    computed = result.get("computed", {})
    det = verdict.get("deterministic", {})
    scores: dict[str, bool | None] = {}
    notes: list[str] = []

    # 1. Did it reach the right call?
    action_ok = decision.get("action") == scenario.expected_action
    qty_ok = True
    if scenario.expected_quantity is not None:
        tol = scenario.quantity_tolerance * max(scenario.expected_quantity, 1)
        qty_ok = abs(decision.get("quantity", -1) - scenario.expected_quantity) <= tol
    scores["decision_correct"] = action_ok and qty_ok
    if not action_ok:
        notes.append(f"action {decision.get('action')} != {scenario.expected_action}")
    if not qty_ok:
        notes.append(f"quantity {decision.get('quantity')} != {scenario.expected_quantity}")

    # 2. Did it actually go and look?
    evidence_check = next(
        (c for c in det.get("checks", []) if c["id"] == "evidence_complete"), None
    )
    scores["evidence_complete"] = bool(evidence_check and evidence_check["passed"])

    # 3. Did it stay inside the rules, and name the one that bound it?
    no_blocking = not det.get("blocking_failures")
    binding_ok = True
    if scenario.expected_binding_constraint:
        binding_ok = scenario.expected_binding_constraint in (
            computed.get("binding_constraints") or []
        )
        if not binding_ok:
            notes.append(
                f"expected binding constraint {scenario.expected_binding_constraint}, "
                f"got {computed.get('binding_constraints')}"
            )
    scores["constraints_respected"] = no_blocking and binding_ok

    # 4. Did the action match the decision?
    status = result.get("status")
    status_ok = scenario.expected_status is None or status == scenario.expected_status
    approvals_ok = True
    if scenario.expects_approval_task:
        approvals_ok = status in ("AWAITING_APPROVAL", "ESCALATED")
        if not approvals_ok:
            notes.append(f"expected a human task, status was {status}")
    scores["action_appropriate"] = status_ok and approvals_ok
    if not status_ok:
        notes.append(f"status {status} != {scenario.expected_status}")

    # 5. Did it check what actually happened?
    if decision.get("quantity", 0) == 0:
        scores["result_validated"] = True
        notes.append("no write to validate")
    else:
        scores["result_validated"] = bool(verification.get("verified"))

    # 6. Did it recover when the outcome differed from the intent?
    if scenario.expects_replan:
        replanned = result.get("attempts", 1) > 1
        resolved = (
            verification.get("matched_intent")
            or verification.get("need_now_covered")
            or status == "ESCALATED"
        )
        scores["recovered_on_failure"] = replanned and bool(resolved)
        if not replanned:
            notes.append("expected a replan, agent did not retry")
    else:
        scores["recovered_on_failure"] = None

    applicable = [v for v in scores.values() if v is not None]
    return {
        "scores": scores,
        "passed": all(applicable),
        "score": f"{sum(1 for v in applicable if v)}/{len(applicable)}",
        "notes": notes,
    }


def run(verbose: bool = False, only: str | None = None) -> dict:
    db = SessionLocal()
    results = []

    for scenario in SCENARIOS:
        if only and scenario.id != only:
            continue

        reset_and_seed(db)
        supplier_api.clear_failure_profile()
        if scenario.failure_profile:
            supplier_api.set_failure_profile(scenario.failure_profile)

        result = run_agent(db, dict(scenario.request))
        graded = _grade(scenario, result)
        supplier_api.clear_failure_profile()

        results.append(
            {
                "id": scenario.id,
                "title": scenario.title,
                "expected_action": scenario.expected_action,
                "actual_action": result.get("decision", {}).get("action"),
                "expected_quantity": scenario.expected_quantity,
                "actual_quantity": result.get("decision", {}).get("quantity"),
                "status": result.get("status"),
                "attempts": result.get("attempts"),
                "confidence": result.get("verdict", {}).get("effective_confidence"),
                "tags": scenario.tags,
                **graded,
                "trace": result.get("trace", []) if verbose else [],
            }
        )

    passed = sum(1 for r in results if r["passed"])
    summary = {
        "mode": "llm" if llm.is_available() else "policy_fallback",
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "by_dimension": {
            d: {
                "passed": sum(1 for r in results if r["scores"].get(d) is True),
                "applicable": sum(1 for r in results if r["scores"].get(d) is not None),
            }
            for d in DIMENSIONS
        },
        "results": results,
    }
    return summary


def _print(summary: dict) -> None:
    print(f"\nMode: {summary['mode']}")
    print(f"{summary['passed']}/{summary['total']} scenarios fully passed\n")

    header = f"{'ID':<5}{'Scenario':<42}{'Expected':<13}{'Actual':<13}{'Qty':<14}{'Score':<7}"
    print(header)
    print("-" * len(header))
    for r in summary["results"]:
        qty = f"{r['actual_quantity']}"
        if r["expected_quantity"] is not None:
            qty = f"{r['actual_quantity']} (exp {r['expected_quantity']})"
        mark = "" if r["passed"] else "  <-- review"
        print(
            f"{r['id']:<5}{r['title'][:40]:<42}{r['expected_action']:<13}"
            f"{str(r['actual_action']):<13}{qty:<14}{r['score']:<7}{mark}"
        )
        for note in r["notes"]:
            if not r["passed"]:
                print(f"     note: {note}")

    print("\nBy dimension")
    for dim, stats in summary["by_dimension"].items():
        if stats["applicable"]:
            print(f"  {dim:<24} {stats['passed']}/{stats['applicable']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the purchasing agent evaluation.")
    parser.add_argument("--verbose", action="store_true", help="include full traces")
    parser.add_argument("--only", help="run a single scenario by id, e.g. E9")
    parser.add_argument("--json", dest="as_json", help="write results to this path")
    args = parser.parse_args()

    summary = run(verbose=args.verbose, only=args.only)
    _print(summary)

    if args.as_json:
        pathlib.Path(args.as_json).write_text(json.dumps(summary, indent=2, default=str))
        print(f"\nWrote {args.as_json}")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
