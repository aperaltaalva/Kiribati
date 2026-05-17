from __future__ import annotations

import html
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def publish_static_site(
    *,
    output_dir: str | Path = "output",
    site_dir: str | Path = "site",
) -> Path:
    output_path = Path(output_dir)
    site_path = Path(site_dir)
    site_path.mkdir(parents=True, exist_ok=True)

    brief_pages: list[tuple[str, str]] = []
    for html_path in sorted(output_path.glob("daily_brief_*.html"), reverse=True):
        page_name = html_path.name
        destination = site_path / page_name
        shutil.copyfile(html_path, destination)
        brief_pages.append((extract_date_from_brief_name(page_name), page_name))

    index_path = site_path / "index.html"
    index_path.write_text(render_index(brief_pages), encoding="utf-8")
    LOGGER.info("Static site generated: index=%s pages=%s", index_path, len(brief_pages))
    return index_path


def extract_date_from_brief_name(name: str) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return match.group(1) if match else name


def render_index(brief_pages: list[tuple[str, str]]) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if brief_pages:
        links = "\n".join(
            f'<li><a href="{html.escape(page_name)}">Kiribati Daily Macro and Policy Monitor - {html.escape(label)}</a></li>'
            for label, page_name in brief_pages
        )
    else:
        links = "<li>No daily brief pages have been generated yet.</li>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kiribati Macro Monitor</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.55; color: #1f2933; max-width: 860px; margin: 32px auto; padding: 0 18px; }}
    h1 {{ font-size: 1.8rem; }}
    a {{ color: #0b5cad; }}
    .warning {{ background: #fff7e6; border-left: 4px solid #d97706; padding: 12px 14px; margin: 18px 0; }}
    li {{ margin: 0.5rem 0; }}
  </style>
</head>
<body>
  <h1>Kiribati Macro Monitor</h1>
  <p>Public-source daily brief archive. Generated {generated_at}.</p>
  <div class="warning">
    <strong>Public-source-only:</strong> Do not publish confidential, internal, mission-sensitive, or non-public material here unless access controls are approved by your organization.
  </div>
  <h2>Daily Briefs</h2>
  <ul>
    {links}
  </ul>
</body>
</html>
"""
