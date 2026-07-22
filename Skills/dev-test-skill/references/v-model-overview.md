# The V-Model — Reference

Researched via Google AI Mode (g-query) and standard SDLC references,
2026-07-20. This is the model this skill implements — read it once to
understand *why* the ledger enforces what it enforces, not just *what*
commands exist.

## Shape

The V-Model pairs every development ("Verification") phase on the left side
of the V with a corresponding testing ("Validation") phase on the right
side, joined at the bottom by the Coding vertex. The defining trait —
what makes it a "V" and not just a renamed Waterfall — is that **each
validation phase verifies its specific paired verification phase**, not
just "whatever came before." A defect found in System Testing traces back
to System Design, not to some undifferentiated earlier blob of work.

```
Requirements Analysis                              Acceptance Testing
        \                                                  /
         System Design                         System Testing
                \                                  /
                 Architecture Design    Integration Testing
                        \                  /
                         Module Design   Unit Testing
                                \        /
                                 Coding
```

| # | Verification (left) | Pairs with | Validation (right) |
|---|---|---|---|
| 1 | Requirements Analysis | ↔ | Acceptance Testing |
| 2 | System Design | ↔ | System Testing |
| 3 | Architecture Design | ↔ | Integration Testing |
| 4 | Module Design | ↔ | Unit Testing |
| — | **Coding** (vertex, no pair) | | |

## Why this skill exists instead of a generic "write tests" reminder

Two properties distinguish the V-Model from just "do requirements, then
design, then code, then test, in order":

1. **Early test authoring.** The test plan for a validation phase is
   drafted *alongside* its paired verification phase, not after coding is
   done — e.g. the Acceptance Test Plan is drafted during Requirements
   Analysis, using the same requirements as its basis, so ambiguity in a
   requirement surfaces while it's still cheap to fix. The ledger's
   `--allow-parallel` flag on `phase-start` exists specifically to support
   this — starting a validation phase (to draft its test plan) before its
   paired verification phase's gate is approved is normal V-Model practice,
   not a process violation. What the ledger does still block by default is
   skipping a gate outright.
2. **Traceability.** Every requirement must be traceable all the way to an
   acceptance test that verifies it — that end-to-end chain
   (requirement → design → code → test case → verification result) is the
   Requirements Traceability Matrix (RTM), and it's the artifact that
   actually proves the V was followed, not just asserted. The ledger's
   `traceability.csv` and the `lint` command's "no acceptance-testing row"
   warning exist to keep that chain honest.

## Phase gates

A phase gate is a checkpoint: before work "crosses" from one phase to the
next, the deliverables produced in the current phase are reviewed and
formally approved. The general shape (confirmed via research, standard
project-management practice):

```
[Phase Work] → [Collect Deliverables] → [Gate Review] → [Approve / Rework]
```

An approved gate should mean: the phase's deliverables exist, were
reviewed, and (for validation-side phases) actually verify something
traceable back to their paired phase — not just "time to move on." The
ledger's `gate-review` command encodes the minimum mechanical version of
this (artifacts exist, traceability is linked for validation phases) and
surfaces warnings for anything softer that still needs a human judgment
call — it does not replace an actual review conversation.

## Typical deliverables per phase

| Phase | Typical deliverable | Natural format |
|---|---|---|
| Requirements Analysis | Business/Software Requirements Specification (BRS/SRS) | Document (docx) |
| System Design | System Design Document (SDD) | Document (docx) |
| Architecture Design | Architecture diagrams, component/interface specs | Slides/diagrams (pptx) + document (docx) |
| Module Design | Low-Level/Module Design Specification | Document (docx) |
| Coding | Source code, coding-standards checklist | Code (not a document skill) |
| Unit Testing | Unit Test Plan/Cases | Spreadsheet (xlsx) |
| Integration Testing | Integration Test Plan/Cases | Spreadsheet (xlsx) |
| System Testing | System Test Plan/Cases + Execution Report | Spreadsheet (xlsx) |
| Acceptance Testing | UAT Plan + sign-off | Spreadsheet (xlsx) or document (docx) |

These are defaults, not requirements — `add-artifact --type` accepts
`docx|pptx|xlsx|md|code|other`, so a project can deviate (e.g. Architecture
Design as a single docx if diagrams aren't needed) without fighting the
tool.

## Sources

- Google AI Mode (g-query), queried 2026-07-20: V-Model phase structure,
  typical deliverables, phase-gate/entry-exit-criteria process.
- Standard SDLC/V-Model literature (well-established, cross-checked against
  general knowledge — the phase list above is the widely-used canonical
  version, not a novel interpretation).
