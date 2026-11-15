
from contextlib import closing

from backend.database import get_connection, initialize_database
from backend.import_data import import_data
from backend.import_sleep_alertness import import_sleep_alertness
from backend.actigraphy import import_synthetic_actigraphy
from backend.decision_support import ensure_live_profile
from data.clean_data import clean_data


def prepare_database() -> None:
    """
    Prepare the local AstroGuard database.

    1. Create the database and tables if needed (additive, repeatable).
    2. If no legacy measurements exist, clean and import the 720-row dataset.
    3. Load the synthetic sleep & alertness scenarios and synthetic actigraphy
       (idempotent; duplicates are skipped).
    """

    initialize_database()

    with closing(get_connection()) as conn:
        measurement_count = conn.execute(
            "SELECT COUNT(*) FROM measurements"
        ).fetchone()[0]

    if measurement_count > 0:
        print(
            f"Database already ready: "
            f"{measurement_count} measurements."
        )
    else:
        print("Database is empty. Preparing demo data...")

        clean_df, rejected_df = clean_data()

        if clean_df.empty:
            raise RuntimeError(
                "No valid measurements available for import."
            )

        import_data()

        print("AstroGuard database setup completed!")

    prepare_sleep_alertness_demo()


def prepare_sleep_alertness_demo() -> None:
    """Load the fictional sleep/alertness scenarios and synthetic actigraphy.

    Idempotent: identical records already stored are skipped, existing rows
    are never overwritten, and nothing is deleted.
    """
    counts = import_sleep_alertness()
    new_rows = sum(c["inserted"] for c in counts.values())
    import_synthetic_actigraphy()
    ensure_live_profile()   # empty profile for interactive entries; no data added
    print(f"Sleep & alertness demo data ready ({new_rows} new synthetic rows).")


if __name__ == "__main__":
    prepare_database()
