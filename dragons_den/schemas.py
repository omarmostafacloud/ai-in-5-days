"""Explicit, strict Pydantic schemas for every tool input/output and agent output.

Every tool in ``dragons_den.tools`` validates its arguments against an ``*Input``
model *before* doing any work and serialises its result through an ``*Output``
model. LLM agents that must emit structured data (pitch intake, dragon verdicts,
chair verdict, critic report) are constrained with ``output_schema``.

All models use ``extra="forbid"`` so hallucinated arguments are rejected and
turned into guided recovery instructions instead of silently ignored.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

STRICT = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
# Shared enums
# --------------------------------------------------------------------------- #
class Pillar(str, Enum):
    """Google Cloud Well-Architected Framework pillars judged by the dragons."""

    SECURITY = "security"
    RELIABILITY = "reliability"
    COST = "cost_optimization"
    OPERATIONAL_EXCELLENCE = "operational_excellence"
    PERFORMANCE = "performance_optimization"
    DATA_AI = "data_and_ai"


class Severity(str, Enum):
    """Severity of a review finding / remediation backlog item."""

    BLOCKER = "blocker"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class DenDecision(str, Enum):
    """The final 'Dragons' Den' decision on a pitched architecture."""

    IM_IN = "im_in"  # approved as-is
    IM_IN_WITH_CONDITIONS = "im_in_with_conditions"  # approved with remediation backlog
    IM_OUT = "im_out"  # rejected, redesign required


# --------------------------------------------------------------------------- #
# Guided error envelope (returned to the LLM instead of raising)
# --------------------------------------------------------------------------- #
class GuidedToolError(BaseModel):
    """Structured error returned to the LLM with explicit recovery instructions."""

    model_config = STRICT

    status: Literal["error"] = "error"
    error_code: str = Field(description="Machine readable error code, e.g. INVALID_ARGUMENT.")
    message: str = Field(description="Human readable explanation of what went wrong.")
    recovery_instructions: str = Field(description="Concrete next step the agent should take to recover.")
    retryable: bool = Field(default=True, description="Whether retrying with fixed args can succeed.")
    valid_values: list[str] | None = Field(default=None, description="Accepted values for the offending argument, if enumerable.")
    suggested_next_tool: str | None = Field(default=None, description="Tool the agent should call next, if any.")


# --------------------------------------------------------------------------- #
# Tool: lookup_well_architected_principle
# --------------------------------------------------------------------------- #
class PrincipleLookupInput(BaseModel):
    model_config = STRICT
    pillar: Pillar
    topic: str = Field(min_length=2, max_length=80)


class Principle(BaseModel):
    model_config = STRICT
    principle_id: str
    pillar: Pillar
    title: str
    guidance: str
    keywords: list[str]


class PrincipleLookupOutput(BaseModel):
    model_config = STRICT
    status: Literal["success"] = "success"
    pillar: Pillar
    topic: str
    principles: list[Principle]


# --------------------------------------------------------------------------- #
# Tool: scan_design_for_security_antipatterns
# --------------------------------------------------------------------------- #
class SecurityScanInput(BaseModel):
    model_config = STRICT
    design_description: str = Field(min_length=20, max_length=20000)
    internet_facing: bool = False


class SecurityFinding(BaseModel):
    model_config = STRICT
    rule_id: str
    severity: Severity
    title: str
    evidence: str
    recommendation: str


class SecurityScanOutput(BaseModel):
    model_config = STRICT
    status: Literal["success"] = "success"
    findings: list[SecurityFinding]
    controls_detected: list[str]
    security_score: int = Field(ge=0, le=100)


# --------------------------------------------------------------------------- #
# Tool: calculate_composite_availability_slo
# --------------------------------------------------------------------------- #
class AvailabilityInput(BaseModel):
    model_config = STRICT
    serial_components: list[str] = Field(min_length=1, max_length=25)
    multi_region: bool = False
    target_slo_percent: float = Field(default=99.9, ge=90.0, le=99.999)

    @field_validator("serial_components")
    @classmethod
    def _normalise(cls, value: list[str]) -> list[str]:
        return [v.strip().lower() for v in value if v.strip()]


class AvailabilityOutput(BaseModel):
    model_config = STRICT
    status: Literal["success"] = "success"
    per_component_sla: dict[str, float]
    composite_availability_percent: float
    target_slo_percent: float
    meets_target: bool
    allowed_downtime_minutes_per_month: float
    unknown_components: list[str]
    recommendation: str


# --------------------------------------------------------------------------- #
# Tool: estimate_monthly_gcp_cost
# --------------------------------------------------------------------------- #
class CostLineItemInput(BaseModel):
    model_config = STRICT
    service: str = Field(min_length=2, max_length=60)
    quantity: float = Field(gt=0, le=1_000_000)


class CostEstimateInput(BaseModel):
    model_config = STRICT
    line_items: list[CostLineItemInput] = Field(min_length=1, max_length=40)
    monthly_budget_usd: float | None = Field(default=None, gt=0)


class CostLineItemOutput(BaseModel):
    model_config = STRICT
    service: str
    quantity: float
    unit: str
    unit_price_usd: float
    monthly_cost_usd: float


class CostEstimateOutput(BaseModel):
    model_config = STRICT
    status: Literal["success"] = "success"
    line_items: list[CostLineItemOutput]
    total_monthly_usd: float
    committed_use_discount_savings_usd: float
    within_budget: bool | None
    optimisation_tips: list[str]


# --------------------------------------------------------------------------- #
# Tool: check_data_residency_compliance
# --------------------------------------------------------------------------- #
class ResidencyInput(BaseModel):
    model_config = STRICT
    data_classification: DataClassification
    storage_regions: list[str] = Field(min_length=1, max_length=20)
    regulations: list[str] = Field(default_factory=list, max_length=10)
    uses_generative_ai: bool = False


class ResidencyOutput(BaseModel):
    model_config = STRICT
    status: Literal["success"] = "success"
    compliant: bool
    violations: list[str]
    required_controls: list[str]
    approved_regions: list[str]


# --------------------------------------------------------------------------- #
# Tool: search_past_review_decisions
# --------------------------------------------------------------------------- #
class PastDecisionSearchInput(BaseModel):
    model_config = STRICT
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=3, ge=1, le=10)


class PastDecision(BaseModel):
    model_config = STRICT
    decision_id: str
    customer_alias: str
    title: str
    decision: DenDecision
    den_score: int
    summary: str


class PastDecisionSearchOutput(BaseModel):
    model_config = STRICT
    status: Literal["success"] = "success"
    query: str
    matches: list[PastDecision]


# --------------------------------------------------------------------------- #
# Tool: create_remediation_backlog_item
# --------------------------------------------------------------------------- #
class BacklogItemInput(BaseModel):
    model_config = STRICT
    title: str = Field(min_length=5, max_length=120)
    pillar: Pillar
    severity: Severity
    owner_role: Literal["customer_architect", "pso_consultant", "security_engineer", "sre", "finops_lead", "data_engineer"]
    acceptance_criteria: str = Field(min_length=10, max_length=1000)


class BacklogItemOutput(BaseModel):
    model_config = STRICT
    status: Literal["success"] = "success"
    item_id: str
    title: str
    pillar: Pillar
    severity: Severity
    owner_role: str
    acceptance_criteria: str


# --------------------------------------------------------------------------- #
# Tool: file_architecture_review_decision (HITL-gated, high stakes)
# --------------------------------------------------------------------------- #
class FileDecisionInput(BaseModel):
    model_config = STRICT
    customer_alias: str = Field(min_length=2, max_length=60, pattern=r"^[A-Za-z0-9 _\-]+$")
    design_title: str = Field(min_length=3, max_length=120)
    decision: DenDecision
    den_score: int = Field(ge=0, le=100)
    executive_summary: str = Field(min_length=20, max_length=4000)
    backlog_item_ids: list[str] = Field(default_factory=list, max_length=50)


class FileDecisionOutput(BaseModel):
    model_config = STRICT
    status: Literal["filed", "rejected_by_human", "pending_human_approval"]
    decision_id: str | None = None
    message: str


# --------------------------------------------------------------------------- #
# Agent output schemas (constrain LLM generations)
# --------------------------------------------------------------------------- #
class DesignPitch(BaseModel):
    """Structured digest of the architecture pitch, produced by the intake analyst."""

    model_config = STRICT
    customer_alias: str = Field(description="Non-identifying customer codename, e.g. 'Acme Retail'.")
    design_title: str
    business_goal: str
    components: list[str] = Field(description="GCP / third-party components, lower-case, e.g. 'cloud run'.")
    request_path: list[str] = Field(description="Ordered serial components on the critical user request path.")
    data_classification: DataClassification
    storage_regions: list[str]
    regulations: list[str]
    internet_facing: bool
    uses_generative_ai: bool
    target_slo_percent: float
    monthly_budget_usd: float | None
    estimated_usage: list[CostLineItemInput] = Field(
        description="Best-effort monthly usage per billable service for the FinOps dragon."
    )
    complexity: Literal["low", "medium", "high"]


class DragonFinding(BaseModel):
    model_config = STRICT
    severity: Severity
    title: str
    evidence: str
    recommendation: str


class DragonVerdict(BaseModel):
    """Verdict of a single specialist dragon."""

    model_config = STRICT
    dragon: Literal["security_dragon", "reliability_dragon", "finops_dragon", "data_ai_dragon"]
    pillar_score: int = Field(ge=0, le=100)
    stance: Literal["in", "in_with_conditions", "out"]
    one_liner: str = Field(description="Punchy Dragons' Den style quip summarising the stance.")
    findings: list[DragonFinding]
    tool_evidence: list[str] = Field(description="Names of tools whose outputs grounded this verdict.")


class ChairVerdict(BaseModel):
    """Synthesised panel verdict from the Den Chair."""

    model_config = STRICT
    customer_alias: str
    design_title: str
    decision: DenDecision
    den_score: int = Field(ge=0, le=100)
    executive_summary: str
    top_risks: list[DragonFinding]
    remediation_plan: list[str]


class CriticReport(BaseModel):
    """Self-evaluation report from the verdict critic."""

    model_config = STRICT
    grounded: bool
    consistent_with_scores: bool
    safe_and_professional: bool
    issues: list[str]
    approved: bool


def to_tool_result(model: BaseModel) -> dict[str, Any]:
    """Serialise a schema instance into the plain dict ADK returns to the LLM."""
    return model.model_dump(mode="json")
