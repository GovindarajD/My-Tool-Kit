import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sdlc_ledger.py"


class SdlcLedgerCliTests(unittest.TestCase):
    def run_cli(self, *args, expect_success=True):
        cmd = [sys.executable, "-S", str(SCRIPT), *args]
        proc = subprocess.run(cmd, text=True, capture_output=True)
        if expect_success and proc.returncode != 0:
            self.fail(f"command failed: {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        if not expect_success and proc.returncode == 0:
            self.fail(f"command unexpectedly succeeded: {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}")
        return proc

    def init_run(self, tmpdir, project="Test Project"):
        proc = self.run_cli("init", "--project", project, "--out-dir", tmpdir, "--force")
        return Path(proc.stdout.strip().splitlines()[-1])

    def test_init_creates_expected_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(tmp)
            for name in ["sdlc_state.json", "sdlc_state.yaml", "artifacts.csv", "traceability.csv", "README.md"]:
                self.assertTrue((run_dir / name).exists(), name)
            state = json.loads((run_dir / "sdlc_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["current_phase"], "requirements-analysis")
            self.assertEqual(len(state["phases"]), 9)
            self.assertEqual(state["phases"][0]["status"], "pending")

    def test_phase_start_first_phase_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(tmp)
            self.run_cli("phase-start", "--dir", str(run_dir), "--phase", "requirements-analysis")
            state = json.loads((run_dir / "sdlc_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["phases"][0]["status"], "in_progress")

    def test_phase_start_out_of_order_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(tmp)
            # system-design (index 1) before requirements-analysis gate is approved
            self.run_cli(
                "phase-start", "--dir", str(run_dir), "--phase", "system-design",
                expect_success=False,
            )

    def test_phase_start_out_of_order_allowed_with_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(tmp)
            self.run_cli(
                "phase-start", "--dir", str(run_dir), "--phase", "system-design",
                "--allow-parallel",
            )

    def test_gate_review_blocked_without_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(tmp)
            self.run_cli("phase-start", "--dir", str(run_dir), "--phase", "requirements-analysis")
            self.run_cli(
                "gate-review", "--dir", str(run_dir), "--phase", "requirements-analysis",
                "--approved-by", "tester",
                expect_success=False,
            )

    def test_full_happy_path_two_phases_and_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(tmp)

            # Phase 1: requirements-analysis
            self.run_cli("phase-start", "--dir", str(run_dir), "--phase", "requirements-analysis")
            self.run_cli(
                "add-artifact", "--dir", str(run_dir), "--phase", "requirements-analysis",
                "--title", "SRS v1", "--path", "dev/01-requirements/srs.docx", "--type", "docx",
            )
            self.run_cli(
                "add-trace", "--dir", str(run_dir), "--requirement-id", "R001",
                "--title", "System shall do X", "--origin-phase", "requirements-analysis",
            )
            gate1 = self.run_cli(
                "gate-review", "--dir", str(run_dir), "--phase", "requirements-analysis",
                "--approved-by", "tester",
            )
            gate1_data = json.loads(gate1.stdout)
            self.assertTrue(gate1_data["gate_approved"])
            # requirements-analysis is verification-side, so the "does this
            # phase have linked traceability" check (which only applies to
            # validation-side phases checking their own test_phase) doesn't
            # fire here — no warnings expected on this gate.
            self.assertEqual(gate1_data["warnings"], [])

            state = json.loads((run_dir / "sdlc_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["current_phase"], "system-design")
            self.assertEqual(state["phases"][0]["status"], "complete")

            # Phase 2: system-design now unblocked
            self.run_cli("phase-start", "--dir", str(run_dir), "--phase", "system-design")
            self.run_cli(
                "add-artifact", "--dir", str(run_dir), "--phase", "system-design",
                "--title", "SDD v1", "--path", "dev/02-system-design/sdd.docx", "--type", "docx",
            )
            self.run_cli(
                "gate-review", "--dir", str(run_dir), "--phase", "system-design",
                "--approved-by", "tester",
            )

            status = self.run_cli("status", "--dir", str(run_dir))
            status_data = json.loads(status.stdout)
            self.assertEqual(status_data["total_artifacts"], 2)
            self.assertEqual(status_data["current_phase"], "architecture-design")

    def test_gate_review_warns_when_validation_phase_has_no_trace_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(tmp)
            self.run_cli(
                "phase-start", "--dir", str(run_dir), "--phase", "unit-testing",
                "--allow-parallel",
            )
            self.run_cli(
                "add-artifact", "--dir", str(run_dir), "--phase", "unit-testing",
                "--title", "Unit test plan", "--path", "utp.xlsx", "--type", "xlsx",
            )
            gate = self.run_cli(
                "gate-review", "--dir", str(run_dir), "--phase", "unit-testing",
                "--approved-by", "tester",
            )
            data = json.loads(gate.stdout)
            self.assertTrue(any("no traceability.csv rows" in w for w in data["warnings"]))

    def test_lint_flags_missing_acceptance_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(tmp)
            self.run_cli("phase-start", "--dir", str(run_dir), "--phase", "requirements-analysis")
            self.run_cli(
                "add-artifact", "--dir", str(run_dir), "--phase", "requirements-analysis",
                "--title", "SRS", "--path", "srs.docx", "--type", "docx",
            )
            self.run_cli(
                "add-trace", "--dir", str(run_dir), "--requirement-id", "R001",
                "--origin-phase", "requirements-analysis",
            )
            self.run_cli(
                "gate-review", "--dir", str(run_dir), "--phase", "requirements-analysis",
                "--approved-by", "tester",
            )
            lint = self.run_cli("lint", "--dir", str(run_dir))
            data = json.loads(lint.stdout)
            self.assertTrue(any("acceptance-testing" in w for w in data["warnings"]))

    def test_report_md_contains_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(tmp)
            self.run_cli("phase-start", "--dir", str(run_dir), "--phase", "requirements-analysis")
            self.run_cli(
                "add-trace", "--dir", str(run_dir), "--requirement-id", "R001",
                "--title", "Widget must spin", "--origin-phase", "requirements-analysis",
            )
            report = self.run_cli("report", "--dir", str(run_dir))
            self.assertIn("R001", report.stdout)
            self.assertIn("Widget must spin", report.stdout)

    def test_yaml_mirror_is_regenerated_and_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(tmp)
            yaml_text = (run_dir / "sdlc_state.yaml").read_text(encoding="utf-8")
            self.assertIn("project:", yaml_text)
            self.assertIn("requirements-analysis", yaml_text)


if __name__ == "__main__":
    unittest.main()
