from __future__ import annotations

import logging
from collections import Counter
from typing import Any

HEALTH_LABELS = {
    "succeeded": "Succeeded",
    "failed": "Failed",
    "no_new_items": "No new items",
    "skipped": "Skipped",
}


def annotate_source_health(
    source_log: list[dict[str, Any]],
    *,
    new_counts_by_source: dict[str, int] | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    annotated: list[dict[str, Any]] = []
    for entry in source_log:
        enriched = dict(entry)
        fetched_count = int(enriched.get("count") or 0)
        if new_counts_by_source is not None:
            new_count: int | None = int(new_counts_by_source.get(str(enriched.get("name")), 0))
        elif dry_run:
            new_count = fetched_count
        else:
            new_count = None

        health = source_health_status(enriched.get("status"), fetched_count, new_count)
        enriched["new_count"] = new_count
        enriched["health"] = health
        enriched["health_label"] = HEALTH_LABELS[health]
        annotated.append(enriched)
    return annotated


def source_health_status(status: object, fetched_count: int, new_count: int | None) -> str:
    if status == "error":
        return "failed"
    if status in {"disabled", "manual", "skipped", "skipped_max_items"}:
        return "skipped"
    if new_count is not None and new_count == 0:
        return "no_new_items"
    if fetched_count == 0:
        return "no_new_items"
    return "succeeded"


def summarize_source_health(source_log: list[dict[str, Any]]) -> dict[str, int]:
    health_counts = Counter(str(entry.get("health", "unknown")) for entry in source_log)
    return {
        "sources_total": len(source_log),
        "sources_succeeded": health_counts.get("succeeded", 0),
        "sources_failed": health_counts.get("failed", 0),
        "sources_no_new_items": health_counts.get("no_new_items", 0),
        "sources_skipped": health_counts.get("skipped", 0),
        "items_fetched": sum(int(entry.get("count") or 0) for entry in source_log),
        "items_new": sum(int(entry.get("new_count") or 0) for entry in source_log if entry.get("new_count") is not None),
    }


def log_source_health(logger: logging.Logger, source_log: list[dict[str, Any]]) -> None:
    summary = summarize_source_health(source_log)
    logger.info(
        "Source health: total=%s succeeded=%s failed=%s no_new_items=%s skipped=%s fetched_items=%s new_items=%s",
        summary["sources_total"],
        summary["sources_succeeded"],
        summary["sources_failed"],
        summary["sources_no_new_items"],
        summary["sources_skipped"],
        summary["items_fetched"],
        summary["items_new"],
    )
    for entry in source_log:
        if entry.get("health") == "failed":
            logger.warning("Source failed: %s - %s", entry.get("name"), entry.get("error"))
