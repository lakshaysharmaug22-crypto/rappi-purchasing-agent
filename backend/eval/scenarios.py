"""Evaluation scenarios.

Each case states what the right answer is *and why*, so a reviewer can argue
with the expectation rather than just read a pass/fail. Quantities are the
output of the policy engine, which is separately unit-tested; the agent is
graded on whether it reached the same place and acted correctly on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scenario:
    id: str
    title: str
    why_it_matters: str
    request: dict
    expected_action: str
    expected_quantity: int | None = None
    quantity_tolerance: float = 0.0
    expected_binding_constraint: str | None = None
    expected_status: str | None = None
    expects_approval_task: bool = False
    expects_replan: bool = False
    failure_profile: dict | None = None
    notes: str = ""
    tags: list[str] = field(default_factory=list)


def _req(sku: str, node: str, qty: int, supplier: str = "SUP-ANDINA") -> dict:
    return {
        "scenario": "S1",
        "sku": sku,
        "node_id": node,
        "supplier_id": supplier,
        "period": "2026-09",
        "recommended_quantity": qty,
    }


SCENARIOS: list[Scenario] = [
    Scenario(
        id="E1",
        title="Recommendation is simply wrong",
        why_it_matters=(
            "The planner asks for 800 units of a SKU that is already covered. An agent "
            "that trusts its input buys 800 units of working capital it does not need."
        ),
        request=_req("SKU-ARE-001", "TURBO-BOG-01", 800),
        expected_action="REJECT",
        expected_quantity=0,
        expected_status="COMPLETED",
        notes=(
            "On hand 1,200 less 100 reserved plus 600 already inbound is a position of "
            "1,700 against a target of 1,535 over a 13-day horizon. Nothing is needed."
        ),
        tags=["recommendation-review", "over-buy"],
    ),
    Scenario(
        id="E2",
        title="Need is real but below the supplier minimum",
        why_it_matters=(
            "The correct answer is neither the recommendation nor the raw need. Buying "
            "the MOQ is right here; refusing to buy would stock out."
        ),
        request=_req("SKU-AGU-002", "TURBO-BOG-01", 300),
        expected_action="MODIFY",
        expected_quantity=400,
        expected_binding_constraint="moq_lift",
        expects_approval_task=True,
        notes="Need of 300 lifts to the Andina MOQ of 400, which budget and storage both allow.",
        tags=["moq", "modify-up"],
    ),
    Scenario(
        id="E3",
        title="Budget binds before need is met",
        why_it_matters=(
            "The agent must buy what it can afford, say so, and not silently pretend "
            "the gap is closed."
        ),
        request=_req("SKU-CAF-003", "TURBO-MDE-02", 1000),
        expected_action="MODIFY",
        expected_quantity=450,
        expected_binding_constraint="budget",
        expects_approval_task=True,
        notes=(
            "Need is 995 units. Medellín has 3,200 left in period budget at 6.50 a unit, "
            "so 492 units are affordable, rounded down to the 50-unit lot."
        ),
        tags=["budget", "constraint"],
    ),
    Scenario(
        id="E4",
        title="Storage binds on a bulky SKU",
        why_it_matters=(
            "Volume, not units, is what fills a dark store. A six-pack of water occupies "
            "three times a jar of spread."
        ),
        request=_req("SKU-AGU-002", "TURBO-MDE-02", 2000),
        expected_action="MODIFY",
        expected_quantity=700,
        expected_binding_constraint="storage",
        expects_approval_task=True,
        notes="Need is 1,430 units but free storage after the 5% buffer allows only 750.",
        tags=["storage", "constraint"],
    ),
    Scenario(
        id="E5",
        title="Demand has run far ahead of forecast",
        why_it_matters=(
            "Planning against a stale forecast under-buys a SKU that is selling 2.6x "
            "plan. The agent must notice the divergence and say which number it used."
        ),
        request=_req("SKU-SNK-005", "TURBO-BOG-01", 200),
        expected_action="MODIFY",
        expected_quantity=1500,
        expects_approval_task=True,
        notes=(
            "Forecast is 80/day, last 7 days ran 210/day. Planning against 210 gives a "
            "need of 1,470, rounded up to the 100-unit lot."
        ),
        tags=["forecast-drift", "modify-up"],
    ),
    Scenario(
        id="E6",
        title="Perishable cannot be bought to full cover",
        why_it_matters=(
            "The cheapest legal order is the wrong order if a third of it expires on the "
            "shelf. Shelf life must cap cover before MOQ is even considered."
        ),
        request=_req("SKU-LEC-004", "TURBO-BOG-01", 1500),
        expected_action="MODIFY",
        expected_quantity=600,
        expected_binding_constraint="perishable_cover",
        expects_approval_task=True,
        notes="Need is 970 units, but 14-day shelf life allows only 8.4 days of cover.",
        tags=["perishable", "constraint"],
    ),
    Scenario(
        id="E7",
        title="Recommendation is correct and small enough to act on alone",
        why_it_matters=(
            "Autonomy has to be demonstrated, not just claimed. This is the case where "
            "the agent should execute without a human and be right to."
        ),
        request=_req("SKU-CAF-003", "TURBO-BOG-01", 550),
        expected_action="ACCEPT",
        expected_quantity=550,
        expected_status="AUTO_EXECUTED",
        notes="Need is exactly 550, order value 3,575 sits under the 5,000 autonomy ceiling.",
        tags=["accept", "autonomous"],
    ),
    Scenario(
        id="E8",
        title="Every route to buying is blocked",
        why_it_matters=(
            "There is genuine demand and no legal way to meet it. Closing this quietly "
            "as 'no order' would hide a stockout from the buyer."
        ),
        request=_req("SKU-ARE-001", "TURBO-SAO-03", 600),
        expected_action="INVESTIGATE",
        expected_quantity=0,
        expected_binding_constraint="moq_unreachable",
        expected_status="ESCALATED",
        expects_approval_task=True,
        notes=(
            "São Paulo has 400 of budget left, which affords 166 units, below the Andina "
            "MOQ of 500. The 596-unit need must reach a human."
        ),
        tags=["escalation", "constraint"],
    ),
    Scenario(
        id="E9",
        title="Supplier confirms only half the order",
        why_it_matters=(
            "This is the whole feedback loop in one case. The write succeeds, the outcome "
            "differs from the intent, and the agent must notice and re-source."
        ),
        request=_req("SKU-CAF-003", "TURBO-BOG-01", 550),
        expected_action="MODIFY",
        expected_status="AUTO_EXECUTED",
        expects_replan=True,
        failure_profile={
            "SUP-ANDINA": {"fill_rate": 0.5, "round_to": 50,
                           "message": "Roaster capacity shortfall this cycle."}
        },
        notes=(
            "Andina confirms 250 of 550. The agent detects the 300-unit shortfall and "
            "sources the residual from Caribe, then verifies full confirmation."
        ),
        tags=["partial-fulfilment", "replan", "feedback-loop"],
    ),
    Scenario(
        id="E10",
        title="Supplier rejects the order outright",
        why_it_matters=(
            "A zero-fill is different from a partial fill. The residual is the whole "
            "order and the alternate supplier has to carry all of it."
        ),
        request=_req("SKU-CAF-003", "TURBO-BOG-01", 550),
        expected_action="ACCEPT",
        expects_replan=True,
        failure_profile={
            "SUP-ANDINA": {"fill_rate": 0.0, "message": "SKU discontinued at this supplier."}
        },
        notes=(
            "Andina confirms nothing, so the agent re-sources the full need from Caribe. "
            "The reported action is the second pass, not the first: after a replan the "
            "recommendation under review is the recomputed residual, so agreeing with it "
            "is ACCEPT rather than MODIFY. This expectation was written as MODIFY first "
            "and the evaluation caught it — the harness is worth more when it can "
            "disagree with the person who wrote it."
        ),
        tags=["rejection", "replan", "feedback-loop"],
    ),
]
