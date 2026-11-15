"""Startup regression tests for the Streamlit dashboard (app.py).

The end-to-end test runs the app against a temporary copy of the repository, so
the real backend/astroguard.db and data/processed/ outputs are never touched.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import streamlit as st
from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_FILE = REPO_ROOT / "app.py"
RESULT_PREFIX = "STARTUP_RESULT="

# Executed in a subprocess whose working directory is the temporary repo copy.
E2E_SCRIPT = f"""
import json
import streamlit as st
from streamlit.testing.v1 import AppTest

def summary(at):
    return {{
        "exceptions": [e.message for e in at.exception],
        "errors": [e.value for e in at.error],
        "selectboxes": [s.label for s in at.selectbox],
        "headers": [h.value for h in at.header],
        "charts": len(at.get("plotly_chart")),
        "astronaut_options": list(at.selectbox[0].options) if at.selectbox else [],
        "status_banners": sum("Suggested action" in h.proto.body for h in at.get("html")),
        "selected": at.selectbox[0].value if at.selectbox else None,
    }}

first = AppTest.from_file("app.py", default_timeout=120).run()
result = {{"first": summary(first)}}
if first.selectbox:
    options = first.selectbox[0].options
    first.selectbox[0].select(options[-1]).run()
    result["rerun"] = summary(first)

# A new session on an already-populated database must not re-import or fail.
st.cache_resource.clear()
second = AppTest.from_file("app.py", default_timeout=120).run()
result["second_start"] = summary(second)
print({RESULT_PREFIX!r} + json.dumps(result))
"""


def copy_repository(destination):
    ignore = shutil.ignore_patterns("__pycache__", "*.db", "*.db-*", "processed", "external")
    shutil.copy2(APP_FILE, destination / "app.py")
    for package in ("ai", "backend", "data", "components"):
        shutil.copytree(REPO_ROOT / package, destination / package, ignore=ignore)


class FreshCheckoutStartupTests(unittest.TestCase):
    """The dashboard must start on a checkout that has no database yet."""

    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory()
        cls.workdir = Path(cls._temp.name)
        copy_repository(cls.workdir)
        assert not (cls.workdir / "backend" / "astroguard.db").exists()

        env = {k: v for k, v in os.environ.items() if k != "ASTROGUARD_DB_PATH"}
        completed = subprocess.run(
            [sys.executable, "-c", E2E_SCRIPT],
            cwd=cls.workdir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        cls.output = completed.stdout + completed.stderr
        lines = [l for l in completed.stdout.splitlines() if l.startswith(RESULT_PREFIX)]
        cls.result = json.loads(lines[-1][len(RESULT_PREFIX):]) if lines else None

    @classmethod
    def tearDownClass(cls):
        cls._temp.cleanup()

    def setUp(self):
        self.assertIsNotNone(self.result, f"App test harness failed:\n{self.output}")

    # Stage 1B: app.py became the Mission Control dashboard (requested redesign). The
    # original single-page assertions (exactly two selectboxes, two headers, two charts)
    # described the old layout; the same invariants are now checked for the new layout,
    # and the original legacy health page is checked when a legacy astronaut is selected.
    def assert_dashboard_rendered(self, run, *, legacy=False):
        self.assertEqual(run["exceptions"], [])
        self.assertEqual(run["errors"], [])
        self.assertEqual(run["selectboxes"][:3],
                         ["Select astronaut", "Select mission", "Mission task context"])
        self.assertEqual(run["status_banners"], 1)
        self.assertGreaterEqual(run["charts"], 1)
        if legacy:
            self.assertEqual(run["headers"], ["Health Trends", "Health Intelligence & Alerts"])
            self.assertGreaterEqual(run["charts"], 2)

    def test_first_start_prepares_database_and_renders(self):
        self.assert_dashboard_rendered(self.result["first"])
        options = self.result["first"]["astronaut_options"]
        # Options are display labels; each contains the astronaut ID.
        self.assertEqual(len(options), 8)
        for label, astronaut in zip(options, ["AST-S01", "AST-S02", "AST-S03", "AST-S04"]):
            self.assertIn(astronaut, label)
            self.assertIn("fictional", label)
        self.assertIn("LIVE-USER-01", options[4])        # separate interactive test profile
        self.assertEqual(sorted(options[5:]),
                         [f"AST-00{i} — legacy health demo" for i in (1, 2, 3)])
        self.assertEqual(self.result["first"]["selected"], "AST-S01")

    def test_database_populated_from_demo_data(self):
        db_file = self.workdir / "backend" / "astroguard.db"
        self.assertTrue(db_file.exists())
        connection = sqlite3.connect(db_file)
        try:
            count = connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 720)

    def test_rerun_after_selecting_another_astronaut(self):
        # The last option is a legacy astronaut, so the original health page must render.
        self.assertEqual(self.result["rerun"]["selected"], "AST-003")
        self.assert_dashboard_rendered(self.result["rerun"], legacy=True)

    def test_second_start_on_existing_database(self):
        self.assert_dashboard_rendered(self.result["second_start"])
        self.assertIn("Database already ready: 720 measurements.", self.output)


class StartupErrorHandlingTests(unittest.TestCase):
    """Database failures must show a dashboard error, never an uncaught exception."""

    def setUp(self):
        st.cache_resource.clear()
        self.addCleanup(st.cache_resource.clear)

    def run_app(self):
        return AppTest.from_file(str(APP_FILE), default_timeout=60).run()

    def test_prepare_database_failure_is_reported(self):
        failure = sqlite3.OperationalError("disk I/O error")
        with mock.patch("backend.bootstrap.prepare_database", side_effect=failure):
            at = self.run_app()
        self.assertEqual([e.message for e in at.exception], [])
        self.assertEqual(len(at.error), 1)
        self.assertIn("Could not prepare the mission database", at.error[0].value)
        self.assertEqual(len(at.selectbox), 0)

    def test_missing_dataset_is_reported(self):
        failure = FileNotFoundError("Dataset not found")
        with mock.patch("backend.bootstrap.prepare_database", side_effect=failure):
            at = self.run_app()
        self.assertEqual([e.message for e in at.exception], [])
        self.assertIn("Dataset not found", at.error[0].value)

    def test_astronaut_query_database_error_is_reported(self):
        # Regression: DatabaseError was referenced in app.py without being imported.
        failure = sqlite3.OperationalError("no such table: astronauts")
        with mock.patch("backend.bootstrap.prepare_database"), \
                mock.patch("backend.frontend_service.get_astronauts", side_effect=failure):
            at = self.run_app()
        self.assertEqual([e.message for e in at.exception], [])
        self.assertEqual(len(at.error), 1)
        self.assertIn("Could not load astronauts", at.error[0].value)

    def test_database_prepared_before_first_query(self):
        calls = []
        with mock.patch("backend.bootstrap.prepare_database",
                        side_effect=lambda: calls.append("prepare")), \
                mock.patch("backend.frontend_service.get_astronauts",
                           side_effect=lambda: calls.append("query") or []):
            at = self.run_app()
        self.assertEqual(calls, ["prepare", "query"])
        self.assertEqual([e.message for e in at.exception], [])


if __name__ == "__main__":
    unittest.main()
