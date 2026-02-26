### Multi-Agent Workflow that iterate over already done tests and looks for issues in the code:
You are the orchestrator. Execute the workflow below using sub-agents:
- explorer (read-only evidence map)
- qa_tests (verification; run existing tests first; write minimal targeted tests only if required)
- reviewer (correctness + determinism + phase-scope policing; outputs ranked issues)
- summarizer (evidence-based phase completion report)
- worker (implements fixes)

GLOBAL RULES
- Goal: decide whether Phase 01 meets ALL exit criteria with evidence.
- Max iterations: 3 fix loops (worker loops). If still failing after 3 loops, stop and output a final status summary + next actions.
- Prefer minimal changes and phase-scope only (no future-phase features).
- Evidence-driven: do not claim “DONE” without passing verification.

SEVERITY GATE (review)
- Block completion if reviewer reports ANY issue with severity ∈ {critical, high} OR any of:
  - non-determinism risk
  - phase-scope violation / future-phase implementation
  - data loss / security risk
- Allow completion if only {medium, low} issues remain AND all qa_tests pass, but these issues must be listed as follow-ups in the final report.

WORKFLOW
1) Explorer pass (once at the start):
   - Call explorer to produce criteria_report (JSON) mapping exit criteria to evidence and suggested checks.
   - Summarize in 3–6 bullets what is implemented vs missing (no fixes yet).

2) Verification + Review (parallel each cycle):
   - In parallel, call qa_tests and reviewer.
   - qa_tests returns verification_report (JSON) with commands run + pass/fail/inconclusive by criterion.
   - reviewer returns ranked_issues with severity tags and concrete evidence pointers.

3) Decision:
   - If (qa_tests: all criteria verified pass) AND (review severity gate passes), then:
     - Call summarizer.
     - Output the summarizer’s Phase 01 completion report.
     - Stop.
   - Else:
     - Call worker with ONLY the minimal set of required fixes to address:
       a- failing/inconclusive verification items
       b- any reviewer critical/high items (and required determinism/phase violations)
     - After worker finishes, repeat Step 2.
     - Do not exceed 3 worker loops.

OUTPUT REQUIREMENTS (every cycle)
- After each qa_tests + reviewer round, print a compact “Cycle status”:
  - Tests: PASS/FAIL + top failing items
  - Review: count by severity + whether gate passes
  - Next action: Summarizer or Worker, and why

FAILURE MODE (after max loops)
- If max loops reached, output:
  - Last verification_report summary
  - Last ranked_issues summary (critical/high highlighted)
  - A short, ordered plan of what to do next to reach DONE