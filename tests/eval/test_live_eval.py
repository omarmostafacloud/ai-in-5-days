"""Live LLM evaluation with ADK ``AgentEvaluator`` against the golden evalset.

Skipped unless ``RUN_LIVE_EVAL=1`` and real Gemini credentials are available
(``GOOGLE_API_KEY`` from Secret Manager, or Vertex AI ADC). Runs nightly in CI.

Note: rubric criteria score the final user-facing response; the filing step pauses for
HITL confirmation which the evaluator does not approve, so nothing is ever filed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE_EVAL") != "1", reason="live eval disabled (set RUN_LIVE_EVAL=1)")


@pytest.mark.asyncio
async def test_golden_evalset_live():
    from google.adk.evaluation.agent_evaluator import AgentEvaluator

    from eval.build_evalset import build

    evalset = ROOT / "eval" / "dragons_den.evalset.json"
    evalset.write_text(build().model_dump_json(indent=2, exclude_none=True, by_alias=True))
    await AgentEvaluator.evaluate(
        agent_module="dragons_den",
        eval_dataset_file_path_or_dir=str(evalset),
        num_runs=1,
        print_detailed_results=True,
    )
