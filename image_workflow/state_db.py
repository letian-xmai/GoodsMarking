from __future__ import annotations

from pathlib import Path
import sqlite3


PROGRESS_FIELDS = [
    "outward_code",
    "assignee",
    "status",
    "total_urls",
    "downloaded_count",
    "selected_count",
    "failed_count",
    "needs_review",
    "updated_at",
    "notes",
]


class StateDb:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read_progress(self) -> list[dict[str, str]]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT outward_code, assignee, status, total_urls, downloaded_count,
                       selected_count, failed_count, needs_review, updated_at, notes
                FROM product_progress
                ORDER BY outward_code
                """
            ).fetchall()
        return [{field: "" if row[field] is None else str(row[field]) for field in PROGRESS_FIELDS} for row in rows]

    def replace_progress(self, rows: list[dict[str, str]]) -> None:
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("DELETE FROM product_progress")
            for row in rows:
                self._upsert_progress(conn, row)

    def upsert_progress(self, row: dict[str, str]) -> None:
        with self._connect() as conn:
            self._ensure_schema(conn)
            self._upsert_progress(conn, row)

    def read_review_statuses(self, target_keys: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
        if not target_keys:
            return {}
        with self._connect() as conn:
            self._ensure_schema(conn)
            statuses: dict[tuple[str, str], str] = {}
            for outward_code, image_url in target_keys:
                row = conn.execute(
                    """
                    SELECT manual_status FROM image_review_status
                    WHERE outward_code = ? AND image_url = ?
                    """,
                    (outward_code, image_url),
                ).fetchone()
                if row and row["manual_status"]:
                    statuses[(outward_code, image_url)] = str(row["manual_status"])
        return statuses

    def upsert_review_statuses(self, updates: dict[tuple[str, str], str]) -> None:
        if not updates:
            return
        with self._connect() as conn:
            self._ensure_schema(conn)
            for (outward_code, image_url), status in updates.items():
                clean_status = str(status).strip()
                if clean_status:
                    conn.execute(
                        """
                        INSERT INTO image_review_status(outward_code, image_url, manual_status)
                        VALUES (?, ?, ?)
                        ON CONFLICT(outward_code, image_url)
                        DO UPDATE SET manual_status = excluded.manual_status, updated_at = CURRENT_TIMESTAMP
                        """,
                        (outward_code, image_url, clean_status),
                    )
                else:
                    conn.execute(
                        "DELETE FROM image_review_status WHERE outward_code = ? AND image_url = ?",
                        (outward_code, image_url),
                    )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_progress (
                outward_code TEXT PRIMARY KEY,
                assignee TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                total_urls INTEGER NOT NULL DEFAULT 0,
                downloaded_count INTEGER NOT NULL DEFAULT 0,
                selected_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                needs_review TEXT NOT NULL DEFAULT 'no',
                updated_at TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS image_review_status (
                outward_code TEXT NOT NULL,
                image_url TEXT NOT NULL,
                manual_status TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(outward_code, image_url)
            )
            """
        )

    def _upsert_progress(self, conn: sqlite3.Connection, row: dict[str, str]) -> None:
        values = {field: str(row.get(field, "")) for field in PROGRESS_FIELDS}
        conn.execute(
            """
            INSERT INTO product_progress(
                outward_code, assignee, status, total_urls, downloaded_count,
                selected_count, failed_count, needs_review, updated_at, notes
            )
            VALUES (
                :outward_code, :assignee, :status, :total_urls, :downloaded_count,
                :selected_count, :failed_count, :needs_review, :updated_at, :notes
            )
            ON CONFLICT(outward_code) DO UPDATE SET
                assignee = excluded.assignee,
                status = excluded.status,
                total_urls = excluded.total_urls,
                downloaded_count = excluded.downloaded_count,
                selected_count = excluded.selected_count,
                failed_count = excluded.failed_count,
                needs_review = excluded.needs_review,
                updated_at = excluded.updated_at,
                notes = excluded.notes
            """,
            values,
        )
