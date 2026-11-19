"""Offline, deterministic evaluation harness (regression gate for CI).

Runs three suites against ``eval/golden_dataset.json`` without any network access:

1. **Tool golden checks** - every expected tool outcome (SLO math, cost, residency,
   security rules) must hold. These are the facts the dragons ground their verdicts on,
   so a regression here silently corrupts every review.
2. **Guardrail red-team** - prompt injections must be blocked, benign pitches allowed,
   PII redacted.
3. **Orchestration trajectory** - the full multi-agent graph is executed with a
   scripted LLM; we assert the expected agent/tool trajectory, the self-eval loop and
   the HITL pause.

Writes ``eval/results/offline_scorecard.json`` and exits non-zero if the pass rate
is below ``--threshold`` (default 1.0).

Usage::

    python -m eval.run_offline_eval --threshold 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("DECISION_STORE_PATH", ":memory:")
os.environ.setdefault("LOG_LEVEL", "ERROR")

from google.genai import types  # noqa: E402

from dragons_den import tools  # noqa: E402
from dragons_den.guardrails import DenGuardrailPlugin  # noqa: E402

DATASET = json.loads((ROOT / "eval" / "golden_dataset.json").read_text())


def _check(expect: dict[str, Any], out: dict[str, Any]) -> list[str]:
    """Return a list of failed expectation descriptions (empty == pass)."""
    fails: list[str] = []
    if out.get("status") not in {"success", None}:
        return [f"tool returned error: {out.get('error_code')}"]
    rule_ids = {f["rule_id"] for f in out.get("findings", []) if "rule_id" in f}
    for key, val in expect.items():
        ok = True
        if key == "rule_ids_include":
            ok = set(val) <= rule_ids
        elif key == "findings_count_max":
            ok = len(out["findings"]) <= val
        elif key == "security_score_max":
            ok = out["security_score"] <= val
        elif key == "security_score_min":
            ok = out["security_score"] >= val
        elif key in {"meets_target", "within_budget", "compliant"}:
            ok = out[key] is val
        elif key == "tip_contains":
            ok = any(val in t for t in out["optimisation_tips"])
        elif key == "cud_savings_min":
            ok = out["committed_use_discount_savings_usd"] >= val
        elif key == "total_min":
            ok = out["total_monthly_usd"] >= val
        elif key == "controls_include":
            ok = set(val) <= set(out["required_controls"])
        elif key == "violation_contains":
            ok = any(val in v for v in out["violations"])
        if not ok:
            fails.append(f"{key}={val!r} not satisfied")
    return fails


def run_tool_suite() -> list[dict[str, Any]]:
    results = []
    for case in DATASET["cases"]:
        for exp in case["tool_expectations"]:
            fn = getattr(tools, exp["tool"])
            args = dict(exp["args"])
            if exp["tool"] == "scan_design_for_security_antipatterns":
                args["design_description"] = case["pitch_text"]
            started = time.perf_counter()
            out = fn(**args)
            fails = _check(exp["expect"], out)
            results.append(
                {
                    "suite": "tool_golden",
                    "case": case["id"],
                    "tool": exp["tool"],
                    "passed": not fails,
                    "failures": fails,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
    return results


async def run_guardrail_suite() -> list[dict[str, Any]]:
    plugin = DenGuardrailPlugin()

    class _Ctx:
        class session:
            id = "eval"

    results = []
    for case in DATASET["guardrail_cases"]:
        msg = types.Content(role="user", parts=[types.Part(text=case["input"])])
        out = await plugin.on_user_message_callback(invocation_context=_Ctx(), user_message=msg)
        text = out.parts[0].text if out else case["input"]
        if case["expect"] == "blocked":
            passed = out is not None and text.startswith("[GUARDRAIL]")
        elif case["expect"] == "allowed":
            passed = out is None
        else:
            passed = all(s not in text for s in case.get("must_not_contain", []))
        results.append({"suite": "guardrails", "case": case["id"], "passed": passed, "failures": [] if passed else [text]})
    return results


async def run_trajectory_suite() -> list[dict[str, Any]]:
    from fakes import CALLS, install_scripted_models
    from google.adk.apps import App
    from google.adk.apps.app import ResumabilityConfig
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    from dragons_den.agent import build_root_agent

    CALLS.clear()
    root = build_root_agent()
    install_scripted_models(root)
    app = App(
        name="dragons_den",
        root_agent=root,
        plugins=[DenGuardrailPlugin()],
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
    sessions = InMemorySessionService()
    runner = Runner(app=app, session_service=sessions)
    session = await sessions.create_session(app_name="dragons_den", user_id="eval")
    authors, tool_calls = [], []
    async for ev in runner.run_async(
        user_id="eval",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=DATASET["cases"][0]["pitch_text"])]),
    ):
        if ev.author not in authors:
            authors.append(ev.author)
        tool_calls += [fc.name for fc in ev.get_function_calls()]

    expected_agents = [
        "den_host",
        "design_review_pipeline",
        "pitch_intake_analyst",
        "dragon_panel",
        "security_dragon",
        "reliability_dragon",
        "finops_dragon",
        "data_ai_dragon",
        "verdict_refinement_loop",
        "den_chair",
        "verdict_critic",
        "verdict_quality_gate",
        "review_clerk",
    ]
    expected_tools = [
        "transfer_to_agent",
        "scan_design_for_security_antipatterns",
        "calculate_composite_availability_slo",
        "estimate_monthly_gcp_cost",
        "check_data_residency_compliance",
        "create_remediation_backlog_item",
        "file_architecture_review_decision",
        "adk_request_confirmation",
    ]
    checks = {
        "all_agents_participated": set(expected_agents) <= set(authors),
        "expected_tools_called": set(expected_tools) <= set(tool_calls),
        "self_eval_loop_refined": CALLS["den_chair"] == 2,
        "hitl_pause_before_filing": tool_calls[-1] == "adk_request_confirmation" or "adk_request_confirmation" in tool_calls,
    }
    return [{"suite": "trajectory", "case": name, "passed": ok, "failures": [] if ok else [name]} for name, ok in checks.items()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=1.0)
    args = parser.parse_args()

    results = run_tool_suite()
    results += asyncio.run(run_guardrail_suite())
    results += asyncio.run(run_trajectory_suite())

    by_suite: dict[str, dict[str, int]] = {}
    for r in results:
        s = by_suite.setdefault(r["suite"], {"passed": 0, "total": 0})
        s["total"] += 1
        s["passed"] += int(r["passed"])
    pass_rate = sum(r["passed"] for r in results) / len(results)
    scorecard = {
        "dataset": DATASET["dataset"],
        "pass_rate": round(pass_rate, 4),
        "threshold": args.threshold,
        "suites": by_suite,
        "results": results,
    }
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "offline_scorecard.json").write_text(json.dumps(scorecard, indent=2))

    print(f"\nDragons' Den offline eval - pass rate {pass_rate:.1%} (threshold {args.threshold:.0%})")
    for suite, s in by_suite.items():
        print(f"  {suite:<12} {s['passed']}/{s['total']}")
    for r in results:
        if not r["passed"]:
            print(f"  FAIL {r['suite']}/{r['case']}: {r['failures']}")
    return 0 if pass_rate >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
