from __future__ import annotations

import requests

from kiribati_monitor import ingest
from kiribati_monitor.models import Source
from kiribati_monitor.observability import annotate_source_health


class FakeResponse:
    def __init__(self, status_code: int, *, text: str = "", content: bytes | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(f"{self.status_code} error")
            error.response = self
            raise error

    def json(self) -> dict:
        return {}


def test_request_with_retries_retries_retryable_status(monkeypatch) -> None:
    responses = [FakeResponse(500), FakeResponse(200, text="ok")]

    class FakeSession:
        def get(self, url: str, timeout: int) -> FakeResponse:
            return responses.pop(0)

    monkeypatch.setattr(ingest, "sleep_before_retry", lambda backoff_seconds, attempt: None)
    response, attempts = ingest.request_with_retries(
        FakeSession(),  # type: ignore[arg-type]
        "https://example.org",
        timeout_seconds=1,
        max_attempts=2,
        backoff_seconds=0,
    )

    assert response.status_code == 200
    assert attempts == 2
    assert responses == []


def test_fetch_sources_handles_http_failure_without_crashing(monkeypatch) -> None:
    source = Source(
        name="Example RSS",
        url="https://example.org/feed.xml",
        source_type="media",
        fetch_method="rss",
        tags=["test"],
        importance=1,
        enabled=True,
    )

    class FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def get(self, url: str, timeout: int) -> FakeResponse:
            raise requests.Timeout("simulated timeout")

    monkeypatch.setattr(ingest.requests, "Session", FakeSession)
    monkeypatch.setattr(ingest, "sleep_before_retry", lambda backoff_seconds, attempt: None)

    articles, source_log = ingest.fetch_sources(
        [source],
        timeout_seconds=1,
        max_attempts=2,
        backoff_seconds=0,
    )

    assert articles == []
    assert source_log[0]["status"] == "error"
    assert "simulated timeout" in source_log[0]["error"]
    assert source_log[0]["attempt_count"] == 2


def test_empty_feed_is_reported_as_no_new_items(monkeypatch) -> None:
    source = Source(
        name="Empty Feed",
        url="https://example.org/feed.xml",
        source_type="media",
        fetch_method="rss",
        tags=["test"],
        importance=1,
        enabled=True,
    )

    class FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def get(self, url: str, timeout: int) -> FakeResponse:
            return FakeResponse(200, text="<rss><channel></channel></rss>")

    monkeypatch.setattr(ingest.requests, "Session", FakeSession)

    articles, source_log = ingest.fetch_sources([source], timeout_seconds=1, max_attempts=1)
    health = annotate_source_health(source_log, dry_run=True)

    assert articles == []
    assert source_log[0]["status"] == "ok"
    assert health[0]["health"] == "no_new_items"
