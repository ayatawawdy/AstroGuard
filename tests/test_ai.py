import json
import unittest

import pandas as pd

from ai import analyze_astronaut_data


def records(start, n, astronaut="SIM-A", **overrides):
    return [{"astronaut_id": astronaut, "date": date.date().isoformat(),
             "sleep_duration": 8.0, "heart_rate": 60.0,
             "activity_minutes": 45.0, "stress_level": 2.0, **overrides}
            for date in pd.date_range(start, periods=n)]


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.history = records("2026-01-01", 7)

    def analyze(self, rows, **options):
        return analyze_astronaut_data(self.history, rows, **options)

    def test_normal_and_json_serialization(self):
        report = self.analyze(records("2026-01-08", 3))
        self.assertEqual([r["alert_level"] for r in report["results"]], ["normal"] * 3)
        json.dumps(report, allow_nan=False)

    def test_deviations_both_directions(self):
        result = self.analyze(records("2026-01-08", 1, sleep_duration=6,
                                      heart_rate=70))["results"][0]
        self.assertEqual(result["alert_level"], "warning")
        self.assertEqual({a["metric"] for a in result["anomalies"]},
                         {"sleep_duration", "heart_rate"})
        self.assertIn("historical mean", result["anomalies"][0]["explanation"])

    def test_persistent_moderate_change(self):
        rows = self.analyze(records("2026-01-08", 3, sleep_duration=7.4))["results"]
        self.assertEqual([r["alert_level"] for r in rows], ["normal", "normal", "warning"])
        self.assertEqual(rows[-1]["anomalies"][0]["kinds"], ["persistent_change"])
        self.assertEqual(rows[-1]["anomalies"][0]["consecutive_days"], 3)

    def test_gap_direction_and_missing_break_persistence(self):
        for middle in (records("2026-01-10", 1, sleep_duration=7.4)[0],
                       records("2026-01-09", 1, sleep_duration=8.6)[0],
                       records("2026-01-09", 1, sleep_duration=None)[0]):
            with self.subTest(middle=middle):
                rows = records("2026-01-08", 1, sleep_duration=7.4) + [middle]
                last_day = "2026-01-10" if middle["date"] == "2026-01-09" else "2026-01-11"
                rows += records(last_day, 1, sleep_duration=7.4)
                self.assertTrue(all(not r["anomalies"] for r in self.analyze(rows)["results"]))

    def test_missing_current_does_not_hide_other_anomaly(self):
        result = self.analyze(records("2026-01-08", 1, sleep_duration=None,
                                      stress_level=4))["results"][0]
        self.assertEqual(result["alert_level"], "warning")
        self.assertEqual(result["data_quality_issues"], ["sleep_duration: missing measurement"])
        result = self.analyze(records("2026-01-08", 1, sleep_duration=None))["results"][0]
        self.assertEqual(result["alert_level"], "insufficient_data")

    def test_insufficient_history_and_missing_history(self):
        self.history[0]["sleep_duration"] = None
        report = self.analyze(records("2026-01-08", 1))
        self.assertNotIn("sleep_duration", report["baselines"]["SIM-A"])
        self.assertEqual(report["results"][0]["alert_level"], "insufficient_data")

    def test_personalized_baselines(self):
        history = self.history + records("2026-01-01", 7, "SIM-B", heart_rate=90)
        rows = records("2026-01-08", 1) + records("2026-01-08", 1, "SIM-B", heart_rate=90)
        report = analyze_astronaut_data(history, rows)
        self.assertTrue(all(r["alert_level"] == "normal" for r in report["results"]))
        self.assertEqual(report["baselines"]["SIM-B"]["heart_rate"]["mean"], 90)

    def test_sample_standard_deviation_and_input_unchanged(self):
        history = pd.DataFrame(self.history)
        history["sleep_duration"] = [7, 8, 9, 7, 8, 9, 8]
        before = history.copy(deep=True)
        report = analyze_astronaut_data(history, records("2026-01-08", 1))
        self.assertAlmostEqual(report["baselines"]["SIM-A"]["sleep_duration"]["std"],
                               history["sleep_duration"].std())
        pd.testing.assert_frame_equal(history, before)

    def test_invalid_data_and_leakage_rejected(self):
        bad_rows = [records("2026-01-07", 1), records("2026-01-08", 1) * 2,
                    records("2026-01-08", 1, stress_level=float("inf")),
                    records("2026-01-08", 1, sleep_duration=-1),
                    records("2026-01-08", 1, date="bad"),
                    records("2026-01-08", 1, astronaut_id=None),
                    records("2026-01-08", 1, stress_level="bad"),
                    [{"astronaut_id": "SIM-A"}]]
        for rows in bad_rows:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.analyze(rows)

    def test_invalid_configuration(self):
        for options in ({"min_history": 1}, {"persistence_days": 2.5},
                        {"scale_floors": {}}, {"deviation_threshold": 1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.analyze(records("2026-01-08", 1), **options)

    def test_simulated_csv(self):
        from pathlib import Path
        data = pd.read_csv(Path(__file__).resolve().parents[1] / "ai/data/simulated_health.csv")
        report = analyze_astronaut_data(data[data.date < "2026-01-08"],
                                        data[data.date >= "2026-01-08"])
        self.assertEqual(len(report["results"]), 8)
        self.assertTrue(any(r["anomalies"] for r in report["results"]))


if __name__ == "__main__":
    unittest.main()
