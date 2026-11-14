
from contextlib import closing

from backend.database import get_connection, initialize_database
from backend.import_data import import_data
from data.clean_data import clean_data


def prepare_database() -> None:
    """
    Prepare the local AstroGuard database.

    1. Create the database and tables if needed.
    2. Check whether measurements already exist.
    3. If empty, clean and import the demo dataset.
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
        return

    print("Database is empty. Preparing demo data...")

    clean_df, rejected_df = clean_data()

    if clean_df.empty:
        raise RuntimeError(
            "No valid measurements available for import."
        )

    import_data()

    print("AstroGuard database setup completed!")


if __name__ == "__main__":
    prepare_database()
