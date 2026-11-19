"""A scripted, deterministic stand-in for Gemini so the whole multi-agent graph
(transfer -> sequential -> parallel -> loop -> HITL pause/resume) can be exercised
in CI without network access or API keys.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

PITCH = {
    "customer_alias": "Orbit Retail",
    "design_title": "Holiday storefront on Cloud Run",
    "business_goal": "Survive Black Friday traffic",
    "components": ["cloud load balancing", "cloud run", "cloud sql zonal", "cloud storage"],
    "request_path": ["cloud load balancing", "cloud run", "cloud sql zonal"],
    "data_classification": "confidential",
    "storage_regions": ["europe-west1"],
    "regulations": ["gdpr", "pci-dss"],
    "internet_facing": True,
    "uses_generative_ai": False,
    "target_slo_percent": 99.9,
    "monthly_budget_usd": 5000,
    "estimated_usage": [{"service": "cloud run", "quantity": 16}, {"service": "cloud sql", "quantity": 8}],
    "complexity": "medium",
}

DRAGON_TOOL_CALLS: dict[str, tuple[str, dict[str, Any]]] = {
    "security_dragon": (
        "scan_design_for_security_antipatterns",
        {"design_description": "Cloud Run behind load balancer, service account keys stored in CI.", "internet_facing": True},
    ),
    "reliability_dragon": (
        "calculate_composite_availability_slo",
        {"serial_components": PITCH["request_path"], "target_slo_percent": 99.9},
    ),
    "finops_dragon": ("estimate_monthly_gcp_cost", {"line_items": PITCH["estimated_usage"], "monthly_budget_usd": 5000}),
    "data_ai_dragon": (
        "check_data_residency_compliance",
        {"data_classification": "confidential", "storage_regions": ["europe-west1"], "regulations": ["gdpr"]},
    ),
}


def _verdict(name: str, score: int, stance: str) -> dict[str, Any]:
    return {
        "dragon": name,
        "pillar_score": score,
        "stance": stance,
        "one_liner": f"{name} has spoken.",
        "findings": [{"severity": "high", "title": f"{name} finding", "evidence": "tool output", "recommendation": "fix it"}],
        "tool_evidence": [DRAGON_TOOL_CALLS[name][0]],
    }


CHAIR = {
    "customer_alias": "Orbit Retail",
    "design_title": "Holiday storefront on Cloud Run",
    "decision": "im_in_with_conditions",
    "den_score": 68,
    "executive_summary": "Solid serverless core; zonal Cloud SQL and SA keys must be fixed before launch.",
    "top_risks": [
        {"severity": "high", "title": "reliability_dragon finding", "evidence": "tool output", "recommendation": "fix it"}
    ],
    "remediation_plan": ["30d: Cloud SQL HA", "60d: Workload Identity", "90d: DR test"],
}


def _call(name: str, args: dict[str, Any]) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part.from_function_call(name=name, args=args)]))


def _text(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def _last_function_response(req: LlmRequest) -> types.FunctionResponse | None:
    for content in reversed(req.contents or []):
        texts = [p.text for p in content.parts or [] if p.text]
        if texts and texts[0].startswith("The text between <<<BEGIN_SYSTEM_INSTRUCTION"):
            continue  # dynamic instruction appended after history when static_instruction is set
        for part in content.parts or []:
            if part.function_response:
                return part.function_response
        if content.role == "user" and any(p.text for p in content.parts or []):
            return None
    return None


CALLS: dict[str, int] = defaultdict(int)  # per-agent model call counter
MODEL_REQUESTS: list[tuple[str, str | None]] = []  # (agent, routed model id)


class ScriptedLlm(BaseLlm):
    """Fake LLM whose behaviour is keyed by the agent it is attached to."""

    agent: str = ""

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["scripted-.*"]

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        CALLS[self.agent] += 1
        MODEL_REQUESTS.append((self.agent, llm_request.model))
        yield self._respond(llm_request)

    def _respond(self, req: LlmRequest) -> LlmResponse:
        fr = _last_function_response(req)
        a = self.agent
        if a == "den_host":
            return _call("transfer_to_agent", {"agent_name": "design_review_pipeline"})
        if a == "pitch_intake_analyst":
            return _text(json.dumps(PITCH))
        if a in DRAGON_TOOL_CALLS:
            if fr is None:
                tool, args = DRAGON_TOOL_CALLS[a]
                return _call(tool, args)
            score = {"security_dragon": 55, "reliability_dragon": 60, "finops_dragon": 80, "data_ai_dragon": 75}[a]
            return _text(json.dumps(_verdict(a, score, "in_with_conditions")))
        if a == "den_chair":
            return _text(json.dumps(CHAIR))
        if a == "verdict_critic":
            approved = CALLS[a] >= 2  # reject first draft -> exercise the loop
            return _text(
                json.dumps(
                    {
                        "grounded": True,
                        "consistent_with_scores": approved,
                        "safe_and_professional": True,
                        "issues": [] if approved else ["Den Score band mismatch"],
                        "approved": approved,
                    }
                )
            )
        if a == "review_clerk":
            if fr is None:
                return _call(
                    "create_remediation_backlog_item",
                    {
                        "title": "Upgrade Cloud SQL to HA",
                        "pillar": "reliability",
                        "severity": "high",
                        "owner_role": "sre",
                        "acceptance_criteria": "Regional HA instance with failover tested",
                    },
                )
            if fr.name == "create_remediation_backlog_item":
                return _call(
                    "file_architecture_review_decision",
                    {
                        "customer_alias": CHAIR["customer_alias"],
                        "design_title": CHAIR["design_title"],
                        "decision": CHAIR["decision"],
                        "den_score": CHAIR["den_score"],
                        "executive_summary": CHAIR["executive_summary"],
                        "backlog_item_ids": [fr.response.get("item_id", "DEN-BL-X")],
                    },
                )
            return _text(f"Filing result: {json.dumps(fr.response)}")
        return _text("ok")


def install_scripted_models(agent: Any) -> None:
    """Recursively replace every LlmAgent's model with a ScriptedLlm."""
    if hasattr(agent, "model") and not isinstance(agent.model, str | type(None)):
        agent.model = ScriptedLlm(model="scripted-gemini", agent=agent.name)
    for sub in getattr(agent, "sub_agents", []):
        install_scripted_models(sub)
