# Worked Example: Applying This Skill to the Webots SIP Bridge

**This file is illustrative only.** The skill itself (`SKILL.md`) is generic
and has no knowledge of Webots, SIP, or this specific project — everything
below is one example of using the generic V-Model ledger against a real
plan (`plan/webots-sip-integration-plan.md` in this repo), so the shape of
"what does this look like in practice" is concrete instead of abstract.
Don't generalize from this file back into the skill itself; if you're
tempted to add a Webots-specific command or phase to `sdlc_ledger.py`,
that's a sign it belongs here instead.

## Kickoff

```bash
python .claude/skills/dev-test-skill/scripts/sdlc_ledger.py init \
  --project "Webots SIP Bridge" --out-dir dev
```

## Requirements Analysis phase

The plan's §1 (Goal) and §7 (Create vs. control mapping table) are the raw
material for the Requirements Spec. Each row of that mapping table becomes
a numbered requirement, e.g.:

- `R001` — "Agent can launch a Webots simulation from a world file"
  (source: plan §7, row 1)
- `R002` — "Agent can read live robot/sensor telemetry" (plan §7, row 3)
- `R003` — "Agent can actuate a robot's motors through a governed path"
  (plan §7, row 4)
- `R004` — "System serializes concurrent access to one Supervisor
  connection" (plan §3's confirmed thread-safety finding — a non-functional
  requirement, still gets an ID and a trace row)

```bash
python .claude/skills/dev-test-skill/scripts/sdlc_ledger.py phase-start \
  --dir dev --phase requirements-analysis
# ... write dev/01-requirements-analysis/srs.docx using the docx skill ...
python .claude/skills/dev-test-skill/scripts/sdlc_ledger.py add-artifact \
  --dir dev --phase requirements-analysis --title "SRS v1" \
  --path dev/01-requirements-analysis/srs.docx --type docx
python .claude/skills/dev-test-skill/scripts/sdlc_ledger.py add-trace \
  --dir dev --requirement-id R001 --title "Launch simulation from world file" \
  --origin-phase requirements-analysis
# (repeat add-trace for R002, R003, R004, ...)
python .claude/skills/dev-test-skill/scripts/sdlc_ledger.py gate-review \
  --dir dev --phase requirements-analysis --approved-by "<reviewer name>"
```

## System / Architecture / Module Design phases

The plan's §5 (Architecture decision), §6 (Component design diagram), and
the per-file table in §6.1 map directly onto System Design (the bridge as a
whole, its relationship to SIP and Webots), Architecture Design (the
component diagram — a natural `pptx` deliverable), and Module Design (one
subsection per file: `WebotsProcessManager`, `WebotsSupervisorConnection`,
`WebotsTelemetryAdapter`, the execution dispatch table, etc.).

Each module-design entry should link back to the requirement(s) it
implements — e.g. `WebotsSupervisorConnection`'s design entry cites `R004`
via `add-trace --design-ref`.

## Implementation phase

WBI-01 through WBI-13 (plan §10) *are* the coding-phase work breakdown —
this skill doesn't replace that numbering, it sits alongside it. Each WBI
task's code is the deliverable; no `dev/` artifact is required here beyond
an optional coding-standards note, per `artifact-templates.md`.

## Test phases

The plan's own §11 (Testing strategy) already specifies real-only,
skip-gracefully testing — that maps directly onto the V-Model's Unit /
Integration / System test phases:

- **Unit Testing** ↔ Module Design: `CallbackAdapter` with fixed literal
  values, per module — no live Webots needed.
- **Integration Testing** ↔ Architecture Design: the concurrency test from
  plan §11 (fire a telemetry read and an actuation write concurrently,
  assert no interleaving) is exactly an integration test verifying the
  architecture's serialization guarantee.
- **System Testing** ↔ System Design: end-to-end — launch a real Webots
  instance, observe via `sip_observe`, actuate via `sip_execute_safe`,
  confirm governance behaves (plan §11's manual verification step, formalized
  as a system test).
- **Acceptance Testing** ↔ Requirements Analysis: does an actual MCP agent
  (Claude Code, etc.) successfully create and control a simulation
  end-to-end, per the original goal in plan §1?

Each test case gets a `test_case_id` (e.g. `UT-001`, `IT-001`, `ST-001`,
`AT-001`) and an `add-trace` row linking it back to the requirement it
verifies — by the time Acceptance Testing's gate is reviewed, `lint` should
show zero "no acceptance-testing traceability row" warnings for `R001`–`R004`.
