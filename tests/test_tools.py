"""Unit tests for tools: explicit schemas, deterministic results, guided errors."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.tools import FunctionTool
from pydantic import ValidationError

from dragons_den import tools
from dragons_den.schemas import (
    AvailabilityOutput,
    CostEstimateOutput,
    GuidedToolError,
    ResidencyOutput,
    SecurityScanOutput,
)

ALL_TOOLS = [
    tools.lookup_well_architected_principle,
    tools.search_past_review_decisions,
    tools.scan_design_for_security_antipatterns,
    tools.calculate_composite_availability_slo,
    tools.estimate_monthly_gcp_cost,
    tools.check_data_residency_compliance,
    tools.create_remediation_backlog_item,
    tools.file_architecture_review_decision,
]


# ---------------------------------------------------------------- interface design
@pytest.mark.parametrize("fn", ALL_TOOLS, ids=lambda f: f.__name__)
def test_tools_have_comprehensive_docstrings(fn):
    doc = fn.__doc__ or ""
    assert len(doc.splitlines()[0]) > 20, "one-line purpose required"
    assert "Args:" in doc and "Returns:" in doc
    declared = FunctionTool(fn)._get_declaration()
    params = (declared.parameters_json_schema or {}).get("properties", {})
    for name in params:
        assert f"{name}:" in doc, f"parameter '{name}' undocumented in {fn.__name__}"


@pytest.mark.parametrize("fn", ALL_TOOLS, ids=lambda f: f.__name__)
def test_tool_names_are_descriptive_verb_object(fn):
    verb = fn.__name__.split("_")[0]
    assert verb in {"lookup", "search", "scan", "calculate", "estimate", "check", "create", "file"}
    assert len(fn.__name__.split("_")) >= 3


@pytest.mark.parametrize("fn", ALL_TOOLS, ids=lambda f: f.__name__)
def test_explicit_json_schema_exposed_to_llm(fn):
    decl = FunctionTool(fn)._get_declaration()
    schema = decl.parameters_json_schema
    assert schema and schema["type"] == "object" and schema.get("required")
    assert "tool_context" not in schema["properties"]


def test_hitl_wrapper_requires_confirmation():
    assert tools.file_decision_with_human_approval._require_confirmation is True


# ---------------------------------------------------------------- security
def test_security_scan_detects_antipatterns():
    out = tools.scan_design_for_security_antipatterns(
        "Public Cloud Run app. The DB password is hard-coded in the code; CI uses a service account key; "
        "ops team has Owner role; firewall allows 0.0.0.0/0 on port 22.",
        internet_facing=True,
    )
    SecurityScanOutput.model_validate(out)
    rules = {f["rule_id"] for f in out["findings"]}
    assert {"SEC-R1", "SEC-R2", "SEC-R3", "SEC-R4", "SEC-R8"} <= rules
    assert out["security_score"] < 30


def test_security_scan_rewards_controls():
    out = tools.scan_design_for_security_antipatterns(
        "Global external LB with Cloud Armor, IAP for admin, Workload Identity, secrets in Secret Manager, "
        "CMEK via Cloud KMS and a VPC Service Controls perimeter.",
        internet_facing=True,
    )
    assert out["findings"] == []
    assert out["security_score"] >= 95


# ---------------------------------------------------------------- reliability
def test_composite_slo_math():
    out = tools.calculate_composite_availability_slo(["cloud load balancing", "cloud run", "cloud sql zonal"], False, 99.9)
    AvailabilityOutput.model_validate(out)
    assert out["composite_availability_percent"] == pytest.approx(99.99 * 99.95 * 99.5 / 10000, abs=1e-3)
    assert out["meets_target"] is False
    assert "cloud sql zonal" in out["recommendation"]


def test_multi_region_improves_availability():
    single = tools.calculate_composite_availability_slo(["cloud run", "spanner"], False, 99.99)
    multi = tools.calculate_composite_availability_slo(["cloud run", "spanner"], True, 99.99)
    assert multi["composite_availability_percent"] > single["composite_availability_percent"]
    assert multi["meets_target"] is True


def test_unknown_components_guided_error():
    out = tools.calculate_composite_availability_slo(["mainframe", "quantum db"])
    err = GuidedToolError.model_validate(out)
    assert err.error_code == "NO_KNOWN_COMPONENTS" and "cloud run" in err.valid_values


# ---------------------------------------------------------------- finops
def test_cost_estimate_and_budget():
    out = tools.estimate_monthly_gcp_cost(
        [{"service": "Cloud Run", "quantity": 10}, {"service": "cloud sql", "quantity": 8}], monthly_budget_usd=500
    )
    CostEstimateOutput.model_validate(out)
    assert out["total_monthly_usd"] == pytest.approx(10 * 52 + 8 * 60)
    assert out["within_budget"] is False
    assert out["committed_use_discount_savings_usd"] > 0


def test_cost_unpriced_service_guided_error():
    out = tools.estimate_monthly_gcp_cost([{"service": "oracle exadata", "quantity": 1}])
    assert out["status"] == "error" and out["error_code"] == "UNPRICED_SERVICES"
    assert "cloud sql" in out["valid_values"]


def test_cost_invalid_quantity_is_validation_error_not_crash():
    out = tools.estimate_monthly_gcp_cost([{"service": "cloud run", "quantity": -5}])
    assert out["status"] == "error" and out["error_code"] == "INVALID_ARGUMENT"
    assert "quantity" in out["message"]


# ---------------------------------------------------------------- data & ai
def test_gdpr_residency_violation():
    out = tools.check_data_residency_compliance("confidential", ["us-central1", "europe-west1"], ["GDPR"], True)
    ResidencyOutput.model_validate(out)
    assert out["compliant"] is False
    assert any("us-central1" in v for v in out["violations"])
    assert "europe-west1" in out["approved_regions"]
    assert any("Model Armor" in c for c in out["required_controls"])


def test_eu_multiregion_alias_is_compliant():
    out = tools.check_data_residency_compliance("internal", ["eu"], ["gdpr"])
    assert out["compliant"] is True


def test_invalid_classification_guided_error():
    out = tools.check_data_residency_compliance("top-secret", ["us-east1"])
    assert out["error_code"] == "UNKNOWN_CLASSIFICATION" and "restricted" in out["valid_values"]


# ---------------------------------------------------------------- knowledge
def test_principle_lookup_ranks_relevant():
    out = tools.lookup_well_architected_principle("security", "service account keys")
    assert out["principles"][0]["principle_id"] == "SEC-01"


def test_principle_lookup_unknown_pillar():
    out = tools.lookup_well_architected_principle("vibes", "anything")
    assert out["status"] == "error" and "security" in out["valid_values"]


def test_search_past_decisions_finds_precedent():
    out = tools.search_past_review_decisions("single VM storefront black friday")
    assert out["matches"] and out["matches"][0]["decision_id"] == "DEN-2026-0012"


# ---------------------------------------------------------------- clerk / HITL
def _ctx(confirmed: bool | None = None, state: dict | None = None):
    calls = []
    ctx = SimpleNamespace(
        state=state if state is not None else {},
        tool_confirmation=None if confirmed is None else SimpleNamespace(confirmed=confirmed),
        request_confirmation=lambda **kw: calls.append(kw),
    )
    return ctx, calls


def test_backlog_item_appends_to_state():
    ctx, _ = _ctx()
    out = tools.create_remediation_backlog_item(
        "Enable Cloud SQL HA", "reliability", "high", "sre", "Regional instance with failover drill passed", ctx
    )
    assert out["item_id"].startswith("DEN-BL-")
    assert ctx.state["remediation_backlog"][0]["item_id"] == out["item_id"]


def test_backlog_item_invalid_enum():
    ctx, _ = _ctx()
    out = tools.create_remediation_backlog_item("Fix it now", "reliability", "catastrophic", "sre", "something good", ctx)
    assert out["error_code"] == "INVALID_ENUM" and "blocker" in out["valid_values"]


FILE_ARGS = dict(
    customer_alias="Volta Energy",
    design_title="Lakehouse v2",
    decision="im_in",
    den_score=90,
    executive_summary="Well designed lakehouse; approved as is with minor tweaks.",
    backlog_item_ids=[],
)


def test_file_decision_requests_confirmation_when_missing():
    ctx, calls = _ctx(confirmed=None)
    out = tools.file_architecture_review_decision(**FILE_ARGS, tool_context=ctx)
    assert out["status"] == "pending_human_approval" and calls
    assert "filed_decision_id" not in ctx.state


def test_file_decision_rejected_by_human():
    ctx, _ = _ctx(confirmed=False)
    out = tools.file_architecture_review_decision(**FILE_ARGS, tool_context=ctx)
    assert out["status"] == "rejected_by_human"


def test_file_decision_approved_persists():
    ctx, _ = _ctx(confirmed=True)
    out = tools.file_architecture_review_decision(**FILE_ARGS, tool_context=ctx)
    assert out["status"] == "filed" and ctx.state["filed_decision_id"] == out["decision_id"]


def test_file_decision_rejects_pii_in_alias():
    ctx, _ = _ctx(confirmed=True)
    out = tools.file_architecture_review_decision(**{**FILE_ARGS, "customer_alias": "bob@corp.com"}, tool_context=ctx)
    assert out["error_code"] == "INVALID_ARGUMENT"


def test_schema_forbids_extra_fields():
    from dragons_den.schemas import CostLineItemInput

    with pytest.raises(ValidationError):
        CostLineItemInput(service="cloud run", quantity=1, discount="100%")
