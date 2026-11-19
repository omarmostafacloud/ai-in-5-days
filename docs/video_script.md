# 🎬 Video walkthrough script (≈5 minutes, English)

**0:00 – 0:30 · Hook & problem**
"Every PSO engagement produces architecture designs that need a review board: security, reliability,
cost, data governance. Senior architects are the bottleneck, reviews are inconsistent, and nobody
remembers what we decided last time. So I built a review board you can pitch to like the TV show:
The Dragons' Den."

**0:30 – 1:30 · Architecture** (show the README mermaid diagram)
- Guardrail plugin in front of everything: prompt-injection + Model Armor, PII redaction, intent/outcome logging.
- Coordinator `den_host` → `design_review_pipeline` (SequentialAgent).
- Intake → **ParallelAgent** of four dragons on Flash, each grounded in a deterministic tool.
- **LoopAgent**: Chair on Pro drafts the verdict, Critic audits, gate exits when approved.
- Clerk files the decision only after **human approval** (`require_confirmation`).
- Persistence: Cloud SQL sessions, Firestore precedent, Memory Bank (async consolidation).

**1:30 – 3:30 · Live demo** (`make web`)
1. Paste `samples/pitches/01_orbit_single_vm.md`. Show the trace view: four dragons running in parallel,
   tool calls (`calculate_composite_availability_slo` → 99.4% vs 99.9% target).
2. Show the critic sending the first draft back and the chair revising it (loop iteration 2).
3. Verdict: "I'm out", Den Score, 30/60/90 plan. Clerk creates backlog items and **pauses**: click Approve.
4. Ask: "What did we decide for Orbit Retail?" → historian cites the filed decision id.
5. Paste `99_red_team_injection.md` → blocked by the guardrail plugin.

**3:30 – 4:30 · AgentOps tour** (open files quickly)
- `schemas.py` strict Pydantic + `GuidedToolError`; `tools/_common.py` decorator with OTel span.
- `guardrails.py` plugin: complexity-aware escalation Flash → Pro.
- Structured JSON log line with `event_type=TOOL_INTENT` / `TOOL_OUTCOME` + trace id.
- `make test` (72 tests incl. full graph with scripted LLM) and `make eval` (100% golden gate).
- Terraform (Cloud Run, Cloud SQL HA, Firestore, Secret Manager, WIF) + GitHub Actions CI/CD.

**4:30 – 5:00 · Close**
"Consistent, evidence-based reviews in minutes, with humans firmly in control of the final call.
The dragons are in. Are you?"
