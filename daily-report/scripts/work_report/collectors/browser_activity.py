from __future__ import annotations

from contextlib import suppress
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from work_report.models import DateWindow, SourceRecord


SHANGHAI_TZ = timezone(timedelta(hours=8))
CHROME_EPOCH = 11644473600000000


def _local_datetime_to_epoch_us(value: datetime) -> int:
    return int(value.replace(tzinfo=SHANGHAI_TZ).timestamp() * 1000000)


def _chrome_time_to_local_datetime(value: int) -> datetime:
    unix_seconds = (value - CHROME_EPOCH) / 1000000
    return datetime.fromtimestamp(unix_seconds, tz=SHANGHAI_TZ).replace(tzinfo=None)


def _window_chrome_time(window: DateWindow) -> tuple[int, int]:
    return (
        _local_datetime_to_epoch_us(window.start) + CHROME_EPOCH,
        _local_datetime_to_epoch_us(window.end + timedelta(seconds=1)) + CHROME_EPOCH,
    )


def _domain_matches_skip(domain: str, skip_domain: str) -> bool:
    normalized_domain = domain.lower().strip(".")
    normalized_skip = skip_domain.lower().strip(".")
    return normalized_domain == normalized_skip or normalized_domain.endswith(f".{normalized_skip}")


def _should_skip_domain(domain: str, skip_domains: set[str]) -> bool:
    return any(_domain_matches_skip(domain, skip_domain) for skip_domain in skip_domains if skip_domain)


def collect_browser_domains(history_path: Path, window: DateWindow, skip_domains: set[str]) -> list[SourceRecord]:
    if not history_path.exists():
        return []
    records: list[SourceRecord] = []
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        try:
            shutil.copy2(str(history_path), str(tmp_path))
            conn = sqlite3.connect(str(tmp_path))
        except (OSError, sqlite3.Error):
            return records
        try:
            cursor = conn.cursor()
            start, end = _window_chrome_time(window)
            cursor.execute(
                """
                SELECT u.url, u.title, COUNT(*) AS visit_total, MAX(v.visit_time) AS last_visit_time
                FROM visits v
                JOIN urls u ON u.id = v.url
                WHERE v.visit_time >= ? AND v.visit_time < ?
                  AND u.url NOT LIKE 'chrome://%'
                  AND u.url NOT LIKE 'chrome-extension://%'
                  AND u.url NOT LIKE 'about:%'
                GROUP BY u.url, u.title
                ORDER BY last_visit_time DESC
                """,
                (start, end),
            )
            domains: dict[str, dict[str, object]] = {}
            for url, title, visit_count, last_visit_time in cursor.fetchall():
                try:
                    hostname = urlparse(str(url)).hostname
                    domain = hostname.lower() if hostname else ""
                    if not domain or _should_skip_domain(domain, skip_domains):
                        continue
                    bucket = domains.setdefault(domain, {"count": 0, "titles": [], "last_visit": int(last_visit_time or 0)})
                    bucket["count"] = int(bucket["count"]) + int(visit_count or 0)
                    titles = bucket["titles"]
                    if isinstance(titles, list) and title and title not in titles:
                        titles.append(title)
                    bucket["last_visit"] = max(int(bucket["last_visit"]), int(last_visit_time or 0))
                except (TypeError, ValueError):
                    continue
        except sqlite3.Error:
            return records
        finally:
            conn.close()

        for domain, value in sorted(domains.items(), key=lambda item: -int(item[1]["count"])):
            titles = [str(title) for title in list(value["titles"])[:5]]
            last_visit = int(value["last_visit"])
            records.append(
                SourceRecord(
                    source="browser",
                    source_detail="chrome-history",
                    title=domain,
                    content=f"访问 {int(value['count'])} 次；代表页面: {'；'.join(titles)}",
                    timestamp=_chrome_time_to_local_datetime(last_visit),
                    tags=("资料调研",),
                    raw={"domain": domain, "titles": titles},
                )
            )
        return records
    finally:
        with suppress(OSError):
            tmp_path.unlink()
