"""Knowledge & precedent tools shared by all dragons."""

from __future__ import annotations

from typing import Any

from dragons_den.memory.decision_store import get_decision_store
from dragons_den.schemas import (
    PastDecision,
    PastDecisionSearchInput,
    PastDecisionSearchOutput,
    Pillar,
    Principle,
    PrincipleLookupInput,
    PrincipleLookupOutput,
    to_tool_result,
)
from dragons_den.tools._common import den_tool, guided_error, principles


@den_tool
def lookup_well_architected_principle(pillar: str, topic: str) -> dict[str, Any]:
    """Look up Google Cloud Well-Architected Framework principles for a pillar and topic.

    Use this to ground every finding in an official principle (cite its principle_id,
    e.g. "SEC-01") instead of relying on memory.

    Args:
        pillar: Well-Architected pillar. One of "security", "reliability",
            "cost_optimization", "operational_excellence", "performance_optimization",
            "data_and_ai".
        topic: Short keyword phrase describing what you are checking, for example
            "service account keys", "multi-region failover" or "committed use discounts".

    Returns:
        dict: ``{"status": "success", "pillar", "topic", "principles": [{principle_id,
        title, guidance, keywords}]}`` ranked by relevance, or a guided error with
        ``valid_values`` when the pillar is unknown.
    """
    valid = [p.value for p in Pillar]
    if pillar not in valid:
        return guided_error(
            "UNKNOWN_PILLAR",
            f"'{pillar}' is not a Well-Architected pillar.",
            "Call lookup_well_architected_principle again with one of the valid pillar values.",
            valid_values=valid,
        )
    args = PrincipleLookupInput(pillar=Pillar(pillar), topic=topic)
    words = set(args.topic.lower().split())
    ranked: list[tuple[int, dict[str, Any]]] = []
    for p in principles():
        if p["pillar"] != args.pillar.value:
            continue
        haystack = " ".join([p["title"], p["guidance"], *p["keywords"]]).lower()
        score = sum(1 for w in words if w in haystack) + sum(3 for k in p["keywords"] if k in args.topic.lower())
        ranked.append((score, p))
    ranked.sort(key=lambda x: -x[0])
    top = [Principle(**p) for _, p in ranked[:3]]
    return to_tool_result(PrincipleLookupOutput(pillar=args.pillar, topic=args.topic, principles=top))


@den_tool
def search_past_review_decisions(query: str, limit: int = 3) -> dict[str, Any]:
    """Search the persistent archive of previously filed Dragons' Den review decisions.

    Use this to find precedent for similar architectures (e.g. "single VM storefront",
    "Gemini clinical summarisation", "Spanner fraud scoring") so verdicts stay
    consistent across engagements.

    Args:
        query: Free-text keywords describing the architecture or customer pattern.
        limit: Maximum number of matches to return (1-10). Defaults to 3.

    Returns:
        dict: ``{"status": "success", "query", "matches": [{decision_id, customer_alias,
        title, decision, den_score, summary}]}``. ``matches`` may be empty, in which case
        proceed without precedent.
    """
    args = PastDecisionSearchInput(query=query, limit=limit)
    rows = get_decision_store().search(args.query, args.limit)
    return to_tool_result(PastDecisionSearchOutput(query=args.query, matches=[PastDecision(**r) for r in rows]))
