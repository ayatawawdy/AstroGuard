
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import closing
from unittest.mock import patch

from backend import services
from backend.database import get_connection, initialize_database


class TestBackendServices(unittest.TestCase):

    def setUp(self):
        # Create an isolated temporary database
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        self.db_path = Path(temp.name) / "test.db"
        initialize_database(self.db_path)

        # Create a fictional astronaut and mission
        with closing(get_connection(self.db_path)) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO astronauts (astronaut_id) VALUES (?)",
                    ("AST-TEST",)
                )
                conn.execute(
                    "INSERT INTO missions (mission_id) VALUES (?)",
                    ("MISSION-TEST",)
                )

        # Redirect services to the temporary database
        patcher = patch.object(
            services,
            "get_connection",
            side_effect=lambda: get_connection(self.db_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_test_measurement(self):
        return services.add_measurement(
            astronaut_id="AST-TEST",
            mission_id="MISSION-TEST",
            metric_type="heart_rate",
            value=74,
            source="synthetic",
            context="Automated test"
        )

    def test_create_and_read(self):
        measurement_id = self.add_test_measurement()

        rows = services.get_measurements(
            "AST-TEST", "heart_rate"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["measurement_id"], measurement_id
        )
        self.assertEqual(rows[0]["value"], 74.0)
        self.assertEqual(rows[0]["is_synthetic"], 1)

    def test_update(self):
        self.add_test_measurement()

        measurement_id = services.get_measurements(
            "AST-TEST", "heart_rate"
        )[0]["measurement_id"]

        result = services.update_measurement(
            measurement_id, 75
        )

        self.assertTrue(result)

        rows = services.get_measurements(
            "AST-TEST", "heart_rate"
        )

        self.assertEqual(rows[0]["value"], 75.0)

    def test_delete(self):
        measurement_id = self.add_test_measurement()

        result = services.delete_measurement(
            measurement_id
        )

        self.assertTrue(result)
        self.assertEqual(
            services.get_measurements("AST-TEST"), []
        )

        # Deleting again should return False
        self.assertFalse(
            services.delete_measurement(measurement_id)
        )

    def test_invalid_measurement(self):
        with self.assertRaises(ValueError):
            services.add_measurement(
                "AST-TEST",
                "MISSION-TEST",
                "mood",
                7,
                source="synthetic"
            )

    def test_unknown_astronaut(self):
        with self.assertRaises(ValueError):
            services.add_measurement(
                "UNKNOWN",
                "MISSION-TEST",
                "heart_rate",
                74,
                source="synthetic"
            )

    def test_database_persistence(self):
        measurement_id = self.add_test_measurement()

        # Open a new connection to verify persistence
        with closing(get_connection(self.db_path)) as conn:
            result = conn.execute(
                """
                SELECT value
                FROM measurements
                WHERE measurement_id = ?
                """,
                (measurement_id,)
            ).fetchone()

        self.assertIsNotNone(result)
        self.assertEqual(result["value"], 74.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
