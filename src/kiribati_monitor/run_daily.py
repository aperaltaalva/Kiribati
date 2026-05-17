from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from .brief import generate_daily_brief, generate_email_brief
from .classify import OpenAIConfigurationError, classify_article, get_model, heuristic_classification
from .emailer import send_brief_email
from .ingest import fetch_sources, load_sources
from .models import StoredArticle
from .observability import annotate_source_health, log_source_health, summarize_source_health
from .static_site import publish_static_site
from .storage import (
    connect,
    finish_daily_run,
    get_articles_needing_classification,
    get_recent_classified_articles,
    save_classification,
    start_daily_run,
    store_articles,
)

LOGGER = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Kiribati daily macro monitor.")
    parser.add_argument("--since-hours", type=int, default=36, help="Lookback window for recent articles.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and generate a brief without persistent storage, OpenAI, or email.")
    parser.add_argument("--no-email", action="store_true", help="Skip email even if SMTP is configured.")
    parser.add_argument("--max-items", type=int, default=None, help="Maximum number of fetched/classified items for this run.")
    parser.add_argument("--publish-site", action="store_true", help="Generate a static public-source-only site in site/. Disabled by default.")
    parser.add_argument(
        "--fresh-hours",
        type=int,
        default=None,
        help="Only brief articles with a published date in the last N hours. Undated articles are excluded.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    configure_logging()
    started_at = time.monotonic()

    sources = load_sources("sources.yaml")
    enabled_count = sum(1 for source in sources if source.enabled)
    timeout_seconds = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
    max_attempts = int(os.getenv("HTTP_MAX_ATTEMPTS", "3"))
    backoff_seconds = float(os.getenv("HTTP_BACKOFF_SECONDS", "1.0"))
    LOGGER.info(
        "Starting Kiribati monitor run: mode=%s since_hours=%s max_items=%s sources=%s enabled=%s timeout=%ss attempts=%s backoff=%ss",
        "dry_run" if args.dry_run else "live",
        args.since_hours,
        args.max_items,
        len(sources),
        enabled_count,
        timeout_seconds,
        max_attempts,
        backoff_seconds,
    )

    articles, source_log = fetch_sources(
        sources,
        since_hours=args.since_hours,
        timeout_seconds=timeout_seconds,
        max_items=args.max_items,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )
    LOGGER.info("Fetch complete: fetched_articles=%s", len(articles))
    articles, source_log = filter_fresh_articles(
        articles,
        source_log,
        fresh_hours=args.fresh_hours,
    )

    output_dir = Path(os.getenv("OUTPUT_DIR", "output"))

    if args.dry_run:
        source_log = annotate_source_health(source_log, dry_run=True)
        log_source_health(LOGGER, source_log)
        LOGGER.info("Dry run: using heuristic classification only; skipping SQLite storage, OpenAI API, and email")
        classified_records = build_dry_run_records(articles)
        md_path, html_path = generate_daily_brief(
            classified_records,
            output_dir=output_dir,
            source_log=source_log,
        )
        email_html_path = generate_email_brief(
            classified_records,
            output_dir=output_dir,
            source_log=source_log,
        )
        site_index_path = publish_site_if_requested(args.publish_site, output_dir)
        health_summary = summarize_source_health(source_log)
        duration_seconds = time.monotonic() - started_at
        LOGGER.info(
            "Run summary: mode=dry_run fetched=%s new=%s classified=%s source_failures=%s no_new_sources=%s brief_md=%s brief_html=%s email_html=%s site_index=%s duration=%.1fs",
            len(articles),
            len(articles),
            len(classified_records),
            health_summary["sources_failed"],
            health_summary["sources_no_new_items"],
            md_path,
            html_path,
            email_html_path,
            site_index_path,
            duration_seconds,
        )
        print_summary(
            fetched=len(articles),
            inserted=len(articles),
            duplicates=0,
            classified=len(classified_records),
            md_path=md_path,
            html_path=html_path,
            email_sent=False,
            source_health=health_summary,
            duration_seconds=duration_seconds,
        )
        return 0

    connection = connect(os.getenv("DB_PATH", "data/kiribati_monitor.sqlite"))
    run_id = start_daily_run(connection, args.since_hours)
    classified_count = 0
    errors = [entry for entry in source_log if entry.get("status") == "error"]

    try:
        store_summary = store_articles(connection, articles)
        source_log = annotate_source_health(source_log, new_counts_by_source=store_summary.source_inserted)
        log_source_health(LOGGER, source_log)
        LOGGER.info(
            "Storage complete: fetched=%s inserted=%s duplicates=%s",
            store_summary.total,
            store_summary.inserted,
            store_summary.duplicates,
        )
        to_classify = get_articles_needing_classification(
            connection,
            since_hours=args.since_hours,
            max_items=args.max_items,
        )
        LOGGER.info("Classification queue: articles_needing_classification=%s model=%s", len(to_classify), get_model())

        for stored in to_classify:
            try:
                classification = classify_article(stored.article, model=get_model())
            except OpenAIConfigurationError:
                LOGGER.warning("OPENAI_API_KEY missing; storing heuristic fallback classification")
                classification = heuristic_classification(stored.article)
            except Exception as exc:
                LOGGER.exception("Classifier failed for article %s", stored.id)
                errors.append({"name": stored.article.source_name, "url": stored.article.url, "error": str(exc)})
                classification = heuristic_classification(stored.article)
            save_classification(connection, stored.id, classification)
            classified_count += 1
        LOGGER.info("Classification complete: classified=%s", classified_count)

        recent = get_recent_classified_articles(
            connection,
            since_hours=args.since_hours,
            max_items=args.max_items,
        )
        recent = filter_fresh_stored_articles(recent, fresh_hours=args.fresh_hours)
        md_path, html_path = generate_daily_brief(recent, output_dir=output_dir, source_log=source_log)
        email_html_path = generate_email_brief(recent, output_dir=output_dir, source_log=source_log)
        LOGGER.info(
            "Brief generated: markdown=%s html=%s email_html=%s included_recent_classified=%s",
            md_path,
            html_path,
            email_html_path,
            len(recent),
        )

        email_sent = False
        email_error = None
        if not args.no_email:
            try:
                email_sent = send_brief_email(
                    subject=f"Kiribati Daily Macro and Policy Monitor - {md_path.stem[-10:]}",
                    markdown_path=md_path,
                    html_path=email_html_path,
                )
            except Exception as exc:
                LOGGER.exception("Email delivery failed")
                email_error = str(exc)
                email_sent = False
        else:
            print(f"Email skipped. Brief generated at {md_path} and {html_path}.")
            if require_email_delivery():
                email_error = "Email delivery is required but --no-email was used."

        site_index_path = publish_site_if_requested(args.publish_site, output_dir)
        write_email_status(output_dir, email_sent=email_sent, email_error=email_error)

        if require_email_delivery() and not email_sent:
            raise RuntimeError(
                email_error
                or "Email delivery is required but no email was sent. Check EMAIL_TO, EMAIL_FROM, and SMTP_* secrets."
            )

        finish_daily_run(
            connection,
            run_id,
            fetched_count=len(articles),
            new_count=store_summary.inserted,
            classified_count=classified_count,
            brief_md_path=str(md_path),
            brief_html_path=str(html_path),
            errors=errors,
        )
        health_summary = summarize_source_health(source_log)
        duration_seconds = time.monotonic() - started_at
        LOGGER.info(
            "Run summary: mode=live fetched=%s inserted=%s duplicates=%s classified=%s source_failures=%s no_new_sources=%s email_sent=%s brief_md=%s brief_html=%s email_html=%s site_index=%s duration=%.1fs",
            len(articles),
            store_summary.inserted,
            store_summary.duplicates,
            classified_count,
            health_summary["sources_failed"],
            health_summary["sources_no_new_items"],
            email_sent,
            md_path,
            html_path,
            email_html_path,
            site_index_path,
            duration_seconds,
        )
        print_summary(
            fetched=len(articles),
            inserted=store_summary.inserted,
            duplicates=store_summary.duplicates,
            classified=classified_count,
            md_path=md_path,
            html_path=html_path,
            email_sent=email_sent,
            source_health=health_summary,
            duration_seconds=duration_seconds,
        )
        return 0
    except Exception:
        LOGGER.exception("Daily run failed")
        finish_daily_run(
            connection,
            run_id,
            fetched_count=len(articles),
            new_count=0,
            classified_count=classified_count,
            brief_md_path=None,
            brief_html_path=None,
            errors=errors,
            status="failed",
        )
        return 1
    finally:
        connection.close()


def build_dry_run_records(articles: list) -> list[StoredArticle]:
    records: list[StoredArticle] = []
    for index, article in enumerate(articles, start=1):
        records.append(
            StoredArticle(
                id=index,
                article=article,
                classification=heuristic_classification(article),
            )
        )
    return records


def filter_fresh_articles(
    articles: list,
    source_log: list[dict],
    *,
    fresh_hours: int | None,
) -> tuple[list, list[dict]]:
    if fresh_hours is None:
        return articles, source_log

    cutoff = time.time() - max(1, fresh_hours) * 3600
    filtered = []
    kept_by_source: dict[str, int] = {}
    excluded_by_source: dict[str, int] = {}

    for article in articles:
        if article.published_date and article.published_date.timestamp() >= cutoff:
            filtered.append(article)
            kept_by_source[article.source_name] = kept_by_source.get(article.source_name, 0) + 1
        else:
            excluded_by_source[article.source_name] = excluded_by_source.get(article.source_name, 0) + 1

    updated_log = []
    for entry in source_log:
        updated = dict(entry)
        source_name = str(updated.get("name"))
        updated["raw_count"] = int(updated.get("count") or 0)
        updated["count"] = kept_by_source.get(source_name, 0)
        updated["excluded_old_or_undated_count"] = excluded_by_source.get(source_name, 0)
        updated_log.append(updated)

    LOGGER.info(
        "Freshness filter applied: fresh_hours=%s kept=%s excluded_old_or_undated=%s",
        fresh_hours,
        len(filtered),
        len(articles) - len(filtered),
    )
    return filtered, updated_log


def filter_fresh_stored_articles(
    articles: list[StoredArticle],
    *,
    fresh_hours: int | None,
) -> list[StoredArticle]:
    if fresh_hours is None:
        return articles
    cutoff = time.time() - max(1, fresh_hours) * 3600
    filtered = [
        item
        for item in articles
        if item.article.published_date and item.article.published_date.timestamp() >= cutoff
    ]
    LOGGER.info(
        "Stored-article freshness filter applied: fresh_hours=%s kept=%s excluded_old_or_undated=%s",
        fresh_hours,
        len(filtered),
        len(articles) - len(filtered),
    )
    return filtered


def publish_site_if_requested(publish_site: bool, output_dir: Path) -> Path | None:
    if not publish_site:
        LOGGER.info("Static site publishing disabled; pass --publish-site to generate site/")
        return None
    site_index_path = publish_static_site(output_dir=output_dir, site_dir=os.getenv("SITE_DIR", "site"))
    LOGGER.info("Static site generated at %s", site_index_path)
    return site_index_path


def write_email_status(output_dir: Path, *, email_sent: bool, email_error: str | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "email_status.json"
    status_path.write_text(
        json.dumps(
            {
                "email_sent": email_sent,
                "email_error": email_error,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    if email_error:
        (output_dir / "email_failed.txt").write_text(email_error, encoding="utf-8")


def require_email_delivery() -> bool:
    return os.getenv("REQUIRE_EMAIL", "").strip().lower() in {"1", "true", "yes", "on"}


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def print_summary(
    *,
    fetched: int,
    inserted: int,
    duplicates: int,
    classified: int,
    md_path: Path,
    html_path: Path,
    email_sent: bool,
    source_health: dict[str, int],
    duration_seconds: float,
) -> None:
    print(
        "Run complete: "
        f"fetched={fetched}, new={inserted}, duplicates={duplicates}, classified={classified}, "
        f"sources_succeeded={source_health['sources_succeeded']}, "
        f"sources_failed={source_health['sources_failed']}, "
        f"sources_no_new_items={source_health['sources_no_new_items']}, "
        f"email_sent={email_sent}, markdown={md_path}, html={html_path}"
        f", duration_seconds={duration_seconds:.1f}"
    )


if __name__ == "__main__":
    sys.exit(main())
