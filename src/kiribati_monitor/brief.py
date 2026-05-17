from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import markdown as markdown_lib
from jinja2 import Environment, PackageLoader, select_autoescape

from .models import StoredArticle

LOGGER = logging.getLogger(__name__)

SECTION_RULES = {
    "Fiscal and macro": {"fiscal", "inflation_imports", "banking_payments"},
    "Fisheries and external sector": {"fisheries", "external_sector"},
    "Climate/disaster and infrastructure": {"climate_disaster"},
    "Donor/project pipeline": {"donor_project"},
    "Politics/governance/geopolitics": {"politics_governance", "geopolitics", "social"},
}


def generate_daily_brief(
    articles: list[StoredArticle],
    *,
    output_dir: str | Path = "output",
    run_date: date | None = None,
    source_log: list[dict[str, Any]] | None = None,
) -> tuple[Path, Path]:
    run_date = run_date or datetime.now(timezone.utc).date()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    context = build_brief_context(articles, run_date=run_date, source_log=source_log or [])
    env = Environment(
        loader=PackageLoader("kiribati_monitor", "templates"),
        autoescape=select_autoescape(),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("daily_brief.md.j2")
    markdown_text = template.render(**context)

    md_path = output_path / f"daily_brief_{run_date.isoformat()}.md"
    html_path = output_path / f"daily_brief_{run_date.isoformat()}.html"
    md_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(markdown_to_html(markdown_text, title=context["title"]), encoding="utf-8")
    return md_path, html_path


def generate_email_brief(
    articles: list[StoredArticle],
    *,
    output_dir: str | Path = "output",
    run_date: date | None = None,
    source_log: list[dict[str, Any]] | None = None,
) -> Path:
    run_date = run_date or datetime.now(timezone.utc).date()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    context = build_brief_context(articles, run_date=run_date, source_log=source_log or [])
    env = Environment(
        loader=PackageLoader("kiribati_monitor", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("email_brief.html.j2")
    html_path = output_path / f"daily_email_{run_date.isoformat()}.html"
    html_path.write_text(template.render(**context), encoding="utf-8")
    return html_path


def build_brief_context(
    articles: list[StoredArticle],
    *,
    run_date: date,
    source_log: list[dict[str, Any]],
) -> dict[str, Any]:
    classified = [item for item in articles if item.classification is not None]
    prioritized = sorted(classified, key=priority_score, reverse=True)
    included = [item for item in prioritized if should_include(item)]
    top_signals = included[:5]

    sections: dict[str, list[StoredArticle]] = defaultdict(list)
    used_top_ids = {item.id for item in top_signals}
    for item in included:
        if item.id in used_top_ids:
            continue
        assert item.classification is not None
        placed = False
        for section_name, topics in SECTION_RULES.items():
            if item.classification.topic in topics:
                sections[section_name].append(item)
                placed = True
                break
        if not placed and item.article.source_type in {"statistics", "data"}:
            sections["Data watch"].append(item)
        elif not placed:
            sections["Fiscal and macro"].append(item)

    for item in classified:
        if item.article.source_type in {"statistics", "data"} and item not in sections["Data watch"]:
            sections["Data watch"].append(item)

    follow_ups = [
        item
        for item in included
        if item.classification
        and item.classification.suggested_follow_up.strip()
        and (item.classification.macro_relevance >= 2 or item.classification.urgency >= 2)
    ][:8]

    return {
        "title": f"Kiribati Daily Macro and Policy Monitor - {run_date.isoformat()}",
        "display_title": f"Kiribati Daily Macro and Policy Monitor — {run_date.isoformat()}",
        "run_date": run_date,
        "confidence_caveats_line": build_confidence_caveats_line(classified, source_log),
        "top_signals": top_signals,
        "sections": {
            "Fiscal and macro": sections.get("Fiscal and macro", []),
            "Fisheries and external sector": sections.get("Fisheries and external sector", []),
            "Climate/disaster and infrastructure": sections.get("Climate/disaster and infrastructure", []),
            "Donor/project pipeline": sections.get("Donor/project pipeline", []),
            "Politics/governance/geopolitics": sections.get("Politics/governance/geopolitics", []),
            "Data watch": dedupe_stored(sections.get("Data watch", [])),
        },
        "follow_ups": follow_ups,
        "source_log": source_log,
        "source_health_summary": build_source_health_summary(source_log),
        "has_major_items": bool(top_signals or included),
    }


def should_include(item: StoredArticle) -> bool:
    classification = item.classification
    if classification is None:
        return False
    return (
        classification.include_in_daily_brief
        or classification.macro_relevance >= 2
        or classification.urgency >= 2
        or item.article.source_type in {"official", "statistics", "multilateral", "donor"}
        and item.article.source_importance >= 4
    )


def priority_score(item: StoredArticle) -> tuple[int, int, str]:
    classification = item.classification
    if classification is None:
        return (0, 0, "")
    timestamp = item.article.published_date or item.article.fetched_at
    return (priority_points(item), int(timestamp.timestamp()), timestamp.isoformat())


def priority_points(item: StoredArticle) -> int:
    classification = item.classification
    if classification is None:
        return 0

    article = item.article
    source_name = article.source_name.lower()
    tags = {tag.lower() for tag in article.source_tags}
    score = 0
    score += classification.macro_relevance * 100
    score += classification.urgency * 45
    score += article.source_importance * 12

    if article.source_type == "official":
        score += 45
    elif article.source_type == "statistics":
        score += 48
    elif article.source_type == "multilateral":
        score += 40
    elif article.source_type == "donor":
        score += 28
    elif article.source_type == "regional":
        score += 18
    elif article.source_type == "media":
        score += 5

    if any(token in source_name for token in ("imf", "world bank", "adb", "asian development bank")):
        score += 45
    if any(token in source_name for token in ("finance", "economic development", "statistics")):
        score += 32

    if classification.topic in {"fiscal", "banking_payments", "inflation_imports"}:
        score += 36
    elif classification.topic in {"fisheries", "external_sector"}:
        score += 34
    elif classification.topic == "climate_disaster":
        score += 32
    elif classification.topic == "donor_project":
        score += 18

    if tags.intersection({"fiscal", "budget", "finance", "macro", "statistics", "inflation"}):
        score += 24
    if tags.intersection({"fisheries", "tuna", "vessel_days", "external_sector"}):
        score += 24
    if tags.intersection({"imf", "world_bank", "adb"}):
        score += 24
    if tags.intersection({"climate", "disaster", "resilience", "infrastructure"}):
        score += 20

    if classification.confidence == "high":
        score += 8
    elif classification.confidence == "medium":
        score += 4

    return score


def build_confidence_caveats_line(
    classified: list[StoredArticle],
    source_log: list[dict[str, Any]],
) -> str:
    counts = Counter(item.classification.confidence for item in classified if item.classification)
    failed_sources = sum(1 for entry in source_log if entry.get("health") == "failed" or entry.get("status") == "error")
    no_new_sources = sum(1 for entry in source_log if entry.get("health") == "no_new_items")
    low_count = counts.get("low", 0)
    if not classified:
        return (
            "Confidence and caveats: no articles were classified in this run. "
            "Public websites may have blocked, timed out, or produced no new items; check the source health report."
        )

    caveats = [
        f"{counts.get('high', 0)} high",
        f"{counts.get('medium', 0)} medium",
        f"{low_count} low confidence classifications",
    ]
    source_note = f"{failed_sources} source failures"
    if no_new_sources:
        source_note += f" and {no_new_sources} sources with no new items"
    if low_count:
        return (
            "Confidence and caveats: "
            + ", ".join(caveats)
            + f"; {source_note}. Low-confidence items may be heuristic fallbacks or based on thin public text."
        )
    return (
        "Confidence and caveats: "
        + ", ".join(caveats)
        + f"; {source_note}. Verify linked sources before using items for mission decisions."
    )


def build_source_health_summary(source_log: list[dict[str, Any]]) -> dict[str, int]:
    health_counts = Counter(str(entry.get("health", "unknown")) for entry in source_log)
    return {
        "total": len(source_log),
        "succeeded": health_counts.get("succeeded", 0),
        "failed": health_counts.get("failed", 0),
        "no_new_items": health_counts.get("no_new_items", 0),
        "skipped": health_counts.get("skipped", 0),
        "fetched": sum(int(entry.get("count") or 0) for entry in source_log),
        "new": sum(int(entry.get("new_count") or 0) for entry in source_log if entry.get("new_count") is not None),
    }


def dedupe_stored(items: list[StoredArticle]) -> list[StoredArticle]:
    seen: set[int] = set()
    deduped: list[StoredArticle] = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        deduped.append(item)
    return deduped


def markdown_to_html(markdown_text: str, *, title: str) -> str:
    body = markdown_lib.markdown(markdown_text, extensions=["extra", "sane_lists"])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.55; color: #1f2933; max-width: 920px; margin: 32px auto; padding: 0 20px; }}
    h1 {{ font-size: 1.8rem; margin-bottom: 0.75rem; }}
    h2 {{ font-size: 1.18rem; border-bottom: 1px solid #d9e2ec; padding-bottom: 0.25rem; margin-top: 1.8rem; }}
    a {{ color: #0b5cad; }}
    li {{ margin-bottom: 0.55rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
    th, td {{ border-bottom: 1px solid #e4e7eb; padding: 6px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f5f7fa; }}
    .muted {{ color: #52606d; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""
