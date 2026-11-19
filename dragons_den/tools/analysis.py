"""Deterministic analysis tools used by the specialist dragons.

These are intentionally rule-based (no LLM inside) so that every dragon's opinion is
grounded in reproducible numbers that the eval suite can regression-test.
"""

from __future__ import annotations

import re
from typing import Any

from dragons_den.schemas import (
    AvailabilityInput,
    AvailabilityOutput,
    CostEstimateInput,
    CostEstimateOutput,
    CostLineItemInput,
    CostLineItemOutput,
    DataClassification,
    ResidencyInput,
    ResidencyOutput,
    SecurityFinding,
    SecurityScanInput,
    SecurityScanOutput,
    Severity,
    to_tool_result,
)
from dragons_den.tools._common import den_tool, guided_error, match_catalog_key, reference_catalog

# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
_SEC_RULES: list[tuple[str, Severity, str, str, str]] = [
    # (rule_id, severity, regex, title, recommendation)
    (
        "SEC-R1",
        Severity.BLOCKER,
        r"(hard[- ]?coded|plain[- ]?text|in the code|committed).{0,40}(password|secret|api key|credential)|"
        r"(password|secret|api key|credential)s?.{0,40}(hard[- ]?coded|in (the )?(code|repo|image|env file))",
        "Credentials embedded in code or config",
        "Move secrets to Secret Manager and inject at runtime (SEC-03).",
    ),
    (
        "SEC-R2",
        Severity.HIGH,
        r"(service account|sa)[ -]?(json )?keys?|downloaded key|key file",
        "Long-lived service account keys",
        "Use Workload Identity / Workload Identity Federation instead of keys (SEC-01).",
    ),
    (
        "SEC-R3",
        Severity.HIGH,
        r"\b(roles/)?(owner|editor)\b( role)?",
        "Primitive Owner/Editor roles granted",
        "Replace with least-privilege predefined or custom roles (SEC-01).",
    ),
    (
        "SEC-R4",
        Severity.HIGH,
        r"0\.0\.0\.0/0|open to the internet|public ssh|ssh from anywhere|port 22 open|rdp open",
        "Management ports exposed to the internet",
        "Close 0.0.0.0/0 ingress; use IAP TCP forwarding for admin access (SEC-02).",
    ),
    (
        "SEC-R5",
        Severity.MEDIUM,
        r"\bhttp://|plain http|without tls|no tls|unencrypted traffic",
        "Unencrypted transport",
        "Terminate TLS at the load balancer and enforce HTTPS redirects (SEC-02).",
    ),
    (
        "SEC-R6",
        Severity.MEDIUM,
        r"public (bucket|gcs)|allusers|allauthenticatedusers",
        "Publicly readable storage",
        "Enforce public access prevention and uniform bucket-level access (SEC-02).",
    ),
    (
        "SEC-R7",
        Severity.MEDIUM,
        r"self[- ]managed (mysql|postgres|database|db)|database on (a )?vm",
        "Self-managed database on VMs",
        "Prefer Cloud SQL/AlloyDB with automated patching and backups (SEC-04, REL-04).",
    ),
]

_CONTROLS: dict[str, str] = {
    "cloud armor": r"cloud armor|\bwaf\b",
    "identity-aware proxy": r"\biap\b|identity[- ]aware proxy",
    "secret manager": r"secret manager",
    "workload identity": r"workload identity",
    "cmek / cloud kms": r"\bcmek\b|cloud kms|customer[- ]managed (encryption )?keys?",
    "vpc service controls": r"vpc[- ]?sc\b|vpc service controls",
    "private service connect": r"private service connect|\bpsc\b|private ip",
    "binary authorization": r"binary authori[sz]ation",
    "security command center": r"security command center|\bscc\b",
    "audit logging": r"audit log",
    "model armor": r"model armor",
}

_SEVERITY_PENALTY = {Severity.BLOCKER: 40, Severity.HIGH: 18, Severity.MEDIUM: 8, Severity.LOW: 3}


@den_tool
def scan_design_for_security_antipatterns(design_description: str, internet_facing: bool = False) -> dict[str, Any]:
    """Scan an architecture description for known Google Cloud security anti-patterns.

    Detects embedded credentials, service-account keys, primitive IAM roles, open
    management ports, unencrypted transport, public buckets and self-managed databases,
    and inventories positive controls (Cloud Armor, IAP, CMEK, VPC-SC, ...).

    Args:
        design_description: Full free-text architecture description as pitched by the
            consultant (minimum 20 characters).
        internet_facing: True if any component is reachable from the public internet.
            Enables the "missing WAF" and "missing IAP" perimeter checks.

    Returns:
        dict: ``{"status": "success", "findings": [{rule_id, severity, title, evidence,
        recommendation}], "controls_detected": [...], "security_score": 0-100}``.
    """
    args = SecurityScanInput(design_description=design_description, internet_facing=internet_facing)
    text = args.design_description.lower()
    findings: list[SecurityFinding] = []
    for rule_id, severity, pattern, title, rec in _SEC_RULES:
        if m := re.search(pattern, text):
            start = max(m.start() - 40, 0)
            findings.append(
                SecurityFinding(
                    rule_id=rule_id,
                    severity=severity,
                    title=title,
                    evidence=f"...{args.design_description[start : m.end() + 40].strip()}...",
                    recommendation=rec,
                )
            )
    controls = [name for name, pattern in _CONTROLS.items() if re.search(pattern, text)]
    if args.internet_facing and "cloud armor" not in controls:
        findings.append(
            SecurityFinding(
                rule_id="SEC-R8",
                severity=Severity.HIGH,
                title="Internet-facing service without WAF",
                evidence="internet_facing=true and no Cloud Armor / WAF mentioned",
                recommendation="Front with External Application Load Balancer + Cloud Armor policies (SEC-02).",
            )
        )
    if args.internet_facing and "identity-aware proxy" not in controls and re.search(r"admin|back[- ]?office|console", text):
        findings.append(
            SecurityFinding(
                rule_id="SEC-R9",
                severity=Severity.MEDIUM,
                title="Admin surface without Identity-Aware Proxy",
                evidence="admin/back-office interface mentioned without IAP",
                recommendation="Protect admin UIs with IAP and context-aware access (SEC-02).",
            )
        )
    penalty = sum(_SEVERITY_PENALTY[f.severity] for f in findings)
    score = max(0, min(100, 85 - penalty + 3 * len(controls)))
    return to_tool_result(SecurityScanOutput(findings=findings, controls_detected=controls, security_score=score))


# --------------------------------------------------------------------------- #
# Reliability
# --------------------------------------------------------------------------- #
@den_tool
def calculate_composite_availability_slo(
    serial_components: list[str], multi_region: bool = False, target_slo_percent: float = 99.9
) -> dict[str, Any]:
    """Compute the composite availability of components that sit in series on the request path.

    Composite availability = product of each component's published SLA. With
    ``multi_region=True`` each component is modelled as two independent regional
    replicas behind global load balancing: 1 - (1 - a)^2.

    Args:
        serial_components: Ordered list of components a single user request traverses,
            e.g. ["cloud load balancing", "cloud run", "cloud sql"]. Use Google Cloud
            product names; zonal/HA variants like "cloud sql zonal" are supported.
        multi_region: True if every tier is deployed active-active in two regions.
        target_slo_percent: The availability SLO the customer needs (90.0-99.999).

    Returns:
        dict: ``{"status": "success", "per_component_sla", "composite_availability_percent",
        "target_slo_percent", "meets_target", "allowed_downtime_minutes_per_month",
        "unknown_components", "recommendation"}``. Unknown components are assumed 99.0%
        and listed so you can ask the consultant to clarify.
    """
    args = AvailabilityInput(
        serial_components=serial_components, multi_region=multi_region, target_slo_percent=target_slo_percent
    )
    catalog: dict[str, float] = reference_catalog()["sla_catalog"]
    per: dict[str, float] = {}
    unknown: list[str] = []
    for comp in args.serial_components:
        key = match_catalog_key(comp, catalog)
        if key is None:
            unknown.append(comp)
            per[comp] = 99.0
        else:
            per[comp] = catalog[key]
    if len(unknown) == len(args.serial_components):
        return guided_error(
            "NO_KNOWN_COMPONENTS",
            "None of the components matched the SLA catalog.",
            "Re-call with Google Cloud product names (see valid_values) for the request path.",
            valid_values=sorted(catalog),
        )
    composite = 1.0
    for sla in per.values():
        a = sla / 100.0
        if args.multi_region:
            a = 1 - (1 - a) ** 2
        composite *= a
    composite_pct = round(composite * 100, 4)
    meets = composite_pct >= args.target_slo_percent
    weakest = min(per, key=per.get)  # type: ignore[arg-type]
    if meets:
        rec = f"Composite {composite_pct}% meets the {args.target_slo_percent}% SLO; define burn-rate alerts (REL-01)."
    else:
        rec = (
            f"Composite {composite_pct}% misses the {args.target_slo_percent}% SLO. Weakest link: '{weakest}' "
            f"({per[weakest]}%). Upgrade it to an HA/regional variant, remove serial hops, or go multi-region (REL-02)."
        )
    downtime = round((1 - composite) * 30 * 24 * 60, 1)
    return to_tool_result(
        AvailabilityOutput(
            per_component_sla=per,
            composite_availability_percent=composite_pct,
            target_slo_percent=args.target_slo_percent,
            meets_target=meets,
            allowed_downtime_minutes_per_month=downtime,
            unknown_components=unknown,
            recommendation=rec,
        )
    )


# --------------------------------------------------------------------------- #
# FinOps
# --------------------------------------------------------------------------- #
_CUD_RATE = 0.28  # blended 1-year committed use discount


@den_tool
def estimate_monthly_gcp_cost(line_items: list[CostLineItemInput], monthly_budget_usd: float | None = None) -> dict[str, Any]:
    """Estimate the monthly Google Cloud bill for a design and find savings.

    Args:
        line_items: Billable usage per service. Each item has ``service`` (Google Cloud
            product name, e.g. "cloud run", "cloud sql", "bigquery", "gemini api") and
            ``quantity`` in the service's billing unit (vCPU-month for compute, node-month
            for Spanner, TiB scanned for BigQuery, GiB-month for storage, million output
            tokens for Gemini/Vertex AI).
        monthly_budget_usd: Optional customer budget to compare against.

    Returns:
        dict: ``{"status": "success", "line_items": [{service, quantity, unit,
        unit_price_usd, monthly_cost_usd}], "total_monthly_usd",
        "committed_use_discount_savings_usd", "within_budget", "optimisation_tips"}``,
        or a guided error listing valid services when a service is not priced.
    """
    args = CostEstimateInput(
        line_items=[li if isinstance(li, CostLineItemInput) else CostLineItemInput(**li) for li in line_items],
        monthly_budget_usd=monthly_budget_usd,
    )
    cat = reference_catalog()
    pricing: dict[str, dict[str, Any]] = cat["pricing_catalog"]
    out: list[CostLineItemOutput] = []
    unknown: list[str] = []
    cud_base = 0.0
    for li in args.line_items:
        key = match_catalog_key(li.service, pricing)
        if key is None:
            unknown.append(li.service)
            continue
        cost = round(li.quantity * pricing[key]["unit_price_usd"], 2)
        out.append(
            CostLineItemOutput(
                service=key,
                quantity=li.quantity,
                unit=pricing[key]["unit"],
                unit_price_usd=pricing[key]["unit_price_usd"],
                monthly_cost_usd=cost,
            )
        )
        if key in cat["cud_eligible"]:
            cud_base += cost
    if unknown and not out:
        return guided_error(
            "UNPRICED_SERVICES",
            f"No pricing data for: {', '.join(unknown)}.",
            "Re-call estimate_monthly_gcp_cost using services from valid_values (map close equivalents, "
            "e.g. 'postgres' -> 'cloud sql').",
            valid_values=sorted(pricing),
        )
    total = round(sum(i.monthly_cost_usd for i in out), 2)
    tips: list[str] = []
    if cud_base > 500:
        tips.append(f"Commit ~70% of steady compute/DB baseline: est. savings ${cud_base * _CUD_RATE:,.0f}/month (COST-02).")
    if any(i.service == "compute engine" for i in out):
        tips.append("Consider Cloud Run / GKE Autopilot to scale to zero outside peak (COST-01).")
    if any(i.service == "bigquery" and i.quantity > 50 for i in out):
        tips.append("Partition + cluster BigQuery tables or move to editions/slots to cap scan cost (DATA-03).")
    if any(i.service in {"gemini api", "vertex ai"} for i in out):
        tips.append("Route simple prompts to Flash-class models and enable context caching (COST-01).")
    if unknown:
        tips.append(f"Unpriced services excluded from total: {', '.join(unknown)}.")
    tips.append("Label every resource and export billing to BigQuery with budgets/alerts (COST-03).")
    within = None if args.monthly_budget_usd is None else total <= args.monthly_budget_usd
    return to_tool_result(
        CostEstimateOutput(
            line_items=out,
            total_monthly_usd=total,
            committed_use_discount_savings_usd=round(cud_base * _CUD_RATE, 2),
            within_budget=within,
            optimisation_tips=tips,
        )
    )


# --------------------------------------------------------------------------- #
# Data & AI governance
# --------------------------------------------------------------------------- #
_MULTI_REGION_ALIASES = {"eu": "europe-multi", "europe": "europe-multi", "us": "us-multi", "asia": "asia-multi"}


@den_tool
def check_data_residency_compliance(
    data_classification: str,
    storage_regions: list[str],
    regulations: list[str] | None = None,
    uses_generative_ai: bool = False,
) -> dict[str, Any]:
    """Check data residency and governance controls against regulations and data classification.

    Args:
        data_classification: One of "public", "internal", "confidential", "restricted".
        storage_regions: Google Cloud regions/multi-regions where data is stored or
            processed, e.g. ["europe-west1"], ["us-central1", "us-east4"], ["eu"].
        regulations: Applicable regimes, e.g. ["gdpr"], ["hipaa"], ["pci-dss"], ["psd2"],
            ["sovereign-ksa"]. Unknown regimes are flagged for legal review.
        uses_generative_ai: True if the design sends this data to Gemini / LLMs.

    Returns:
        dict: ``{"status": "success", "compliant", "violations", "required_controls",
        "approved_regions"}`` or a guided error with valid classification values.
    """
    valid_cls = [c.value for c in DataClassification]
    if data_classification not in valid_cls:
        return guided_error(
            "UNKNOWN_CLASSIFICATION",
            f"'{data_classification}' is not a recognised data classification.",
            "Ask or infer the classification and call again with one of valid_values.",
            valid_values=valid_cls,
        )
    args = ResidencyInput(
        data_classification=DataClassification(data_classification),
        storage_regions=storage_regions,
        regulations=[r.lower().strip() for r in (regulations or [])],
        uses_generative_ai=uses_generative_ai,
    )
    rules: dict[str, Any] = reference_catalog()["residency_rules"]
    regions = [_MULTI_REGION_ALIASES.get(r.lower().strip(), r.lower().strip()) for r in args.storage_regions]
    violations: list[str] = []
    controls: list[str] = []
    approved: set[str] = set(regions)
    for reg in args.regulations:
        rule = rules.get(reg)
        if rule is None:
            controls.append(f"No rule pack for '{reg}': obtain legal/compliance sign-off.")
            continue
        controls.extend(rule["controls"])
        for region in regions:
            if not any(region.startswith(p) for p in rule["allowed_region_prefixes"]):
                violations.append(f"{reg.upper()}: region '{region}' is outside the permitted boundary.")
                approved.discard(region)
    if args.data_classification in {DataClassification.CONFIDENTIAL, DataClassification.RESTRICTED}:
        controls += [
            "CMEK with Cloud KMS on all data stores",
            "VPC Service Controls perimeter",
            "Dataplex policy tags / column-level security",
        ]
    if args.data_classification is DataClassification.RESTRICTED and len(regions) > 2:
        violations.append("RESTRICTED data replicated to more than two regions without documented justification.")
    if args.uses_generative_ai:
        controls += reference_catalog()["genai_controls"]
        if args.data_classification is DataClassification.RESTRICTED:
            controls.append("De-identify restricted fields (Cloud DLP) before sending prompts to the model.")
    # De-duplicate while preserving order.
    controls = list(dict.fromkeys(controls))
    return to_tool_result(
        ResidencyOutput(
            compliant=not violations,
            violations=violations,
            required_controls=controls,
            approved_regions=sorted(approved),
        )
    )
