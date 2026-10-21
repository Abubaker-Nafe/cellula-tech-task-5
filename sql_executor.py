import json
import math
import sqlite3
from contextlib import closing
from time import monotonic

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify

from dataset_store import get_dataset_info, get_dataset_path

MAX_SQL_CHARS = 20_000
MAX_RESULT_ROWS = 100
MAX_RESULT_BYTES = 1_000_000
QUERY_SECONDS = 5.0

SAFE_FUNCTIONS = set("""
abs avg coalesce count date datetime group_concat ifnull instr julianday
length like glob lower ltrim max min nullif replace round rtrim strftime
substr substring sum time total trim typeof upper current_date current_time
current_timestamp row_number rank dense_rank lag lead first_value last_value
nth_value
""".split())


class SQLExecutionError(ValueError):
    """The SQL failed validation or execution."""


def validate_sql(sql: str, columns: list[dict]) -> str:
    sql = sql.strip()
    if not sql or len(sql) > MAX_SQL_CHARS:
        raise SQLExecutionError("SQL must contain 1 to 20,000 characters.")

    try:
        statements = [item for item in sqlglot.parse(sql, read="sqlite") if item]
        if len(statements) != 1 or not isinstance(
            statements[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)
        ):
            raise SQLExecutionError("Only one SELECT query is allowed.")

        schema = {column["name"]: column["type"] for column in columns}
        qualify(
            statements[0].copy(),
            dialect="sqlite",
            schema={"dataset": schema},
            validate_qualify_columns=True,
        )
    except (SqlglotError, RecursionError) as exc:
        raise SQLExecutionError(f"Invalid SQL or column reference: {exc}") from exc

    return sql


def _json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return "hex:" + value.hex()
    return value


def execute_sql(dataset_id: str, sql: str) -> dict:
    info = get_dataset_info(dataset_id)
    sql = validate_sql(sql, info["columns"])
    database_uri = get_dataset_path(dataset_id).as_uri() + "?mode=ro"
    rows = []
    truncated = False
    result_bytes = 2
    reads_dataset = False

    def authorize(action, arg1, arg2, database, source):
        nonlocal reads_dataset
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if database in ("main", None) and (arg1 or "").lower() == "dataset":
                reads_dataset = True
                return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            if (arg2 or arg1 or "").lower() in SAFE_FUNCTIONS:
                return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    try:
        with closing(sqlite3.connect(database_uri, uri=True, timeout=1.0)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
            connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            connection.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100)
            connection.set_authorizer(authorize)

            deadline = monotonic() + QUERY_SECONDS
            connection.set_progress_handler(lambda: monotonic() >= deadline, 1000)

            # Compile and authorize the query before executing it.
            connection.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
            if not reads_dataset:
                raise SQLExecutionError("The query must read the uploaded dataset.")

            cursor = connection.execute(sql)
            names = [column[0] for column in cursor.description]
            if len(names) != len(set(names)):
                raise SQLExecutionError("Use unique aliases for output columns.")

            for record in cursor:
                row = {name: _json_value(value) for name, value in zip(names, record)}
                size = len(json.dumps(row, ensure_ascii=False).encode("utf-8")) + 1
                if len(rows) >= MAX_RESULT_ROWS or result_bytes + size > MAX_RESULT_BYTES:
                    if not rows:
                        raise SQLExecutionError("A result row exceeds the response size limit.")
                    truncated = True
                    break
                rows.append(row)
                result_bytes += size
    except sqlite3.Error as exc:
        if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT:
            raise TimeoutError("The SQL query exceeded its 5-second time budget.") from exc
        raise SQLExecutionError(f"SQL rejected or failed: {exc}") from exc

    return {
        "status": "executed",
        "dataset_id": dataset_id,
        "sql": sql,
        "columns": names,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }
