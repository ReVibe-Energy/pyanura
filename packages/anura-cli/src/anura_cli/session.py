import logging
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Iterator

import anura.avss as avss
from anura.avss.client import Report

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

CREATE_SCHEMA = """
CREATE TABLE vibreshark_schema (
    version INTEGER
);
CREATE TABLE session_info (
    created_at INTEGER
);
CREATE TABLE avss_report (
    received_at INTEGER,
    node_id TEXT,
    report_type INTEGER,
    payload_cbor BLOB
);
"""


class SessionFile:
    def __init__(self, path, read_only=True):
        self._path = path
        self._conn: sqlite3.Connection = None
        self._read_only = read_only

    def open(self):
        if self._conn:
            raise RuntimeError("File already open")

        abs_path = Path(self._path).absolute()

        # Use file URI, allowing us to pass options by appending
        # query parameters.
        if sys.platform == "win32":
            file_uri = f"file:///{abs_path}"
        else:
            file_uri = f"file:{abs_path}"

        if self._read_only:
            file_uri += "?mode=ro"

        file_exists = abs_path.exists()
        self._conn = sqlite3.connect(file_uri, uri=True)

        if file_exists:
            self._check_version()
        else:
            self._initialize_schema()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _check_version(self):
        with closing(self._conn.cursor()) as cur:
            cur.row_factory = sqlite3.Row
            cur.execute("SELECT version FROM vibreshark_schema")

            row = cur.fetchone()

            if not row:
                raise RuntimeError("Unrecognized file format")
            elif row["version"] != SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported file version: {row.version}")

    def _initialize_schema(self):
        logger.debug("Initializing schema")
        with closing(self._conn.cursor()) as cur:
            cur.executescript(CREATE_SCHEMA)
            cur.execute(
                "INSERT INTO vibreshark_schema (version) VALUES (?)", [SCHEMA_VERSION]
            )
            self._conn.commit()

    def insert_avss_report(
        self, received_at, node_id, report_type, payload_cbor
    ) -> None:
        self._conn.execute(
            "INSERT INTO avss_report (received_at, node_id, report_type, payload_cbor)"
            " VALUES (?, ?, ?, ?)",
            [received_at, node_id, report_type, payload_cbor],
        )
        self._conn.commit()

    def update_session_info(self, created_at: int) -> None:
        with closing(self._conn.cursor()) as cur:
            cur.execute("DELETE FROM session_info")
            cur.execute(
                "INSERT INTO session_info (created_at) VALUES (?)", [created_at]
            )
            self._conn.commit()
