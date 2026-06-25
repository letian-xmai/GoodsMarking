from __future__ import annotations

from pathlib import Path
from typing import Iterable
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

    def upsert_product_images(self, rows: Iterable[dict[str, str]]) -> None:
        with self._connect() as conn:
            self._ensure_schema(conn)
            for row in rows:
                outward_code = str(row.get("outward_code", "")).strip()
                image_url = str(row.get("image_url", "")).strip()
                if not outward_code or not image_url:
                    continue
                source = str(row.get("source", "")).strip()
                row_number = _int_value(row.get("row_number", "0"))
                download_status = str(row.get("download_status", "")).strip()
                model_status = str(row.get("model_status", "")).strip()
                conn.execute(
                    """
                    INSERT INTO product_images(outward_code, image_url, source, row_number, is_standard, download_status, model_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(outward_code, image_url) DO UPDATE SET
                        source = excluded.source,
                        row_number = excluded.row_number,
                        is_standard = excluded.is_standard,
                        download_status = CASE WHEN excluded.download_status != '' THEN excluded.download_status ELSE product_images.download_status END,
                        model_status = CASE WHEN excluded.model_status != '' THEN excluded.model_status ELSE product_images.model_status END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (outward_code, image_url, source, row_number, 1 if _is_standard_image(image_url, source) else 0, download_status, model_status),
                )

    def update_product_image_statuses(self, rows: Iterable[dict[str, str]]) -> int:
        updated = 0
        with self._connect() as conn:
            self._ensure_schema(conn)
            for row in rows:
                outward_code = str(row.get("outward_code", "")).strip()
                image_url = str(row.get("image_url", "")).strip()
                if not outward_code or not image_url:
                    continue
                download_status = str(row.get("download_status", "")).strip()
                model_status = str(row.get("model_status", "")).strip()
                result = conn.execute(
                    """
                    UPDATE product_images
                    SET download_status = CASE WHEN ? != '' THEN ? ELSE download_status END,
                        model_status = CASE WHEN ? != '' THEN ? ELSE model_status END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE outward_code = ? AND image_url = ?
                    """,
                    (download_status, download_status, model_status, model_status, outward_code, image_url),
                )
                updated += result.rowcount
        return updated

    def first_standard_image_urls(self, outward_codes: set[str]) -> dict[str, str]:
        params = sorted(outward_codes)
        where = ""
        if 0 < len(params) <= 900:
            placeholders = ",".join("?" for _ in params)
            where = f"AND outward_code IN ({placeholders})"
        else:
            params = []
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT p.outward_code, MIN(p.image_url) AS image_url
                FROM product_images p
                JOIN (
                    SELECT outward_code, MIN(row_number) AS first_row
                    FROM product_images
                    WHERE is_standard = 1 {where}
                    GROUP BY outward_code
                ) first
                  ON p.outward_code = first.outward_code
                 AND p.row_number = first.first_row
                WHERE p.is_standard = 1
                GROUP BY p.outward_code
                ORDER BY p.outward_code
                """,
                params,
            ).fetchall()
        urls: dict[str, str] = {}
        for row in rows:
            urls.setdefault(str(row["outward_code"]), str(row["image_url"]))
        return urls

    def product_image_summary(self) -> dict[str, object]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            image_rows = conn.execute(
                """
                SELECT outward_code,
                       COUNT(*) AS total_count,
                       SUM(CASE WHEN is_standard = 1 THEN 1 ELSE 0 END) AS standard_count
                FROM product_images
                GROUP BY outward_code
                """
            ).fetchall()
            progress_rows = conn.execute("SELECT outward_code FROM product_progress").fetchall()
        product_codes = {str(row["outward_code"]) for row in progress_rows}
        standard_counts: dict[str, int] = {}
        cutout_counts: dict[str, int] = {}
        all_standard_product_codes: set[str] = set()
        for row in image_rows:
            code = str(row["outward_code"])
            total_count = int(row["total_count"] or 0)
            standard_count = int(row["standard_count"] or 0)
            product_codes.add(code)
            standard_counts[code] = standard_count
            cutout_counts[code] = max(0, total_count - standard_count)
            if total_count > 0 and standard_count == total_count:
                all_standard_product_codes.add(code)
        for code in product_codes:
            standard_counts.setdefault(code, 0)
            cutout_counts.setdefault(code, 0)
        return {
            "product_codes": product_codes,
            "all_standard_product_codes": all_standard_product_codes,
            "standard_counts": standard_counts,
            "cutout_counts": cutout_counts,
        }

    def product_summary_rows(self, query: str = "", limit: int = 50, offset: int = 0) -> list[dict[str, object]]:
        where_sql = "WHERE p.outward_code LIKE ?" if query else ""
        where_params: list[object] = [f"%{query}%"] if query else []
        params = [*where_params, max(1, limit), max(0, offset)]
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT p.outward_code,
                       SUM(CASE WHEN p.is_standard = 1 THEN 1 ELSE 0 END) AS standard_count,
                       SUM(CASE WHEN p.is_standard = 1 THEN 0 ELSE 1 END) AS cutout_count,
                       COALESCE(MAX(CAST(g.selected_count AS INTEGER)), 0) AS final_count,
                       COALESCE(MAX(q.qualified_count), 0) AS manual_count,
                       COALESCE(MAX(g.status), '') AS status,
                       (
                         SELECT image_url
                         FROM product_images s
                         WHERE s.outward_code = p.outward_code AND s.is_standard = 1
                         ORDER BY s.row_number, s.image_url
                         LIMIT 1
                       ) AS standard_image_url
                FROM product_images p
                LEFT JOIN product_progress g ON p.outward_code = g.outward_code
                LEFT JOIN (
                    SELECT outward_code, COUNT(*) AS qualified_count
                    FROM image_review_status
                    WHERE manual_status = '合格'
                    GROUP BY outward_code
                ) q ON p.outward_code = q.outward_code
                {where_sql}
                GROUP BY p.outward_code
                ORDER BY p.outward_code
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def count_product_summary_rows(self, query: str = "") -> int:
        params: list[object] = []
        where_sql = ""
        if query:
            where_sql = "WHERE outward_code LIKE ?"
            params.append(f"%{query}%")
        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                f"SELECT COUNT(DISTINCT outward_code) AS count FROM product_images {where_sql}",
                params,
            ).fetchone()
        return int(row["count"] or 0) if row else 0

    def workflow_metrics(self) -> dict[str, int]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            total = conn.execute("SELECT COUNT(DISTINCT outward_code) AS count FROM product_images").fetchone()
            completed = conn.execute(
                "SELECT COUNT(*) AS count FROM product_progress WHERE status = 'complete'"
            ).fetchone()
            pending_annotation = conn.execute(
                "SELECT COUNT(*) AS count FROM product_progress WHERE needs_review = 'yes'"
            ).fetchone()
            all_standard = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM (
                    SELECT outward_code,
                           COUNT(*) AS total_count,
                           SUM(CASE WHEN is_standard = 1 THEN 1 ELSE 0 END) AS standard_count
                    FROM product_images
                    GROUP BY outward_code
                    HAVING total_count > 0 AND total_count = standard_count
                )
                """
            ).fetchone()
        total_products = int(total["count"] or 0)
        completed_products = int(completed["count"] or 0)
        invalid_products = int(all_standard["count"] or 0)
        return {
            "total_products": total_products,
            "completed_products": completed_products,
            "invalid_products": invalid_products,
            "pending_annotation_products": int(pending_annotation["count"] or 0),
            "unfinished_products": max(0, total_products - completed_products - invalid_products),
        }

    def count_product_images(self, outward_code: str = "") -> int:
        with self._connect() as conn:
            self._ensure_schema(conn)
            if outward_code:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM product_images WHERE outward_code = ?",
                    (outward_code,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS count FROM product_images").fetchone()
        return int(row["count"] or 0) if row else 0

    def product_image_rows(self, outward_code: str = "", manual_status: str = "", model_status: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, str]]:
        where = []
        params: list[object] = []
        if outward_code:
            where.append("p.outward_code = ?")
            params.append(outward_code)
        if manual_status:
            where.append("COALESCE(r.manual_status, '') = ?")
            params.append(manual_status)
        if model_status:
            where.append("p.model_status = ?")
            params.append(model_status)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        params.extend([max(1, limit), max(0, offset)])
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT p.outward_code, p.image_url, p.source, p.row_number,
                       p.download_status, p.model_status,
                       COALESCE(r.manual_status, '') AS manual_status
                FROM product_images p
                LEFT JOIN image_review_status r
                  ON p.outward_code = r.outward_code
                 AND p.image_url = r.image_url
                {where_sql}
                ORDER BY p.outward_code, p.row_number, p.image_url
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [{key: "" if row[key] is None else str(row[key]) for key in row.keys()} for row in rows]

    def count_product_image_rows(self, outward_code: str = "", manual_status: str = "", model_status: str = "") -> int:
        where = []
        params: list[object] = []
        if outward_code:
            where.append("p.outward_code = ?")
            params.append(outward_code)
        if manual_status:
            where.append("COALESCE(r.manual_status, '') = ?")
            params.append(manual_status)
        if model_status:
            where.append("p.model_status = ?")
            params.append(model_status)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM product_images p
                LEFT JOIN image_review_status r
                  ON p.outward_code = r.outward_code
                 AND p.image_url = r.image_url
                {where_sql}
                """,
                params,
            ).fetchone()
        return int(row["count"] or 0) if row else 0

    def count_model_status(self, model_status: str, outward_code: str = "") -> int:
        params: list[object] = [model_status]
        where = "WHERE model_status = ?"
        if outward_code:
            where += " AND outward_code = ?"
            params.append(outward_code)
        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM product_images {where}",
                params,
            ).fetchone()
        return int(row["count"] or 0) if row else 0

    def count_review_status(self, manual_status: str, outward_code: str = "") -> int:
        with self._connect() as conn:
            self._ensure_schema(conn)
            if outward_code:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS count FROM image_review_status
                    WHERE manual_status = ? AND outward_code = ?
                    """,
                    (manual_status, outward_code),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM image_review_status WHERE manual_status = ?",
                    (manual_status,),
                ).fetchone()
        return int(row["count"] or 0) if row else 0

    def review_status_counts_by_product(self, manual_status: str) -> dict[str, int]:
        with self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT outward_code, COUNT(*) AS count
                FROM image_review_status
                WHERE manual_status = ?
                GROUP BY outward_code
                """,
                (manual_status,),
            ).fetchall()
        return {str(row["outward_code"]): int(row["count"] or 0) for row in rows}

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
            CREATE TABLE IF NOT EXISTS product_images (
                outward_code TEXT NOT NULL,
                image_url TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                row_number INTEGER NOT NULL DEFAULT 0,
                is_standard INTEGER NOT NULL DEFAULT 0,
                download_status TEXT NOT NULL DEFAULT '',
                model_status TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(outward_code, image_url)
            )
            """
        )
        _ensure_column(conn, "product_images", "download_status", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "product_images", "model_status", "TEXT NOT NULL DEFAULT ''")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_product_images_standard
            ON product_images(is_standard, outward_code, row_number, image_url)
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


def _is_standard_image(image_url: str, source: str) -> bool:
    source_value = source.strip().lower()
    if "standard" in source_value:
        return True
    if "cutout" in source_value:
        return False
    return "standard" in image_url.lower()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _int_value(value: object) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0
