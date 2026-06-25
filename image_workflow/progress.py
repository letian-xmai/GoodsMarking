from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .state_db import StateDb


FIELDS = [
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


class ProgressTable:
    def __init__(self, path: str | Path):
        self.state_db = StateDb(path)

    def read_all(self) -> list[dict[str, str]]:
        return self.state_db.read_progress()

    def upsert(
        self,
        *,
        outward_code: str,
        assignee: str = "codex",
        status: str,
        total_urls: int = 0,
        downloaded_count: int = 0,
        selected_count: int = 0,
        failed_count: int = 0,
        needs_review: bool = False,
        notes: str = "",
    ) -> None:
        rows = self.read_all()
        next_row = {
            "outward_code": str(outward_code),
            "assignee": assignee,
            "status": status,
            "total_urls": str(total_urls),
            "downloaded_count": str(downloaded_count),
            "selected_count": str(selected_count),
            "failed_count": str(failed_count),
            "needs_review": "yes" if needs_review else "no",
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "notes": notes,
        }
        replaced = False
        for index, row in enumerate(rows):
            if row.get("outward_code") == str(outward_code):
                rows[index] = next_row
                replaced = True
                break
        if not replaced:
            rows.append(next_row)
        self.state_db.upsert_progress(next_row)

    def initialize_pending(self, group_counts: dict[str, int], assignee: str = "codex") -> None:
        rows = []
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for outward_code, total_urls in sorted(group_counts.items()):
            rows.append({
                "outward_code": str(outward_code),
                "assignee": assignee,
                "status": "pending",
                "total_urls": str(total_urls),
                "downloaded_count": "0",
                "selected_count": "0",
                "failed_count": "0",
                "needs_review": "no",
                "updated_at": now,
                "notes": "",
            })
        self.state_db.replace_progress(rows)
