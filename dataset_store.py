import json
import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import re

import pandas as pd

DATASET_DIR = Path(__file__).resolve().parent / "data" / "datasets"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000


def save_csv_dataset(content: bytes) -> dict:
    if not content:
        raise ValueError("The uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("The CSV file must be 10 MB or smaller.")

    try:
        frame = pd.read_csv(BytesIO(content), encoding="utf-8-sig", nrows=MAX_ROWS + 1)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise ValueError(
            "Upload a valid UTF-8 CSV file with a header row."
        ) from exc

    if frame.empty:
        raise ValueError("The CSV must contain at least one data row.")
    if len(frame) > MAX_ROWS:
        raise ValueError(f"The CSV must contain at most {MAX_ROWS:,} rows.")

    frame.columns = [str(column).strip() for column in frame.columns]
    names = [column.casefold() for column in frame.columns]

    if any(not column or "\x00" in column for column in frame.columns):
        raise ValueError(
            "Column names must be nonempty and contain no null characters."
        )
    if len(names) != len(set(names)):
        raise ValueError(
            "Column names must be unique, ignoring letter case."
        )

    dataset_id = uuid4().hex # Creates a unique dataset ID for each upload.
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    database_path = DATASET_DIR / f"{dataset_id}.sqlite3"

    try:
        with closing(sqlite3.connect(database_path)) as connection:
            frame.to_sql("dataset", connection, if_exists="fail", index=False) # Writes the data into a SQLite table named dataset.
            schema = connection.execute('PRAGMA table_info("dataset")').fetchall()
    except Exception:
        database_path.unlink(missing_ok=True)
        raise

    return {
        "status": "uploaded",
        "dataset_id": dataset_id,
        "table_name": "dataset",
        "row_count": len(frame),
        "columns": [
            {"name": row[1], "type": row[2]}
            for row in schema
        ],
        "preview": json.loads(
            frame.head(5).to_json(
                orient="records", force_ascii=False
            )
        ),
    }

# Validates the ID and locates the existing database file.
def get_dataset_path(dataset_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", dataset_id):
        raise ValueError(
            "The dataset ID must contain 32 lowercase hexadecimal characters."
        )

    database_path = DATASET_DIR / f"{dataset_id}.sqlite3"

    if not database_path.is_file():
        raise FileNotFoundError(
            "Dataset not found. Upload a CSV first."
        )

    return database_path

# Reads the column definitions, total row count, and five sample rows.
def get_dataset_info(dataset_id: str) -> dict:
    database_path = get_dataset_path(dataset_id)
    database_uri = database_path.as_uri() + "?mode=ro"

    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        schema = connection.execute(
            'PRAGMA table_info("dataset")'
        ).fetchall()

        row_count = connection.execute(
            'SELECT COUNT(*) FROM "dataset"'
        ).fetchone()[0]

        preview = pd.read_sql_query(
            'SELECT * FROM "dataset" LIMIT 5',
            connection,
        )

    return {
        "status": "ready",
        "dataset_id": dataset_id,
        "table_name": "dataset",
        "row_count": row_count,
        "columns": [
            {"name": row[1], "type": row[2]}
            for row in schema
        ],
        "preview": json.loads(
            preview.to_json(
                orient="records", force_ascii=False
            )
        ),
    }