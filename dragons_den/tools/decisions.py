"""Back-office tools of the review clerk, including the HITL-gated decision filing."""

from __future__ import annotations

import uuid
from typing import Any

from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext

from dragons_den.memory.decision_store import get_decision_store
from dragons_den.observability.logging import log_event
from dragons_den.schemas import (
    BacklogItemInput,
    BacklogItemOutput,
    DenDecision,
    FileDecisionInput,
    FileDecisionOutput,
    Pillar,
    Severity,
    to_tool_result,
)
from dragons_den.tools._common import den_tool, guided_error

_OWNER_ROLES = ["customer_architect", "pso_consultant", "security_engineer", "sre", "finops_lead", "data_engineer"]


@den_tool
def create_remediation_backlog_item(
    title: str,
    pillar: str,
    severity: str,
    owner_role: str,
    acceptance_criteria: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Create one remediation backlog item for the customer engagement plan.

    Call once per top risk the Den Chair identified, before filing the decision.

    Args:
        title: Imperative, specific title, e.g. "Replace SA keys with Workload Identity".
        pillar: Well-Architected pillar (security, reliability, cost_optimization,
            operational_excellence, performance_optimization, data_and_ai).
        severity: blocker, high, medium or low.
        owner_role: customer_architect, pso_consultant, security_engineer, sre,
            finops_lead or data_engineer.
        acceptance_criteria: Verifiable definition of done (>= 10 characters).

    Returns:
        dict: ``{"status": "success", "item_id", ...}``; the item is also appended to
        session state ``remediation_backlog``. Returns a guided error for invalid enums.
    """
    for name, value, valid in (
        ("pillar", pillar, [p.value for p in Pillar]),
        ("severity", severity, [s.value for s in Severity]),
        ("owner_role", owner_role, _OWNER_ROLES),
    ):
        if value not in valid:
            return guided_error(
                "INVALID_ENUM",
                f"'{value}' is not a valid {name}.",
                f"Call create_remediation_backlog_item again with {name} set to one of valid_values.",
                valid_values=valid,
            )
    args = BacklogItemInput(
        title=title,
        pillar=Pillar(pillar),
        severity=Severity(severity),
        owner_role=owner_role,  # type: ignore[arg-type]
        acceptance_criteria=acceptance_criteria,
    )
    item = BacklogItemOutput(item_id=f"DEN-BL-{uuid.uuid4().hex[:6].upper()}", **args.model_dump())
    backlog = list(tool_context.state.get("remediation_backlog", []))
    backlog.append(to_tool_result(item))
    tool_context.state["remediation_backlog"] = backlog
    return to_tool_result(item)


@den_tool
def file_architecture_review_decision(
    customer_alias: str,
    design_title: str,
    decision: str,
    den_score: int,
    executive_summary: str,
    backlog_item_ids: list[str],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """File the OFFICIAL architecture review decision into the engagement record (HIGH STAKES).

    This is irreversible and visible to the customer and PSO leadership, so it is
    wrapped with ``require_confirmation=True``: ADK pauses the run and asks the human
    reviewer to approve or reject before this body executes. Never call it before the
    verdict critic approved the verdict.

    Args:
        customer_alias: Customer codename (letters, digits, spaces, '-' or '_' only - no
            real names or emails).
        design_title: Title of the reviewed design.
        decision: "im_in", "im_in_with_conditions" or "im_out".
        den_score: Overall Den Score 0-100 from the chair verdict.
        executive_summary: 2-6 sentence summary of the verdict and conditions.
        backlog_item_ids: IDs returned by create_remediation_backlog_item.

    Returns:
        dict: ``{"status": "filed", "decision_id", "message"}`` when approved, or
        ``{"status": "rejected_by_human", ...}`` if the reviewer declined.
    """
    valid = [d.value for d in DenDecision]
    if decision not in valid:
        return guided_error(
            "INVALID_DECISION",
            f"'{decision}' is not a valid Den decision.",
            "Use the decision from the chair verdict; it must be one of valid_values.",
            valid_values=valid,
        )
    args = FileDecisionInput(
        customer_alias=customer_alias,
        design_title=design_title,
        decision=DenDecision(decision),
        den_score=den_score,
        executive_summary=executive_summary,
        backlog_item_ids=backlog_item_ids,
    )

    # Defence in depth: even if wrapped without require_confirmation, refuse to file
    # unless an explicit human confirmation is attached to this tool call.
    confirmation = getattr(tool_context, "tool_confirmation", None)
    if confirmation is None:
        tool_context.request_confirmation(
            hint=f"Approve filing '{args.decision.value}' (Den Score {args.den_score}) for {args.customer_alias}?",
            payload=args.model_dump(mode="json"),
        )
        return to_tool_result(FileDecisionOutput(status="pending_human_approval", message="Waiting for human reviewer approval."))
    if not confirmation.confirmed:
        log_event("HITL_REJECTED", "Human reviewer rejected decision filing", phase="OUTCOME", customer=args.customer_alias)
        return to_tool_result(
            FileDecisionOutput(
                status="rejected_by_human",
                message="The human reviewer declined. Ask what to change, revise the verdict, and do not re-file "
                "without new approval.",
            )
        )

    decision_id = get_decision_store().save(
        {
            "customer_alias": args.customer_alias,
            "title": args.design_title,
            "decision": args.decision.value,
            "den_score": args.den_score,
            "summary": args.executive_summary,
        }
    )
    tool_context.state["filed_decision_id"] = decision_id
    log_event("HITL_APPROVED", "Decision filed after human approval", phase="OUTCOME", decision_id=decision_id)
    return to_tool_result(FileDecisionOutput(status="filed", decision_id=decision_id, message=f"Decision {decision_id} filed."))


# Human-in-the-loop hook: ADK pauses before execution and asks for confirmation.
file_decision_with_human_approval = FunctionTool(file_architecture_review_decision, require_confirmation=True)
