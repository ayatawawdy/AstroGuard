
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import sys
import unittest
import pandas as pd

from clean_data import clean_data
from validate_data import main, validate_data

BASE_DIR = Path(__file__).resolve().parent
SOURCE_FILE = BASE_DIR / "synthetic" / "health_measurements.csv"


class TestDataPipeline(unittest.TestCase):

    def test_corrupted_data(self):
        original_bytes = SOURCE_FILE.read_bytes()
        original_df = pd.read_csv(SOURCE_FILE)

        # Use one valid measurement as the template
        base_record = original_df.iloc[0].to_dict()

        def make_record(measurement_id, **changes):
            record = base_record.copy()
            record["measurement_id"] = measurement_id
            record.update(changes)
            return record

        # Create five intentionally invalid records
        invalid_records = [
            make_record("TEST-MISSING", value=None),

            make_record(
                original_df.iloc[0]["measurement_id"]
            ),

            make_record("TEST-UNIT", unit="mmHg"),

            make_record(
                "TEST-DATE",
                timestamp_utc="invalid-date"
            ),

            make_record(
                "TEST-VALUE",
                value="not-a-number"
            ),
        ]

        test_df = pd.concat(
            [original_df, pd.DataFrame(invalid_records)],
            ignore_index=True
        )

        # Temporary files prevent changes to the real dataset
        with TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            input_file = temp_dir / "corrupted_data.csv"
            output_dir = temp_dir / "results"

            test_df.to_csv(input_file, index=False)

            clean_df, rejected_df = clean_data(
                input_file=input_file,
                output_dir=output_dir
            )

            # Verify record counts
            self.assertEqual(len(test_df), 725)
            self.assertEqual(len(clean_df), 720)
            self.assertEqual(len(rejected_df), 5)

            # Verify no duplicated IDs remain
            self.assertTrue(
                clean_df["measurement_id"].is_unique
            )

            # Verify the expected rejection reasons
            reasons = rejected_df["rejection_reason"]

            self.assertEqual(
                reasons.str.contains(
                    "Invalid numeric value"
                ).sum(),
                2
            )

            for expected in [
                "Duplicate measurement ID",
                "Invalid unit",
                "Invalid or future timestamp",
            ]:
                self.assertTrue(
                    reasons.str.contains(
                        expected, regex=False
                    ).any()
                )

            # Verify output files were generated
            self.assertTrue(
                (output_dir / "health_measurements_clean.csv").exists()
            )
            self.assertTrue(
                (output_dir / "rejected_records.csv").exists()
            )

        # Verify original dataset was not modified
        self.assertEqual(
            SOURCE_FILE.read_bytes(),
            original_bytes
        )


class TestValidationExitStatus(unittest.TestCase):

    def run_validator(self, *args):
        return subprocess.run(
            [sys.executable, str(BASE_DIR / "validate_data.py"), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_valid_dataset_exits_zero(self):
        self.assertEqual(validate_data(), [])
        completed = self.run_validator()
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("VALIDATION PASSED", completed.stdout)

    def test_invalid_dataset_exits_nonzero(self):
        df = pd.read_csv(SOURCE_FILE)
        df.loc[0, "unit"] = "mmHg"
        df.loc[1, "measurement_id"] = df.loc[0, "measurement_id"]

        with TemporaryDirectory() as temp:
            bad_file = Path(temp) / "invalid.csv"
            df.to_csv(bad_file, index=False)

            errors = validate_data(bad_file)
            self.assertIn("Invalid measurement units", errors)
            self.assertIn("Duplicate measurement IDs", errors)
            self.assertEqual(main([str(bad_file)]), 1)

            completed = self.run_validator(str(bad_file))

        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("VALIDATION FAILED", completed.stdout)

    def test_missing_columns_exits_nonzero(self):
        df = pd.read_csv(SOURCE_FILE).drop(columns=["unit", "astronaut_id"])

        with TemporaryDirectory() as temp:
            bad_file = Path(temp) / "missing_columns.csv"
            df.to_csv(bad_file, index=False)
            completed = self.run_validator(str(bad_file))

        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("Missing columns", completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
