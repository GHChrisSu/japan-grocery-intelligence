import fcntl
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from curl_cffi.requests.exceptions import RequestException
from loguru import logger
from protego import Protego
from sqlalchemy import select

from grocery_intelligence.database import (
    database,
    digest,
    errors,
    persist,
    register,
    runs,
    sources,
)
from grocery_intelligence.fetching import http_get
from grocery_intelligence.models import now
from grocery_intelligence.parsing import has_layout, parse_html


class Halt(ValueError):
    def __init__(self, message, pause=None):
        super().__init__(message)
        self.pause = pause


@contextmanager
def exclusive(database_path):
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with database_path.with_suffix(".crawl.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def retry_after(value):
    current = now()
    if value and value.isascii() and value.isdecimal() and len(value) <= 9:
        return current + timedelta(seconds=max(60, int(value)))
    if value:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo:
                return max(retry_at, current + timedelta(seconds=60)).astimezone(
                    current.tzinfo
                )
        except (TypeError, ValueError, OverflowError):
            pass
    return current + timedelta(hours=12)


def fetch(url, settings, headers=None, gap=None):
    gap = settings.request_gap if gap is None else gap
    for attempt in range(3):
        time.sleep(max(1, gap, 2**attempt if attempt else 0))
        try:
            response = http_get(url, settings.user_agent, headers)
        except RequestException:
            if attempt == 2:
                raise Halt("Transport failed after 3 attempts") from None
            continue
        if response.status == 429:
            raise Halt("HTTP 429", retry_after(response.headers.get("retry-after")))
        if response.status in (401, 403):
            raise Halt(f"HTTP {response.status}", now().replace(year=9999))
        if response.status in (500, 502, 503, 504) and attempt < 2:
            continue
        return response
    raise AssertionError("Unreachable")


def collect(source, settings):
    if not source.enabled:
        return "disabled", 0
    if not has_layout(source):
        return "unknown-parser", 0
    with exclusive(settings.database):
        timestamp = now()
        with database(settings.database) as conn:
            source_id = register(conn, source)
            all_states = conn.execute(select(sources)).mappings().all()
            state = next(row for row in all_states if row["id"] == source_id)
            origin = str(source.robots_url)
            if any(
                row["config"].get("robots_url") == origin
                and row["pause_until"]
                and datetime.fromisoformat(row["pause_until"]) > timestamp
                for row in all_states
            ):
                return "paused", 0
            if state["last_attempt"] and timestamp < datetime.fromisoformat(
                state["last_attempt"]
            ) + timedelta(hours=source.crawl_interval_hours):
                return "interval", 0
            conn.execute(
                sources.update()
                .where(sources.c.id == source_id)
                .values(
                    last_attempt=timestamp.isoformat(),
                )
            )
            run_id = conn.execute(
                runs.insert().returning(runs.c.id),
                {
                    "source_id": source_id,
                    "started_at": timestamp.isoformat(),
                    "status": "running",
                    "count": 0,
                },
            ).scalar_one()
        status, count = "error", 0
        try:
            rules = fetch(source.robots_url, settings)
            if rules.status != 200:
                raise Halt(f"robots.txt HTTP {rules.status}")
            robots = Protego.parse(rules.body.decode("utf-8-sig"))
            if not robots.can_fetch(str(source.url), settings.user_agent):
                raise Halt("robots.txt disallows URL", now().replace(year=9999))
            headers = {
                key: state[field]
                for key, field in (
                    ("If-None-Match", "etag"),
                    ("If-Modified-Since", "modified"),
                )
                if state[field]
            }
            delay = robots.crawl_delay(settings.user_agent) or 0
            limit = robots.request_rate(settings.user_agent)
            if limit and limit.requests:
                delay = max(delay, limit.seconds / limit.requests)
            response = fetch(
                source.url, settings, headers, max(delay, settings.request_gap)
            )
            if response.status == 304 and state["last_hash"]:
                status = "unchanged"
            elif response.status == 200:
                if response.css(
                    '#challenge-form, iframe[src*="captcha"], form input[type="password"]'
                ):
                    raise Halt("Challenge or login detected", now().replace(year=9999))
                hashed = digest(response.body)
                if hashed == state["last_hash"]:
                    status = "unchanged"
                else:
                    records = parse_html(response, source)
                    with database(settings.database) as conn:
                        count = persist(
                            conn,
                            source_id,
                            hashed,
                            records,
                            replay=bool(state["last_hash"]),
                        )
                        conn.execute(
                            sources.update()
                            .where(sources.c.id == source_id)
                            .values(
                                last_hash=hashed,
                                etag=response.headers.get("etag"),
                                modified=response.headers.get("last-modified"),
                            )
                        )
                    status = "success"
            else:
                raise Halt(f"Unexpected HTTP {response.status} (redirects disabled)")
        except (ValueError, OSError, RequestException) as exc:
            reason = str(exc) if isinstance(exc, Halt) else type(exc).__name__
            logger.warning("{}: {}", source_id, reason)
            with database(settings.database) as conn:
                conn.execute(errors.insert().values(run_id=run_id, reason=reason))
                if isinstance(exc, Halt) and exc.pause:
                    conn.execute(
                        sources.update()
                        .where(sources.c.id == source_id)
                        .values(pause_until=exc.pause.isoformat())
                    )
        with database(settings.database) as conn:
            conn.execute(
                runs.update()
                .where(runs.c.id == run_id)
                .values(status=status, count=count, finished_at=now().isoformat())
            )
    return status, count
