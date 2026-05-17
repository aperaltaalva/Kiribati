from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .models import Article, Classification, StoredArticle, utc_now

LOGGER = logging.getLogger(__name__)
TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
    "ref_src",
    "spm",
}


@dataclass(frozen=True)
class StoreSummary:
    inserted: int
    duplicates: int
    total: int
    source_inserted: dict[str, int] = field(default_factory=dict)
    source_duplicates: dict[str, int] = field(default_factory=dict)


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    init_db(connection)
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            normalized_url TEXT NOT NULL,
            url_hash TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_tags TEXT NOT NULL,
            source_importance INTEGER NOT NULL,
            published_date TEXT,
            fetched_at TEXT NOT NULL,
            snippet TEXT NOT NULL,
            text TEXT NOT NULL,
            raw_metadata TEXT NOT NULL,
            classification_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            since_hours INTEGER NOT NULL,
            fetched_count INTEGER DEFAULT 0,
            new_count INTEGER DEFAULT 0,
            classified_count INTEGER DEFAULT 0,
            brief_md_path TEXT,
            brief_html_path TEXT,
            errors_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'running'
        )
        """
    )
    connection.commit()


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_PARAMS:
            continue
        filtered_query.append((key, value))
    query = urlencode(sorted(filtered_query))

    return urlunparse((scheme, netloc, path, "", query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def store_articles(connection: sqlite3.Connection, articles: list[Article]) -> StoreSummary:
    inserted = 0
    duplicates = 0
    source_inserted: dict[str, int] = {}
    source_duplicates: dict[str, int] = {}
    for article in articles:
        was_inserted = store_article(connection, article)
        if was_inserted:
            inserted += 1
            source_inserted[article.source_name] = source_inserted.get(article.source_name, 0) + 1
        else:
            duplicates += 1
            source_duplicates[article.source_name] = source_duplicates.get(article.source_name, 0) + 1
    connection.commit()
    return StoreSummary(
        inserted=inserted,
        duplicates=duplicates,
        total=len(articles),
        source_inserted=source_inserted,
        source_duplicates=source_duplicates,
    )


def store_article(connection: sqlite3.Connection, article: Article) -> bool:
    normalized = normalize_url(article.url)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    now = utc_now().isoformat()
    existing = connection.execute("SELECT id FROM articles WHERE url_hash = ?", (digest,)).fetchone()
    if existing:
        connection.execute(
            """
            UPDATE articles
            SET fetched_at = ?, updated_at = ?, raw_metadata = ?
            WHERE id = ?
            """,
            (
                article.fetched_at.isoformat(),
                now,
                json.dumps(article.raw_metadata, sort_keys=True),
                existing["id"],
            ),
        )
        return False

    connection.execute(
        """
        INSERT INTO articles (
            url, normalized_url, url_hash, title, source_name, source_type, source_url,
            source_tags, source_importance, published_date, fetched_at, snippet, text,
            raw_metadata, classification_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            article.url,
            normalized,
            digest,
            article.title,
            article.source_name,
            article.source_type,
            article.source_url,
            json.dumps(article.source_tags),
            article.source_importance,
            article.published_date.isoformat() if article.published_date else None,
            article.fetched_at.isoformat(),
            article.snippet,
            article.text,
            json.dumps(article.raw_metadata, sort_keys=True),
            now,
            now,
        ),
    )
    return True


def get_articles_needing_classification(
    connection: sqlite3.Connection,
    since_hours: int = 36,
    max_items: int | None = None,
) -> list[StoredArticle]:
    cutoff = (utc_now() - timedelta(hours=since_hours)).isoformat()
    sql = """
        SELECT *
        FROM articles
        WHERE classification_json IS NULL
          AND COALESCE(published_date, fetched_at) >= ?
        ORDER BY source_importance DESC, COALESCE(published_date, fetched_at) DESC
    """
    params: list[Any] = [cutoff]
    if max_items is not None:
        sql += " LIMIT ?"
        params.append(max_items)
    rows = connection.execute(sql, params).fetchall()
    return [row_to_stored_article(row) for row in rows]


def get_recent_classified_articles(
    connection: sqlite3.Connection,
    since_hours: int = 36,
    max_items: int | None = None,
) -> list[StoredArticle]:
    cutoff = (utc_now() - timedelta(hours=since_hours)).isoformat()
    sql = """
        SELECT *
        FROM articles
        WHERE classification_json IS NOT NULL
          AND COALESCE(published_date, fetched_at) >= ?
        ORDER BY source_importance DESC, COALESCE(published_date, fetched_at) DESC
    """
    params: list[Any] = [cutoff]
    if max_items is not None:
        sql += " LIMIT ?"
        params.append(max_items)
    rows = connection.execute(sql, params).fetchall()
    return [row_to_stored_article(row) for row in rows]


def save_classification(
    connection: sqlite3.Connection,
    article_id: int,
    classification: Classification,
) -> None:
    connection.execute(
        """
        UPDATE articles
        SET classification_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            classification.model_dump_json(),
            utc_now().isoformat(),
            article_id,
        ),
    )
    connection.commit()


def row_to_stored_article(row: sqlite3.Row) -> StoredArticle:
    article = Article(
        title=row["title"],
        source_name=row["source_name"],
        source_type=row["source_type"],
        source_url=row["source_url"],
        source_tags=json.loads(row["source_tags"] or "[]"),
        source_importance=row["source_importance"],
        url=row["url"],
        published_date=parse_iso(row["published_date"]),
        fetched_at=parse_iso(row["fetched_at"]) or utc_now(),
        snippet=row["snippet"],
        text=row["text"],
        raw_metadata=json.loads(row["raw_metadata"] or "{}"),
    )
    classification = None
    if row["classification_json"]:
        classification = Classification.model_validate_json(row["classification_json"])
    return StoredArticle(id=row["id"], article=article, classification=classification)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def start_daily_run(connection: sqlite3.Connection, since_hours: int) -> int:
    cursor = connection.execute(
        "INSERT INTO daily_runs (started_at, since_hours) VALUES (?, ?)",
        (utc_now().isoformat(), since_hours),
    )
    connection.commit()
    return int(cursor.lastrowid)


def finish_daily_run(
    connection: sqlite3.Connection,
    run_id: int,
    *,
    fetched_count: int,
    new_count: int,
    classified_count: int,
    brief_md_path: str | None,
    brief_html_path: str | None,
    errors: list[dict[str, Any]] | None = None,
    status: str = "completed",
) -> None:
    connection.execute(
        """
        UPDATE daily_runs
        SET finished_at = ?, fetched_count = ?, new_count = ?, classified_count = ?,
            brief_md_path = ?, brief_html_path = ?, errors_json = ?, status = ?
        WHERE id = ?
        """,
        (
            utc_now().isoformat(),
            fetched_count,
            new_count,
            classified_count,
            brief_md_path,
            brief_html_path,
            json.dumps(errors or []),
            status,
            run_id,
        ),
    )
    connection.commit()
