# Artifact Templates — what to put in each deliverable, and which skill builds it

For each phase: the minimum sections a deliverable needs to be a real gate
artifact (not just a title page), and which bundled document-authoring
skill is the natural fit. Load the named skill (`Skill` tool) when you're
ready to actually produce the file — this reference only tells you *what
goes in it* and *where it lands under `dev/`*; the docx/pptx/xlsx skills
themselves own the actual file-format mechanics.

Register every produced file with `add-artifact` immediately after creating
it — an artifact that exists on disk but isn't in `artifacts.csv` doesn't
count for gate review purposes (`gate-review` reads the registry, not the
filesystem).

## Suggested `dev/` layout

```
dev/
├── sdlc_state.json / .yaml       ← managed by sdlc_ledger.py
├── artifacts.csv                 ← managed by sdlc_ledger.py
├── traceability.csv              ← managed by sdlc_ledger.py
├── 01-requirements-analysis/
├── 02-system-design/
├── 03-architecture-design/
├── 04-module-design/
├── 06-unit-testing/
├── 07-integration-testing/
├── 08-system-testing/
├── 09-acceptance-testing/
└── traceability-matrix.xlsx      ← generated snapshot, see below
```
(No `05-implementation/` — code lives in the project's normal source tree,
not under `dev/`; the implementation phase's "artifact" is the code itself
plus an optional coding-standards checklist.)

## Requirements Analysis → docx

**Skill:** `docx`. **Minimum sections:**
- Purpose/scope, stakeholders
- Numbered functional requirements (`R001`, `R002`, ...) — these IDs are
  exactly what goes into `add-trace --requirement-id`
- Non-functional requirements (performance, safety, security as relevant)
- Out-of-scope / explicit exclusions
- Acceptance criteria per requirement (this section *is* the seed of the
  Acceptance Test Plan — draft them together, per the V-Model's early-test-
  authoring principle)

## System Design → docx

**Skill:** `docx`. **Minimum sections:**
- System context (what the system is, its boundaries, external interfaces)
- Major subsystems/components and their responsibilities
- Data flow / control flow at the system level
- Mapping back to requirement IDs from the Requirements Spec (which
  requirement does each subsystem satisfy?) — feeds `add-trace --design-ref`

## Architecture Design → pptx (+ optional docx)

**Skill:** `pptx` for the diagrams (component diagrams, sequence diagrams,
deployment/interface diagrams), `docx` for narrative if the diagrams alone
aren't self-explanatory. **Minimum content:**
- Component/module boundaries and their interfaces (inputs/outputs,
  protocols, data contracts)
- Integration points between components — this is exactly what Integration
  Testing will verify, so be explicit about what "correctly integrated"
  means for each boundary

## Module Design → docx

**Skill:** `docx`. **Minimum sections:**
- Per-module: inputs, outputs, internal logic summary, error handling,
  edge cases the module must handle
- Enough detail that a unit test plan can be written directly from it
  without re-deriving the module's intended behavior from scratch

## Unit / Integration / System / Acceptance Test Plans → xlsx

**Skill:** `xlsx`. **Minimum columns** (one row per test case):
- `test_case_id` (matches `add-trace --test-case-id`)
- `requirement_id` or `design_ref` it verifies
- Preconditions
- Steps
- Expected result
- Actual result (filled in during execution)
- Status (pending/pass/fail/blocked — matches `add-trace --verification-status`)

Keep the `test_case_id` scheme stable across all four test-plan spreadsheets
(e.g. `UT-###`, `IT-###`, `ST-###`, `AT-###`) so a glance at an ID tells you
which phase it belongs to without opening the file.

## Traceability Matrix snapshot → xlsx

The ledger's `report --format json` output is the data; use the `xlsx`
skill to render it as a proper spreadsheet snapshot whenever you want a
shareable RTM artifact (e.g. for a phase-gate review meeting) rather than
reading `traceability.csv` raw. Regenerate it whenever the underlying CSV
changes meaningfully — it's a rendered view, not a second source of truth.

## Coding-standards checklist (Implementation phase) → docx or md

Optional but recommended if the project has specific conventions beyond
generic good practice (naming schemes, error-handling patterns, what "done"
means for a module). A short `md` file is fine — this is the one phase
where a lightweight artifact is normal, since the code itself is the real
deliverable.
