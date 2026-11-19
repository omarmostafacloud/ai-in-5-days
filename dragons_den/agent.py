"""The Dragons' Den: multi-agent architecture review board built on Google ADK.

Orchestration (proven ADK patterns, not a monolith)::

    den_host (Coordinator LlmAgent, Flash)  --transfer-->  review_historian (memory Q&A)
        |
        +--transfer--> design_review_pipeline (SequentialAgent)
                          1. pitch_intake_analyst      (LlmAgent, Flash, output_schema=DesignPitch)
                          2. dragon_panel              (ParallelAgent - fan-out)
                               security_dragon | reliability_dragon | finops_dragon | data_ai_dragon
                               (Flash, auto-escalated to Pro for complex/regulated pitches)
                          3. verdict_refinement_loop   (LoopAgent - generator/critic self-eval)
                               den_chair (Pro) -> verdict_critic (Flash) -> verdict_quality_gate
                          4. review_clerk              (LlmAgent, Flash, HITL-gated filing tool)

The ``App`` wires global plugins (guardrails, PII, intent/outcome logging), events
compaction, context caching and resumability (required for HITL pause/resume).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent, LoopAgent, ParallelAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App
from google.adk.apps.app import ResumabilityConfig
from google.adk.events import Event, EventActions
from google.adk.models import Gemini
from google.adk.tools import load_memory
from google.genai import types

from dragons_den import prompts
from dragons_den.config import HTTP_RETRY, SETTINGS, TaskTier, generation_config, model_for_agent
from dragons_den.guardrails import DenGuardrailPlugin
from dragons_den.memory.async_memory import schedule_memory_consolidation
from dragons_den.memory.compaction import (
    build_context_cache_config,
    build_events_compaction_config,
    trim_text_for_state,
)
from dragons_den.observability.logging import log_event
from dragons_den.observability.pii import redactor
from dragons_den.schemas import ChairVerdict, CriticReport, DesignPitch, DragonVerdict
from dragons_den.secrets import bootstrap_model_credentials
from dragons_den.tools import (
    calculate_composite_availability_slo,
    check_data_residency_compliance,
    create_remediation_backlog_item,
    estimate_monthly_gcp_cost,
    file_decision_with_human_approval,
    lookup_well_architected_principle,
    scan_design_for_security_antipatterns,
    search_past_review_decisions,
)

bootstrap_model_credentials()  # Secret Manager -> env, never hard-coded

CONSTITUTION = types.Content(role="system", parts=[types.Part(text=prompts.DEN_CONSTITUTION)])


def routed_model(agent_name: str) -> Gemini:
    """Strategic model routing: resolve the Gemini tier configured for ``agent_name``."""
    return Gemini(model=model_for_agent(agent_name), retry_options=HTTP_RETRY)


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
async def capture_pitch_text(callback_context: Any) -> None:
    """Persist the (redacted, trimmed) raw pitch into state for the dragons."""
    content = callback_context.user_content
    text = "\n".join(p.text for p in (content.parts if content and content.parts else []) if p.text)
    if text:
        callback_context.state["pitch_text"] = trim_text_for_state(redactor.redact(text))
    return None


class VerdictQualityGate(BaseAgent):
    """Deterministic loop terminator: escalates (exits the loop) once the critic approves."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        report = ctx.session.state.get("critic_report") or {}
        approved = isinstance(report, dict) and bool(report.get("approved"))
        log_event(
            "SELF_EVAL_GATE",
            f"critic approved={approved}",
            phase="OUTCOME",
            approved=approved,
            issues=report.get("issues") if isinstance(report, dict) else None,
        )
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            branch=ctx.branch,
            actions=EventActions(escalate=approved),
        )


# --------------------------------------------------------------------------- #
# Agent factories (fresh instances - an agent can only have one parent)
# --------------------------------------------------------------------------- #
def _llm_agent(name: str, description: str, instruction: str, tier: TaskTier = TaskTier.FAST, **kwargs: Any) -> LlmAgent:
    return LlmAgent(
        name=name,
        description=description,
        model=routed_model(name),
        static_instruction=CONSTITUTION,
        instruction=instruction,
        generate_content_config=generation_config(tier),
        **kwargs,
    )


def build_dragon_panel() -> ParallelAgent:
    """Fan-out: four specialist dragons review the pitch concurrently."""
    shared = [lookup_well_architected_principle, search_past_review_decisions]
    common = {
        "output_schema": DragonVerdict,
        "include_contents": "none",
        "disallow_transfer_to_parent": True,
        "disallow_transfer_to_peers": True,
    }
    return ParallelAgent(
        name="dragon_panel",
        description="Four specialist dragons grill the pitch in parallel.",
        sub_agents=[
            _llm_agent(
                "security_dragon",
                "Security & IAM specialist.",
                prompts.SECURITY_DRAGON_INSTRUCTION,
                tools=[scan_design_for_security_antipatterns, *shared],
                output_key="security_verdict",
                **common,
            ),
            _llm_agent(
                "reliability_dragon",
                "SRE / availability specialist.",
                prompts.RELIABILITY_DRAGON_INSTRUCTION,
                tools=[calculate_composite_availability_slo, *shared],
                output_key="reliability_verdict",
                **common,
            ),
            _llm_agent(
                "finops_dragon",
                "Cost optimisation specialist.",
                prompts.FINOPS_DRAGON_INSTRUCTION,
                tools=[estimate_monthly_gcp_cost, *shared],
                output_key="finops_verdict",
                **common,
            ),
            _llm_agent(
                "data_ai_dragon",
                "Data governance & responsible AI specialist.",
                prompts.DATA_AI_DRAGON_INSTRUCTION,
                tools=[check_data_residency_compliance, *shared],
                output_key="data_ai_verdict",
                **common,
            ),
        ],
    )


def build_verdict_loop() -> LoopAgent:
    """Generator/critic self-evaluation loop that refines the chair's verdict."""
    return LoopAgent(
        name="verdict_refinement_loop",
        description="Chair drafts the verdict, critic audits it, repeat until approved.",
        max_iterations=SETTINGS.max_review_loops,
        sub_agents=[
            _llm_agent(
                "den_chair",
                "Synthesises the panel into a scored verdict.",
                prompts.CHAIR_INSTRUCTION,
                tier=TaskTier.DEEP,
                output_schema=ChairVerdict,
                output_key="chair_verdict",
                include_contents="none",
                disallow_transfer_to_parent=True,
                disallow_transfer_to_peers=True,
            ),
            _llm_agent(
                "verdict_critic",
                "Self-evaluation guardrail for the verdict.",
                prompts.CRITIC_INSTRUCTION,
                output_schema=CriticReport,
                output_key="critic_report",
                include_contents="none",
                disallow_transfer_to_parent=True,
                disallow_transfer_to_peers=True,
            ),
            VerdictQualityGate(name="verdict_quality_gate", description="Exits the loop once approved."),
        ],
    )


def build_review_pipeline() -> SequentialAgent:
    """Sequential pipeline: intake -> parallel panel -> self-eval loop -> HITL clerk."""
    return SequentialAgent(
        name="design_review_pipeline",
        description="Full Dragons' Den review of a new or revised architecture pitch.",
        sub_agents=[
            _llm_agent(
                "pitch_intake_analyst",
                "Extracts a structured DesignPitch.",
                prompts.INTAKE_INSTRUCTION,
                output_schema=DesignPitch,
                output_key="design_pitch",
                before_agent_callback=capture_pitch_text,
                disallow_transfer_to_parent=True,
                disallow_transfer_to_peers=True,
            ),
            build_dragon_panel(),
            build_verdict_loop(),
            _llm_agent(
                "review_clerk",
                "Presents the verdict and files it with human approval.",
                prompts.CLERK_INSTRUCTION,
                tools=[create_remediation_backlog_item, file_decision_with_human_approval],
            ),
        ],
    )


def build_root_agent() -> LlmAgent:
    """Coordinator that routes between a new review and historical Q&A."""
    historian = _llm_agent(
        "review_historian",
        "Answers questions about past review decisions.",
        prompts.HISTORIAN_INSTRUCTION,
        tools=[search_past_review_decisions, load_memory],
    )
    return _llm_agent(
        "den_host",
        "Host of the Dragons' Den architecture review board.",
        prompts.HOST_INSTRUCTION,
        sub_agents=[build_review_pipeline(), historian],
        after_agent_callback=schedule_memory_consolidation,
    )


root_agent = build_root_agent()

app = App(
    name=SETTINGS.app_name,
    root_agent=root_agent,
    plugins=[DenGuardrailPlugin()],
    events_compaction_config=build_events_compaction_config(),
    context_cache_config=build_context_cache_config(),
    resumability_config=ResumabilityConfig(is_resumable=True),
)
