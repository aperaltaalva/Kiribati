from __future__ import annotations

from datetime import datetime, timezone

from kiribati_monitor import run_daily
from kiribati_monitor.models import Article, Source


def sample_article() -> Article:
    return Article(
        title="Kiribati budget update",
        source_name="Ministry of Finance and Economic Development",
        source_type="official",
        source_url="https://www.mfed.gov.ki/",
        source_tags=["fiscal", "budget"],
        source_importance=5,
        url="https://www.mfed.gov.ki/news/budget-update",
        published_date=datetime(2026, 5, 17, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        snippet="Budget update.",
        text="Budget update.",
        raw_metadata={},
    )


def test_dry_run_skips_openai_and_email(monkeypatch, tmp_path) -> None:
    source = Source(
        name="Ministry of Finance and Economic Development",
        url="https://www.mfed.gov.ki/",
        source_type="official",
        fetch_method="html",
        tags=["fiscal"],
        importance=5,
        enabled=True,
    )
    source_log = [
        {
            "name": source.name,
            "url": str(source.url),
            "source_type": source.source_type,
            "fetch_method": source.fetch_method,
            "status": "ok",
            "count": 1,
            "error": None,
            "attempt_count": 1,
            "elapsed_seconds": 0.01,
        }
    ]

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(run_daily, "load_sources", lambda path: [source])
    monkeypatch.setattr(
        run_daily,
        "fetch_sources",
        lambda sources, since_hours, timeout_seconds, max_items, max_attempts, backoff_seconds: (
            [sample_article()],
            source_log,
        ),
    )
    monkeypatch.setattr(
        run_daily,
        "classify_article",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("OpenAI should not be called in dry-run")),
    )
    monkeypatch.setattr(
        run_daily,
        "send_brief_email",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Email should not be sent in dry-run")),
    )
    monkeypatch.setattr(
        run_daily,
        "publish_static_site",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Site should not publish unless requested")),
    )

    exit_code = run_daily.main(["--dry-run", "--max-items", "1"])

    assert exit_code == 0
    assert (tmp_path / "daily_brief_2026-05-17.md").exists() or list(tmp_path.glob("daily_brief_*.md"))
    assert list(tmp_path.glob("daily_email_*.html"))


def test_require_email_fails_when_email_is_not_configured(monkeypatch, tmp_path) -> None:
    source = Source(
        name="Ministry of Finance and Economic Development",
        url="https://www.mfed.gov.ki/",
        source_type="official",
        fetch_method="html",
        tags=["fiscal"],
        importance=5,
        enabled=True,
    )
    source_log = [
        {
            "name": source.name,
            "url": str(source.url),
            "source_type": source.source_type,
            "fetch_method": source.fetch_method,
            "status": "ok",
            "count": 1,
            "error": None,
            "attempt_count": 1,
            "elapsed_seconds": 0.01,
        }
    ]

    class FakeConnection:
        def execute(self, *args, **kwargs):
            return self

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("REQUIRE_EMAIL", "true")
    monkeypatch.setattr(run_daily, "load_sources", lambda path: [source])
    monkeypatch.setattr(
        run_daily,
        "fetch_sources",
        lambda sources, since_hours, timeout_seconds, max_items, max_attempts, backoff_seconds: (
            [sample_article()],
            source_log,
        ),
    )
    monkeypatch.setattr(run_daily, "connect", lambda db_path: FakeConnection())
    monkeypatch.setattr(run_daily, "start_daily_run", lambda connection, since_hours: 1)
    monkeypatch.setattr(run_daily, "store_articles", lambda connection, articles: type("Summary", (), {"inserted": 1, "duplicates": 0, "total": 1, "source_inserted": {source.name: 1}})())
    monkeypatch.setattr(run_daily, "get_articles_needing_classification", lambda connection, since_hours, max_items: [])
    monkeypatch.setattr(
        run_daily,
        "get_recent_classified_articles",
        lambda connection, since_hours, max_items: run_daily.build_dry_run_records([sample_article()]),
    )
    monkeypatch.setattr(run_daily, "send_brief_email", lambda *args, **kwargs: False)
    monkeypatch.setattr(run_daily, "finish_daily_run", lambda *args, **kwargs: None)

    assert run_daily.main(["--max-items", "1"]) == 1
