"""Generate the ADK evalset (live LLM evaluation) from the golden dataset.

The evalset is consumed by ``adk eval`` and ``tests/eval/test_live_eval.py``
(``AgentEvaluator``) with the criteria in ``eval/test_config.json``:
tool trajectory (ANY_ORDER - parallel dragons), rubric-based final response quality
(LLM-as-judge) and safety.

Usage::

    python -m eval.build_evalset   # writes eval/dragons_den.evalset.json
"""

from __future__ import annotations

import json
from pathlib import Path

from google.adk.evaluation.eval_case import EvalCase, IntermediateData, Invocation
from google.adk.evaluation.eval_rubrics import Rubric, RubricContent
from google.adk.evaluation.eval_set import EvalSet
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
MANDATORY_TOOLS = {
    "scan_design_for_security_antipatterns",
    "calculate_composite_availability_slo",
    "estimate_monthly_gcp_cost",
    "check_data_residency_compliance",
}


def build() -> EvalSet:
    data = json.loads((ROOT / "eval" / "golden_dataset.json").read_text())
    cases = []
    for case in data["cases"]:
        tool_uses = [types.FunctionCall(name=t, args={}) for t in sorted(MANDATORY_TOOLS)]
        invocation = Invocation(
            invocation_id=f"{case['id']}-1",
            user_content=types.Content(role="user", parts=[types.Part(text=case["pitch_text"])]),
            final_response=types.Content(
                role="model",
                parts=[types.Part(text=f"Expected decision band: {case['expected_decision']}. " + " ".join(case["rubrics"]))],
            ),
            intermediate_data=IntermediateData(tool_uses=tool_uses, tool_responses=[], intermediate_responses=[]),
        )
        rubrics = [
            Rubric(rubric_id=f"{case['id']}-r{i}", rubric_content=RubricContent(text_property=r))
            for i, r in enumerate(case["rubrics"], start=1)
        ]
        cases.append(EvalCase(eval_id=case["id"], conversation=[invocation], rubrics=rubrics))
    return EvalSet(
        eval_set_id="dragons_den_golden_v1", name="Dragons' Den golden pitches", description=data["description"], eval_cases=cases
    )


if __name__ == "__main__":
    out = ROOT / "eval" / "dragons_den.evalset.json"
    out.write_text(build().model_dump_json(indent=2, exclude_none=True, by_alias=True))
    print(f"wrote {out}")
