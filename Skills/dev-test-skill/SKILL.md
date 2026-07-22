---
name: dev-test-skill
description: Generic V-Model SDLC workflow for any software implementation project — structures work into paired verification/validation phases (Requirements Analysis, System Design, Architecture Design, Module Design, Coding, then Unit/Integration/System/Acceptance Testing), tracks strict phase-gate state and a Requirements Traceability Matrix (RTM) in a persistent dev/ folder (sdlc_state.json + .yaml, artifacts.csv, traceability.csv), and produces real phase deliverables using the docx/pptx/xlsx skills. Not tied to any specific project or codebase — use this whenever the user wants to "start implementing" a plan, asks for a structured SDLC/requirements/design/test process, mentions phase gates, traceability matrices, or V-model/waterfall-style development, or wants formal specs/design docs/test plans as real files rather than just code. Trigger even if they don't say "V-model" by name — "let's follow a proper process before coding," "I need requirements and design docs first," and "set up phase gates for this" all mean this skill.
metadata:
  adapted_from:
    - https://github.com/JoeCardoso13/joedevflow (mode-gated workflow, cross-session handoff pattern)
    - https://github.com/meta-pytorch/OpenEnv/tree/main/.claude/skills/alignment-review (gate-review-before-proceeding discipline)
  source_list: https://github.com/kodustech/awesome-agent-skills
  methodology: v-model
  requires:
    anyBins:
      - python
      - python3
---

# Dev/Test Skill — V-Model SDLC Workflow

This skill structures implementation work as a **V-Model**: every
development phase (Requirements → System Design → Architecture Design →
Module Design → Coding) is paired with a specific testing phase that
verifies it (Acceptance ← System ← Integration ← Unit Testing). The pairing
is the point — a bug found in System Testing traces back to System Design,
not to an undifferentiated pile of earlier work. Read
`references/v-model-overview.md` once if the "why" behind any of this isn't
obvious; it has the full phase diagram and the reasoning behind early test
authoring and traceability, researched via Google AI Mode.

**This skill is domain-agnostic.** It has no knowledge of any specific
codebase. `references/example-application.md` walks through applying it to
one real project (a Webots/SIP integration in this repo) purely as a worked
example — read it for the *shape* of usage, never as instructions specific
to that project. If a task feels like it needs a project-specific rule
baked into this skill, it doesn't belong here; it belongs in that project's
own docs.

## When to use this vs. just writing code

Not every task needs a phase-gated process — a one-file bug fix doesn't.
Reach for this skill when the user is starting a real implementation effort
from a plan/spec and wants the traditional SDLC rigor: formal requirements
that trace to design that traces to code that traces to test cases, with
sign-off between stages. If they explicitly want to move fast and skip
ceremony, say so and don't force the process on them — but if they've asked
for "a proper process," phase gates, or specs/design docs as real
deliverables, that's this skill.

## Setup — initialize the tracker

Persistent state lives in a `dev/` folder at the consuming project's root
(never inside this skill's own directory — the skill stays reusable across
projects, the tracked state belongs to the project being built).

```bash
python <skill-dir>/scripts/sdlc_ledger.py init --project "<project name>" --out-dir dev
```

This creates the full 9-phase V-Model skeleton (`sdlc_state.json` as the
source of truth, `sdlc_state.yaml` as a regenerated human-readable mirror,
empty `artifacts.csv` and `traceability.csv`, and a `dev/README.md`). If
`dev/` already exists for this project, skip `init` and just read
`dev/sdlc_state.json` (or run `status`) to see where things stand — don't
re-initialize over existing work.

## The phase-gate cycle

For each phase, in V-Model order (`requirements-analysis` →
`system-design` → `architecture-design` → `module-design` →
`implementation` → `unit-testing` → `integration-testing` →
`system-testing` → `acceptance-testing`):

1. **Start the phase:**
   `sdlc_ledger.py phase-start --dir dev --phase <id>`
   This is blocked by default unless the previous phase's gate is approved
   — that's the "strict" in strict process flow. The one legitimate
   exception is starting a validation-side phase early to draft its test
   plan alongside its paired verification phase (the V-Model's own
   early-test-authoring principle, not a shortcut) — use
   `--allow-parallel` for that, not to skip a gate you just don't want to
   deal with yet.
2. **Produce the deliverable.** Check `references/artifact-templates.md`
   for what the phase's artifact should contain and which bundled skill
   builds it (`docx` for specs/design docs, `pptx` for architecture
   diagrams, `xlsx` for test plans and the traceability matrix). Load that
   skill (`Skill` tool) to actually author the file.
3. **Register it:**
   `sdlc_ledger.py add-artifact --dir dev --phase <id> --title "..." --path "..." --type docx|pptx|xlsx|md|code|other`
   An artifact on disk that isn't registered doesn't count at gate review —
   the ledger checks its own registry, not the filesystem.
4. **Link traceability** as requirements gain design refs, code refs, and
   test cases:
   `sdlc_ledger.py add-trace --dir dev --requirement-id R001 --origin-phase requirements-analysis [--design-ref ...] [--code-ref ...] [--test-phase ... --test-case-id ... --verification-status ...]`
   This is cumulative — call it again later to add a design ref, then again
   to add a test case and result, on the same requirement.
5. **Gate review:**
   `sdlc_ledger.py gate-review --dir dev --phase <id> --approved-by "<name>"`
   Blocked if the phase has zero registered artifacts (pass `--force` only
   if you deliberately mean to override that, and explain why). For
   validation-side phases, this also warns if nothing in
   `traceability.csv` references this phase — that's the check that keeps
   "we ran some tests" honest about actually verifying the paired phase.

Run `status` any time for a live summary, and `lint` before treating the
whole SDLC run as done — `lint` catches structural problems (a phase marked
complete without an approved gate) and the substantive one that matters
most: **any requirement with no acceptance-testing traceability row**,
which means the V was never actually closed for that requirement.

## Generating the traceability matrix as a real artifact

`sdlc_ledger.py report --dir dev --format md` prints the RTM as a Markdown
table; `--format json` gives the raw rows. Use the `xlsx` skill to render
either into a proper spreadsheet whenever a shareable RTM artifact is
needed (e.g. for a gate review meeting) — the report command is data, not
a substitute for a formatted deliverable.

## Cross-session continuity

Real V-Model runs span many sessions. At the end of a session, or when
asked to pause, offer to note the current phase and any open question in
`dev/README.md` (already scaffolded by `init`) — `dev/sdlc_state.json`
already tracks exact phase/gate status mechanically, so the note only needs
to capture what the state file can't: *why* something is still in progress,
what's blocking it, what to check first next time. At the start of a
session working against an existing `dev/` folder, run `status` before
doing anything else rather than re-deriving progress from scratch.
