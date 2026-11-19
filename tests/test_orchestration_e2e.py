"""End-to-end orchestration test of the full multi-agent graph with a scripted LLM.

Verifies: coordinator transfer, sequential pipeline, parallel dragon fan-out with
output_key state, generator/critic LoopAgent refinement, HITL pause on the
high-stakes filing tool, resume after human approval, and persistence of the
filed decision - all against a persistent SQLite DatabaseSessionService.
"""

from __future__ import annotations

import pytest
from fakes import CALLS, install_scripted_models
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from dragons_den.agent import build_root_agent
from dragons_den.memory.decision_store import get_decision_store

PITCH_TEXT = (
    "Please review: Orbit Retail holiday storefront. Cloud Load Balancing -> Cloud Run -> Cloud SQL (zonal). "
    "Confidential card data in europe-west1, GDPR + PCI-DSS, target 99.9%, budget $5k/month. "
    "Contact: jane.doe@orbit-retail.example"
)


def _confirmation_calls(events):
    for ev in events:
        for fc in ev.get_function_calls():
            if fc.name == "adk_request_confirmation":
                yield fc


async def _run(runner, session_id, message):
    events = []
    async for ev in runner.run_async(user_id="pso-consultant", session_id=session_id, new_message=message):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_full_review_with_hitl_approval(tmp_path):
    from google.adk.apps import App
    from google.adk.apps.app import ResumabilityConfig

    from dragons_den.guardrails import DenGuardrailPlugin

    CALLS.clear()
    root = build_root_agent()
    install_scripted_models(root)
    app = App(
        name="dragons_den",
        root_agent=root,
        plugins=[DenGuardrailPlugin()],
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    sessions = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")
    runner = Runner(app=app, session_service=sessions)
    session = await sessions.create_session(app_name="dragons_den", user_id="pso-consultant")

    # Turn 1: pitch -> full pipeline runs until HITL pause.
    events = await _run(runner, session.id, types.Content(role="user", parts=[types.Part(text=PITCH_TEXT)]))
    confirmations = list(_confirmation_calls(events))
    assert confirmations, "filing tool must pause for human confirmation"

    state = (await sessions.get_session(app_name="dragons_den", user_id="pso-consultant", session_id=session.id)).state
    assert state["design_pitch"]["customer_alias"] == "Orbit Retail"
    for key in ("security_verdict", "reliability_verdict", "finops_verdict", "data_ai_verdict"):
        assert key in state, f"parallel dragon output missing: {key}"
    assert state["critic_report"]["approved"] is True
    assert CALLS["den_chair"] == 2, "critic rejection must trigger a second chair draft"
    assert len(state["remediation_backlog"]) == 1
    assert "filed_decision_id" not in state, "nothing may be filed before human approval"
    # PII from the pitch never reaches persisted state.
    assert "jane.doe@orbit-retail.example" not in str(state)

    # Turn 2: human approves -> decision filed and persisted.
    approval = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=confirmations[0].id, name="adk_request_confirmation", response={"confirmed": True}
                )
            )
        ],
    )
    await _run(runner, session.id, approval)
    state = (await sessions.get_session(app_name="dragons_den", user_id="pso-consultant", session_id=session.id)).state
    decision_id = state.get("filed_decision_id")
    assert decision_id and decision_id.startswith("DEN-")
    assert any(m["decision_id"] == decision_id for m in get_decision_store().search("holiday storefront cloud run", 10))


@pytest.mark.asyncio
async def test_hitl_rejection_files_nothing(tmp_path):
    from google.adk.apps import App
    from google.adk.apps.app import ResumabilityConfig
    from google.adk.sessions import InMemorySessionService

    CALLS.clear()
    root = build_root_agent()
    install_scripted_models(root)
    app = App(name="dragons_den", root_agent=root, resumability_config=ResumabilityConfig(is_resumable=True))
    sessions = InMemorySessionService()
    runner = Runner(app=app, session_service=sessions)
    session = await sessions.create_session(app_name="dragons_den", user_id="u")
    events = []
    async for ev in runner.run_async(
        user_id="u", session_id=session.id, new_message=types.Content(role="user", parts=[types.Part(text=PITCH_TEXT)])
    ):
        events.append(ev)
    fc = next(_confirmation_calls(events))
    async for _ in runner.run_async(
        user_id="u",
        session_id=session.id,
        new_message=types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=fc.id, name="adk_request_confirmation", response={"confirmed": False}
                    )
                )
            ],
        ),
    ):
        pass
    state = (await sessions.get_session(app_name="dragons_den", user_id="u", session_id=session.id)).state
    assert "filed_decision_id" not in state
