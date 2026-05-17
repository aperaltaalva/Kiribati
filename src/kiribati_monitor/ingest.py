from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from pydantic import ValidationError

from .models import Article, Source, utc_now

LOGGER = logging.getLogger(__name__)
USER_AGENT = (
    "Mozilla/5.0 (compatible; kiribati-macro-monitor/0.1; "
    "+https://github.com/public-source-monitoring)"
)
HTTP_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


class FetchResult:
    def __init__(
        self,
        source: Source,
        articles: list[Article] | None = None,
        status: str = "ok",
        error: str | None = None,
        attempt_count: int = 0,
        elapsed_seconds: float = 0.0,
    ) -> None:
        self.source = source
        self.articles = articles or []
        self.status = status
        self.error = error
        self.attempt_count = attempt_count
        self.elapsed_seconds = elapsed_seconds

    def as_log_entry(self) -> dict[str, Any]:
        return {
            "name": self.source.name,
            "url": str(self.source.url),
            "fetch_method": self.source.fetch_method,
            "source_type": self.source.source_type,
            "status": self.status,
            "count": len(self.articles),
            "error": self.error,
            "attempt_count": self.attempt_count,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def load_sources(path: str | Path = "sources.yaml") -> list[Source]:
    source_path = Path(path)
    with source_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or []

    entries = raw.get("sources", raw) if isinstance(raw, dict) else raw
    sources: list[Source] = []
    for index, entry in enumerate(entries):
        try:
            sources.append(Source.model_validate(entry))
        except ValidationError as exc:
            raise ValueError(f"Invalid source at index {index}: {exc}") from exc
    return sources


def fetch_sources(
    sources: list[Source],
    since_hours: int = 36,
    timeout_seconds: int = 20,
    max_items: int | None = None,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
) -> tuple[list[Article], list[dict[str, Any]]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8,*/*;q=0.7",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )

    all_articles: list[Article] = []
    source_log: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for source in sources:
        if max_items is not None and len(all_articles) >= max_items:
            result = FetchResult(source=source, status="skipped_max_items")
            source_log.append(result.as_log_entry())
            continue
        if not source.enabled:
            result = FetchResult(source=source, status="disabled")
            source_log.append(result.as_log_entry())
            continue
        if source.fetch_method == "manual":
            result = FetchResult(source=source, status="manual")
            source_log.append(result.as_log_entry())
            continue

        started = time.monotonic()
        try:
            if source.fetch_method == "rss":
                result = fetch_rss_source(
                    source,
                    session,
                    timeout_seconds,
                    max_attempts=max_attempts,
                    backoff_seconds=backoff_seconds,
                )
            elif source.fetch_method == "html":
                result = fetch_html_source(
                    source,
                    session,
                    timeout_seconds,
                    max_attempts=max_attempts,
                    backoff_seconds=backoff_seconds,
                )
            elif source.fetch_method == "gdelt":
                result = fetch_gdelt_source(
                    source,
                    session,
                    timeout_seconds,
                    since_hours,
                    max_attempts=max_attempts,
                    backoff_seconds=backoff_seconds,
                )
            else:
                result = FetchResult(source=source, status="skipped", error="Unsupported fetch method")
        except Exception as exc:
            LOGGER.warning("Failed to fetch source %s: %s", source.name, exc)
            LOGGER.debug("Fetch traceback for %s", source.name, exc_info=True)
            result = FetchResult(
                source=source,
                status="error",
                error=str(exc),
                attempt_count=int(getattr(exc, "attempt_count", 0) or 0),
                elapsed_seconds=time.monotonic() - started,
            )
        else:
            result.elapsed_seconds = time.monotonic() - started

        for article in result.articles:
            if article.url in seen_urls:
                continue
            seen_urls.add(article.url)
            all_articles.append(article)
            if max_items is not None and len(all_articles) >= max_items:
                break

        source_log.append(result.as_log_entry())

    return all_articles, source_log


def fetch_rss_source(
    source: Source,
    session: requests.Session,
    timeout_seconds: int,
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
) -> FetchResult:
    response, attempts = request_with_retries(
        session,
        str(source.url),
        timeout_seconds,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )

    parsed = feedparser.parse(response.content)
    if getattr(parsed, "bozo", False):
        LOGGER.warning("Malformed feed for %s: %s", source.name, getattr(parsed, "bozo_exception", "unknown"))

    articles: list[Article] = []
    for entry in parsed.entries[:50]:
        url = entry.get("link") or str(source.url)
        title = clean_text(entry.get("title") or source.name)
        snippet = clean_text(entry.get("summary") or entry.get("description") or "")
        published_date = parse_feed_date(entry)
        articles.append(
            build_article(
                source=source,
                title=title,
                url=url,
                published_date=published_date,
                snippet=snippet,
                text=snippet,
                raw_metadata=dict(entry),
            )
        )

    return FetchResult(source=source, articles=articles, attempt_count=attempts)


def fetch_html_source(
    source: Source,
    session: requests.Session,
    timeout_seconds: int,
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
) -> FetchResult:
    response, attempts = request_with_retries(
        session,
        str(source.url),
        timeout_seconds,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    articles = extract_html_articles(source, soup)
    if not articles:
        articles = [extract_page_as_article(source, soup)]
    return FetchResult(source=source, articles=articles[:30], attempt_count=attempts)


def fetch_gdelt_source(
    source: Source,
    session: requests.Session,
    timeout_seconds: int,
    since_hours: int,
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
) -> FetchResult:
    url = with_gdelt_timespan(str(source.url), since_hours)
    response, attempts = request_with_retries(
        session,
        url,
        timeout_seconds,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )
    payload = response.json()

    articles: list[Article] = []
    for item in payload.get("articles", [])[:75]:
        article_url = item.get("url")
        if not article_url:
            continue
        title = clean_text(item.get("title") or article_url)
        domain = item.get("domain") or item.get("sourcecountry") or source.name
        snippet = clean_text(item.get("seendate", ""))
        published_date = parse_gdelt_date(item.get("seendate"))
        articles.append(
            build_article(
                source=source,
                title=title,
                url=article_url,
                published_date=published_date,
                snippet=snippet,
                text=snippet,
                raw_metadata={**item, "gdelt_query_url": url, "gdelt_domain": domain},
            )
        )
    return FetchResult(source=source, articles=articles, attempt_count=attempts)


def request_with_retries(
    session: requests.Session,
    url: str,
    timeout_seconds: int,
    *,
    max_attempts: int = 3,
    backoff_seconds: float = 1.0,
) -> tuple[requests.Response, int]:
    attempts = max(1, int(max_attempts))
    delay = max(0.0, float(backoff_seconds))
    last_error: requests.RequestException | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, timeout=timeout_seconds)
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code in HTTP_RETRY_STATUS_CODES and attempt < attempts:
                LOGGER.info(
                    "Retryable HTTP status %s for %s on attempt %s/%s",
                    status_code,
                    url,
                    attempt,
                    attempts,
                )
                sleep_before_retry(delay, attempt)
                continue
            response.raise_for_status()
            return response, attempt
        except requests.RequestException as exc:
            setattr(exc, "attempt_count", attempt)
            last_error = exc
            if attempt >= attempts or not is_retryable_request_error(exc):
                raise
            LOGGER.info(
                "Retryable HTTP error for %s on attempt %s/%s: %s",
                url,
                attempt,
                attempts,
                exc,
            )
            sleep_before_retry(delay, attempt)

    if last_error:
        raise last_error
    raise RuntimeError(f"HTTP request failed without a captured exception: {url}")


def is_retryable_request_error(exc: requests.RequestException) -> bool:
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = int(getattr(response, "status_code", 0) or 0)
        return status_code in HTTP_RETRY_STATUS_CODES
    return isinstance(exc, (requests.Timeout, requests.ConnectionError))


def sleep_before_retry(backoff_seconds: float, attempt: int) -> None:
    delay = backoff_seconds * (2 ** max(0, attempt - 1))
    if delay > 0:
        time.sleep(delay)


def extract_html_articles(source: Source, soup: BeautifulSoup) -> list[Article]:
    candidates = soup.select(
        "article, .post, .news-item, .news-card, .entry, .views-row, .item, li[class*=news], div[class*=news]"
    )
    articles: list[Article] = []

    for candidate in candidates:
        link = candidate.find("a", href=True)
        if not link:
            continue
        title = extract_title(candidate, link)
        if len(title) < 8:
            continue
        url = urljoin(str(source.url), link["href"])
        snippet = clean_text(candidate.get_text(" ", strip=True))[:800]
        published_date = extract_html_date(candidate)
        articles.append(
            build_article(
                source=source,
                title=title,
                url=url,
                published_date=published_date,
                snippet=snippet,
                text=snippet,
                raw_metadata={"html_selector": candidate.name},
            )
        )

    if articles:
        return unique_articles(articles)

    main = soup.find("main") or soup.body or soup
    for link in main.find_all("a", href=True, limit=80):
        title = clean_text(link.get_text(" ", strip=True))
        if len(title) < 18 or looks_like_navigation(title):
            continue
        url = urljoin(str(source.url), link["href"])
        if not urlparse(url).scheme.startswith("http"):
            continue
        parent = link.find_parent(["article", "li", "div", "section"]) or link
        snippet = clean_text(parent.get_text(" ", strip=True))[:700]
        articles.append(
            build_article(
                source=source,
                title=title,
                url=url,
                published_date=extract_html_date(parent),
                snippet=snippet,
                text=snippet,
                raw_metadata={"html_selector": "main a"},
            )
        )
    return unique_articles(articles[:30])


def extract_page_as_article(source: Source, soup: BeautifulSoup) -> Article:
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"]
    if not title and soup.find("h1"):
        title = soup.find("h1").get_text(" ", strip=True)
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    if not title:
        title = source.name

    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        description = meta_desc["content"]
    if not description:
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p", limit=5)]
        description = " ".join(paragraphs)

    return build_article(
        source=source,
        title=clean_text(title),
        url=str(source.url),
        published_date=None,
        snippet=clean_text(description)[:900],
        text=clean_text(description)[:2000],
        raw_metadata={"html_selector": "page"},
    )


def build_article(
    source: Source,
    title: str,
    url: str,
    published_date: datetime | None,
    snippet: str,
    text: str,
    raw_metadata: dict[str, Any],
) -> Article:
    return Article(
        title=clean_text(title) or source.name,
        source_name=source.name,
        source_type=source.source_type,
        source_url=str(source.url),
        source_tags=source.tags,
        source_importance=source.importance,
        url=url,
        published_date=published_date,
        fetched_at=utc_now(),
        snippet=clean_text(snippet),
        text=clean_text(text),
        raw_metadata=raw_metadata,
    )


def unique_articles(articles: list[Article]) -> list[Article]:
    seen: set[str] = set()
    unique: list[Article] = []
    for article in articles:
        key = article.url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        unique.append(article)
    return unique


def parse_feed_date(entry: Any) -> datetime | None:
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed_time:
        return datetime(*parsed_time[:6], tzinfo=timezone.utc)
    for field in ("published", "updated", "created"):
        if entry.get(field):
            parsed = parse_datetime(str(entry[field]))
            if parsed:
                return parsed
    return None


def parse_gdelt_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return parse_datetime(value)


def parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def extract_html_date(node: Any) -> datetime | None:
    time_tag = node.find("time") if hasattr(node, "find") else None
    if time_tag:
        for field in ("datetime", "content"):
            if time_tag.get(field):
                parsed = parse_datetime(time_tag[field])
                if parsed:
                    return parsed
        parsed = parse_datetime(time_tag.get_text(" ", strip=True))
        if parsed:
            return parsed

    text = clean_text(node.get_text(" ", strip=True)) if hasattr(node, "get_text") else ""
    match = re.search(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
            try:
                return datetime.strptime(match.group(0).replace(".", ""), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def extract_title(candidate: Any, link: Any) -> str:
    heading = candidate.find(["h1", "h2", "h3", "h4"]) if hasattr(candidate, "find") else None
    if heading:
        return clean_text(heading.get_text(" ", strip=True))
    if link.get("title"):
        return clean_text(link["title"])
    return clean_text(link.get_text(" ", strip=True))


def looks_like_navigation(title: str) -> bool:
    lowered = title.lower()
    if lowered in {"home", "about", "contact", "read more", "learn more", "privacy policy"}:
        return True
    return len(lowered.split()) <= 2 and lowered in {"news", "media", "projects", "publications"}


def clean_text(value: Any) -> str:
    raw = str(value or "")
    if "<" in raw and ">" in raw:
        text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    else:
        text = raw
    return re.sub(r"\s+", " ", text).strip()


def with_gdelt_timespan(url: str, since_hours: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["timespan"] = f"{max(1, int(since_hours))}h"
    query.setdefault("format", "json")
    return urlunparse(parsed._replace(query=urlencode(query)))
