#!/usr/bin/env python3
"""State manager for the dev-test-skill (V-Model SDLC).

Standard-library only, by design (no PyYAML, no third-party deps) — this
script must be runnable in any project this skill is dropped into without an
install step, the same portability rule `deep-research-skill`'s
`research_ledger.py` follows. It does not generate artifacts (specs, design
docs, test plans) itself — it tracks phase-gate state, the artifact
registry, and the requirements traceability matrix (RTM) while the agent
produces those artifacts with docx/pptx/xlsx/other tools.

`sdlc_state.json` is the single source of truth, read back by every command.
`sdlc_state.yaml` is a human-readable mirror regenerated after every
mutating command via a minimal write-only YAML emitter below — it is never
parsed back in, so it only ever needs to render the plain dict/list/scalar
shapes this script itself produces, not arbitrary YAML.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# V-Model phase definitions
# ---------------------------------------------------------------------------
# Order matters — it is the strict sequence `phase-start` enforces. Each
# validation-side phase names the verification-side phase it pairs with
# (the defining trait of the V: a test phase verifies its paired dev phase,
# not just "whatever came before it").

V_MODEL_PHASES = [
    {
        "id": "requirements-analysis",
        "side": "verification",
        "paired_with": "acceptance-testing",
        "typical_artifact": "Requirements Specification (BRS/SRS)",
        "suggested_skill": "docx",
    },
    {
        "id": "system-design",
        "side": "verification",
        "paired_with": "system-testing",
        "typical_artifact": "System Design Document",
        "suggested_skill": "docx",
    },
    {
        "id": "architecture-design",
        "side": "verification",
        "paired_with": "integration-testing",
        "typical_artifact": "Architecture Design (diagrams + interface specs)",
        "suggested_skill": "pptx",
    },
    {
        "id": "module-design",
        "side": "verification",
        "paired_with": "unit-testing",
        "typical_artifact": "Module / Low-Level Design Specification",
        "suggested_skill": "docx",
    },
    {
        "id": "implementation",
        "side": "vertex",
        "paired_with": None,
        "typical_artifact": "Source code + coding-standards checklist",
        "suggested_skill": "code",
    },
    {
        "id": "unit-testing",
        "side": "validation",
        "paired_with": "module-design",
        "typical_artifact": "Unit Test Plan / Cases",
        "suggested_skill": "xlsx",
    },
    {
        "id": "integration-testing",
        "side": "validation",
        "paired_with": "architecture-design",
        "typical_artifact": "Integration Test Plan / Cases",
        "suggested_skill": "xlsx",
    },
    {
        "id": "system-testing",
        "side": "validation",
        "paired_with": "system-design",
        "typical_artifact": "System Test Plan / Cases + Execution Report",
        "suggested_skill": "xlsx",
    },
    {
        "id": "acceptance-testing",
        "side": "validation",
        "paired_with": "requirements-analysis",
        "typical_artifact": "UAT Plan + Sign-off",
        "suggested_skill": "xlsx",
    },
]
PHASE_IDS = [p["id"] for p in V_MODEL_PHASES]
PHASE_INDEX = {p["id"]: i for i, p in enumerate(V_MODEL_PHASES)}
PHASE_BY_ID = {p["id"]: p for p in V_MODEL_PHASES}

PHASE_STATUSES = ["pending", "in_progress", "complete"]
ARTIFACT_TYPES = ["docx", "pptx", "xlsx", "md", "code", "other"]
ARTIFACT_STATUSES = ["draft", "reviewed", "approved"]
VERIFICATION_STATUSES = ["pending", "pass", "fail", "blocked", "not-applicable"]

ARTIFACT_FIELDS = [
    "artifact_id", "timestamp_utc", "phase", "title", "path", "type", "status",
]
TRACE_FIELDS = [
    "requirement_id", "title", "origin_phase", "design_ref", "code_ref",
    "test_phase", "test_case_id", "verification_status", "updated_utc", "notes",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def append_csv(path: Path, fields: list[str], row: dict[str, str]) -> None:
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


# ---------------------------------------------------------------------------
# Minimal write-only YAML emitter (no parsing, no third-party dependency).
# Only needs to handle the exact shapes this script produces: nested dicts,
# lists, strings, bools, numbers, and None. Not a general-purpose YAML writer.
# ---------------------------------------------------------------------------

def _yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or re.search(r'[:#\[\]{}"\'\n]|^\s|\s$', text):
        return json.dumps(text)  # valid YAML flow scalar, sidesteps escaping bugs
    return text


def _yaml_dump(obj, indent: int = 0, lines: list[str] | None = None) -> list[str]:
    if lines is None:
        lines = []
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            lines.append(f"{pad}{{}}")
        for key, value in obj.items():
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{pad}{key}:")
                _yaml_dump(value, indent + 1, lines)
            else:
                inline = "{}" if value == {} else ("[]" if value == [] else _yaml_scalar(value))
                lines.append(f"{pad}{key}: {inline}")
    elif isinstance(obj, list):
        if not obj:
            lines.append(f"{pad}[]")
        for item in obj:
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}-")
                _yaml_dump(item, indent + 1, lines)
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
    return lines


def write_yaml_mirror(path: Path, obj: dict) -> None:
    header = [
        "# Auto-generated human-readable mirror of sdlc_state.json.",
        "# Do not hand-edit — this file is overwritten by sdlc_ledger.py on",
        "# every state-changing command; sdlc_state.json is the source of truth.",
        "",
    ]
    path.write_text("\n".join(header + _yaml_dump(obj)) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def state_path(run_dir: Path) -> Path:
    return run_dir / "sdlc_state.json"


def load_state(run_dir: Path) -> dict:
    path = state_path(run_dir)
    if not path.exists():
        raise SystemExit(f"missing sdlc_state.json in {run_dir}; run init first")
    return read_json(path, {})


def save_state(run_dir: Path, state: dict) -> None:
    write_json(state_path(run_dir), state)
    write_yaml_mirror(run_dir / "sdlc_state.yaml", state)


def find_phase(state: dict, phase_id: str) -> dict:
    for phase in state["phases"]:
        if phase["id"] == phase_id:
            return phase
    raise SystemExit(
        f"unknown phase: {phase_id}; valid phases are: {', '.join(PHASE_IDS)}"
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    run_dir = Path(args.out_dir).expanduser().resolve()
    if run_dir.exists() and any(run_dir.iterdir()):
        if not args.force:
            raise SystemExit(
                f"directory already exists and is not empty: {run_dir}; use --force to reset it"
            )
        import shutil
        shutil.rmtree(run_dir)
    ensure_dir(run_dir)

    phases = []
    for p in V_MODEL_PHASES:
        phases.append({
            "id": p["id"],
            "side": p["side"],
            "paired_with": p["paired_with"],
            "typical_artifact": p["typical_artifact"],
            "suggested_skill": p["suggested_skill"],
            "status": "pending",
            "entered_utc": None,
            "exited_utc": None,
            "gate": {
                "approved": False,
                "approved_by": None,
                "approved_utc": None,
                "notes": None,
            },
            "artifact_ids": [],
        })

    state = {
        "project": args.project,
        "methodology": "v-model",
        "created_utc": now_utc(),
        "current_phase": PHASE_IDS[0],
        "phases": phases,
    }
    save_state(run_dir, state)
    write_csv(run_dir / "artifacts.csv", ARTIFACT_FIELDS, [])
    write_csv(run_dir / "traceability.csv", TRACE_FIELDS, [])

    readme = f"""# {args.project} — V-Model SDLC Tracker

Managed by `dev-test-skill`'s `sdlc_ledger.py`. Do not hand-edit
`sdlc_state.json`/`sdlc_state.yaml` — use the CLI so the state stays
consistent (`sdlc_state.yaml` is a regenerated mirror, never the source of
truth).

Files:
- `sdlc_state.json` — phase-gate state machine (source of truth)
- `sdlc_state.yaml` — human-readable mirror (regenerated, do not hand-edit)
- `artifacts.csv` — registry of every deliverable produced per phase
- `traceability.csv` — Requirements Traceability Matrix (RTM) rows

Current phase: **{PHASE_IDS[0]}**

Run `python sdlc_ledger.py status --dir {run_dir}` for the live summary.
"""
    (run_dir / "README.md").write_text(readme, encoding="utf-8")

    print(str(run_dir))
    return 0


def cmd_phase_start(args: argparse.Namespace) -> int:
    run_dir = Path(args.dir).expanduser().resolve()
    state = load_state(run_dir)
    phase = find_phase(state, args.phase)
    idx = PHASE_INDEX[args.phase]

    if phase["status"] == "complete":
        raise SystemExit(f"phase already complete: {args.phase}")

    if idx > 0 and not args.allow_parallel:
        prev = state["phases"][idx - 1]
        if not prev["gate"]["approved"]:
            raise SystemExit(
                f"phase '{args.phase}' cannot start: previous phase "
                f"'{prev['id']}' has not passed its gate review yet. "
                f"Pass --allow-parallel if this is intentional V-model "
                f"overlap (e.g. drafting a test plan alongside its paired "
                f"dev phase, which is normal V-model practice) rather than "
                f"skipping a gate."
            )

    phase["status"] = "in_progress"
    phase["entered_utc"] = now_utc()
    state["current_phase"] = args.phase
    save_state(run_dir, state)
    print(f"phase started: {args.phase}")
    return 0


def next_artifact_id(rows: list[dict[str, str]]) -> str:
    max_num = 0
    for row in rows:
        match = re.match(r"A(\d+)$", row.get("artifact_id", ""))
        if match:
            max_num = max(max_num, int(match.group(1)))
    return f"A{max_num + 1:04d}"


def cmd_add_artifact(args: argparse.Namespace) -> int:
    run_dir = Path(args.dir).expanduser().resolve()
    state = load_state(run_dir)
    phase = find_phase(state, args.phase)

    rows = read_csv(run_dir / "artifacts.csv")
    artifact_id = args.artifact_id or next_artifact_id(rows)
    if any(r.get("artifact_id") == artifact_id for r in rows):
        raise SystemExit(f"artifact_id already exists: {artifact_id}")
    if args.type not in ARTIFACT_TYPES:
        raise SystemExit(f"type must be one of: {', '.join(ARTIFACT_TYPES)}")
    status = args.status or "draft"
    if status not in ARTIFACT_STATUSES:
        raise SystemExit(f"status must be one of: {', '.join(ARTIFACT_STATUSES)}")

    row = {
        "artifact_id": artifact_id,
        "timestamp_utc": now_utc(),
        "phase": args.phase,
        "title": args.title,
        "path": args.path,
        "type": args.type,
        "status": status,
    }
    append_csv(run_dir / "artifacts.csv", ARTIFACT_FIELDS, row)

    phase["artifact_ids"].append(artifact_id)
    save_state(run_dir, state)
    print(f"recorded artifact {artifact_id}")
    return 0


def cmd_add_trace(args: argparse.Namespace) -> int:
    run_dir = Path(args.dir).expanduser().resolve()
    load_state(run_dir)  # validates dir is initialized
    if args.origin_phase not in PHASE_IDS:
        raise SystemExit(f"origin-phase must be one of: {', '.join(PHASE_IDS)}")
    if args.test_phase and args.test_phase not in PHASE_IDS:
        raise SystemExit(f"test-phase must be one of: {', '.join(PHASE_IDS)}")
    status = args.verification_status or "pending"
    if status not in VERIFICATION_STATUSES:
        raise SystemExit(f"verification-status must be one of: {', '.join(VERIFICATION_STATUSES)}")

    rows = read_csv(run_dir / "traceability.csv")
    # Upsert on (requirement_id, test_phase) — a requirement can have one row
    # per validation-side phase it's checked against.
    key = (args.requirement_id, args.test_phase or "")
    updated = False
    for row in rows:
        if (row.get("requirement_id"), row.get("test_phase", "")) == key:
            row.update({
                "title": args.title or row.get("title", ""),
                "origin_phase": args.origin_phase,
                "design_ref": args.design_ref or row.get("design_ref", ""),
                "code_ref": args.code_ref or row.get("code_ref", ""),
                "test_case_id": args.test_case_id or row.get("test_case_id", ""),
                "verification_status": status,
                "updated_utc": now_utc(),
                "notes": args.notes or row.get("notes", ""),
            })
            updated = True
            break
    if not updated:
        rows.append({
            "requirement_id": args.requirement_id,
            "title": args.title or "",
            "origin_phase": args.origin_phase,
            "design_ref": args.design_ref or "",
            "code_ref": args.code_ref or "",
            "test_phase": args.test_phase or "",
            "test_case_id": args.test_case_id or "",
            "verification_status": status,
            "updated_utc": now_utc(),
            "notes": args.notes or "",
        })
    write_csv(run_dir / "traceability.csv", TRACE_FIELDS, rows)
    print(f"recorded trace row: {args.requirement_id} / {args.test_phase or '(no test phase yet)'}")
    return 0


def cmd_gate_review(args: argparse.Namespace) -> int:
    run_dir = Path(args.dir).expanduser().resolve()
    state = load_state(run_dir)
    phase = find_phase(state, args.phase)
    idx = PHASE_INDEX[args.phase]

    if phase["status"] != "in_progress":
        raise SystemExit(
            f"phase '{args.phase}' is not in_progress (status={phase['status']}); "
            f"run phase-start first"
        )

    warnings: list[str] = []
    if not phase["artifact_ids"]:
        if not args.force:
            raise SystemExit(
                f"gate review blocked: phase '{args.phase}' has no registered "
                f"artifacts. Use add-artifact first, or pass --force to "
                f"override (not recommended — a phase gate with zero "
                f"deliverables defeats the point of a gate review)."
            )
        warnings.append("approved with zero registered artifacts (--force used)")

    if phase["side"] == "validation":
        paired = phase["paired_with"]
        trace_rows = read_csv(run_dir / "traceability.csv")
        linked = [r for r in trace_rows if r.get("test_phase") == args.phase]
        if not linked:
            warnings.append(
                f"no traceability.csv rows reference this phase — nothing "
                f"proves this test phase actually verified its paired "
                f"phase '{paired}'"
            )
        else:
            unresolved = [r for r in linked if r.get("verification_status") == "pending"]
            if unresolved:
                warnings.append(
                    f"{len(unresolved)} traceability row(s) still 'pending' "
                    f"verification_status at gate time"
                )

    phase["status"] = "complete"
    phase["exited_utc"] = now_utc()
    phase["gate"] = {
        "approved": True,
        "approved_by": args.approved_by,
        "approved_utc": now_utc(),
        "notes": args.notes,
    }

    if idx + 1 < len(state["phases"]):
        next_phase = state["phases"][idx + 1]["id"]
        if state["current_phase"] == args.phase:
            state["current_phase"] = next_phase
    save_state(run_dir, state)

    result = {"phase": args.phase, "gate_approved": True, "warnings": warnings}
    print(json.dumps(result, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    run_dir = Path(args.dir).expanduser().resolve()
    state = load_state(run_dir)
    artifacts = read_csv(run_dir / "artifacts.csv")
    trace = read_csv(run_dir / "traceability.csv")

    phase_summary = []
    for phase in state["phases"]:
        phase_summary.append({
            "id": phase["id"],
            "side": phase["side"],
            "status": phase["status"],
            "gate_approved": phase["gate"]["approved"],
            "artifact_count": len(phase["artifact_ids"]),
        })

    reqs = {r["requirement_id"] for r in trace if r.get("requirement_id")}
    verified = {
        r["requirement_id"] for r in trace
        if r.get("requirement_id") and r.get("verification_status") == "pass"
    }

    data = {
        "project": state["project"],
        "current_phase": state["current_phase"],
        "phases": phase_summary,
        "total_artifacts": len(artifacts),
        "total_requirements_traced": len(reqs),
        "requirements_verified_pass": len(verified),
        "requirements_not_yet_pass": sorted(reqs - verified),
    }
    print(json.dumps(data, indent=2))
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    run_dir = Path(args.dir).expanduser().resolve()
    state = load_state(run_dir)
    artifacts = read_csv(run_dir / "artifacts.csv")
    trace = read_csv(run_dir / "traceability.csv")

    errors: list[str] = []
    warnings: list[str] = []

    seen_complete = True
    for phase in state["phases"]:
        if phase["status"] == "complete" and not phase["gate"]["approved"]:
            errors.append(f"phase '{phase['id']}' marked complete without an approved gate")
        if phase["status"] == "pending" and phase["gate"]["approved"]:
            errors.append(f"phase '{phase['id']}' has an approved gate but status is still pending")
        if phase["status"] != "complete":
            seen_complete = False
        elif not seen_complete:
            # a later phase complete while an earlier one wasn't yet, at time
            # of iteration — only meaningful if order was violated, which
            # phase-start already prevents; kept as a defence-in-depth check.
            pass

    artifact_ids = {a.get("artifact_id") for a in artifacts}
    for phase in state["phases"]:
        for aid in phase["artifact_ids"]:
            if aid not in artifact_ids:
                errors.append(f"phase '{phase['id']}' references missing artifact {aid}")

    req_ids = {r.get("requirement_id") for r in trace if r.get("requirement_id")}
    acceptance_linked = {
        r.get("requirement_id") for r in trace
        if r.get("test_phase") == "acceptance-testing"
    }
    missing_acceptance = req_ids - acceptance_linked
    if missing_acceptance:
        warnings.append(
            "requirements with no acceptance-testing traceability row "
            "(the defining V-model guarantee — every requirement traces to "
            "an acceptance test): " + ", ".join(sorted(missing_acceptance))
        )

    stale_pending = [
        r for r in trace
        if r.get("verification_status") == "pending" and r.get("test_case_id")
    ]
    if stale_pending:
        warnings.append(
            f"{len(stale_pending)} traceability row(s) have a test_case_id "
            f"assigned but verification_status is still 'pending' — run the "
            f"test case and update the status"
        )

    result = {
        "run_dir": str(run_dir),
        "project": state["project"],
        "current_phase": state["current_phase"],
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(result, indent=2))
    return 1 if errors else 0


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.dir).expanduser().resolve()
    state = load_state(run_dir)
    trace = read_csv(run_dir / "traceability.csv")

    if args.format == "json":
        print(json.dumps(trace, indent=2))
        return 0

    lines = [
        f"# Requirements Traceability Matrix — {state['project']}",
        "",
        "| Requirement | Title | Origin Phase | Design Ref | Code Ref | Test Phase | Test Case | Status |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in trace:
        lines.append(
            "| {requirement_id} | {title} | {origin_phase} | {design_ref} | "
            "{code_ref} | {test_phase} | {test_case_id} | {verification_status} |".format(**{
                k: row.get(k, "") for k in TRACE_FIELDS
            })
        )
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage a V-Model SDLC phase-gate ledger (dev-test-skill)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="create a new SDLC tracking directory")
    p_init.add_argument("--project", required=True)
    p_init.add_argument("--out-dir", default="dev")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_start = sub.add_parser("phase-start", help="begin a phase")
    p_start.add_argument("--dir", required=True)
    p_start.add_argument("--phase", required=True, choices=PHASE_IDS)
    p_start.add_argument(
        "--allow-parallel", action="store_true",
        help="allow starting before the previous phase's gate is approved "
             "(legitimate V-model overlap, e.g. drafting a test plan "
             "alongside its paired dev phase)",
    )
    p_start.set_defaults(func=cmd_phase_start)

    p_art = sub.add_parser("add-artifact", help="register a produced deliverable")
    p_art.add_argument("--dir", required=True)
    p_art.add_argument("--artifact-id")
    p_art.add_argument("--phase", required=True, choices=PHASE_IDS)
    p_art.add_argument("--title", required=True)
    p_art.add_argument("--path", required=True)
    p_art.add_argument("--type", required=True, choices=ARTIFACT_TYPES)
    p_art.add_argument("--status", choices=ARTIFACT_STATUSES)
    p_art.set_defaults(func=cmd_add_artifact)

    p_trace = sub.add_parser("add-trace", help="add/update an RTM row")
    p_trace.add_argument("--dir", required=True)
    p_trace.add_argument("--requirement-id", required=True)
    p_trace.add_argument("--title")
    p_trace.add_argument("--origin-phase", required=True, choices=PHASE_IDS)
    p_trace.add_argument("--design-ref")
    p_trace.add_argument("--code-ref")
    p_trace.add_argument("--test-phase", choices=PHASE_IDS)
    p_trace.add_argument("--test-case-id")
    p_trace.add_argument("--verification-status", choices=VERIFICATION_STATUSES)
    p_trace.add_argument("--notes")
    p_trace.set_defaults(func=cmd_add_trace)

    p_gate = sub.add_parser("gate-review", help="approve a phase gate and advance")
    p_gate.add_argument("--dir", required=True)
    p_gate.add_argument("--phase", required=True, choices=PHASE_IDS)
    p_gate.add_argument("--approved-by", required=True)
    p_gate.add_argument("--notes")
    p_gate.add_argument("--force", action="store_true")
    p_gate.set_defaults(func=cmd_gate_review)

    p_status = sub.add_parser("status", help="print current SDLC status")
    p_status.add_argument("--dir", required=True)
    p_status.set_defaults(func=cmd_status)

    p_lint = sub.add_parser("lint", help="validate ledger integrity")
    p_lint.add_argument("--dir", required=True)
    p_lint.set_defaults(func=cmd_lint)

    p_report = sub.add_parser("report", help="print the traceability matrix")
    p_report.add_argument("--dir", required=True)
    p_report.add_argument("--format", choices=["md", "json"], default="md")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
