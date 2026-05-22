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

    latest_html = ""
    if brief_pages:
        latest_path = site_path / brief_pages[0][1]
        latest_html = extract_body(latest_path.read_text(encoding="utf-8"))

    followups_page = site_path / "followups.html"
    index_path = site_path / "index.html"
    index_path.write_text(
        render_index(brief_pages, latest_html=latest_html, has_followups=followups_page.exists()),
        encoding="utf-8",
    )
    LOGGER.info("Static site generated: index=%s pages=%s", index_path, len(brief_pages))
    return index_path


def extract_date_from_brief_name(name: str) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return match.group(1) if match else name


def extract_body(html_text: str) -> str:
    match = re.search(r"<body[^>]*>(.*)</body>", html_text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else html_text


def render_index(brief_pages: list[tuple[str, str]], *, latest_html: str = "", has_followups: bool = False) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if brief_pages:
        links = "\n".join(
            f'<li><a href="{html.escape(page_name)}">Kiribati Daily Macro and Policy Monitor - {html.escape(label)}</a></li>'
            for label, page_name in brief_pages
        )
    else:
        links = "<li>No daily brief pages have been generated yet.</li>"
    archive = f"""
  <details>
    <summary>Archive</summary>
    <ul>
      {links}
    </ul>
  </details>
"""
    followups_link = (
        """
  <p><a href="followups.html">Daily follow-up dashboard</a></p>
"""
        if has_followups
        else ""
    )
    latest_section = latest_html or "<p>No daily brief pages have been generated yet.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kiribati Macro Monitor</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.55; color: #1f2933; max-width: 860px; margin: 24px auto; padding: 0 16px; }}
    h1 {{ font-size: 1.7rem; }}
    h2 {{ font-size: 1.1rem; margin-top: 1.4rem; }}
    a {{ color: #0b5cad; }}
    .warning {{ background: #fff7e6; border-left: 4px solid #d97706; padding: 12px 14px; margin: 18px 0; }}
    li {{ margin: 0.5rem 0; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.88rem; }}
    th, td {{ border-bottom: 1px solid #e4e7eb; padding: 6px; text-align: left; vertical-align: top; }}
    summary {{ cursor: pointer; font-weight: 600; margin-top: 20px; }}
  </style>
</head>
<body>
  <h1>Kiribati Macro Monitor</h1>
  <p>Public-source daily brief archive. Generated {generated_at}.</p>
  <div class="warning">
    <strong>Public-source-only:</strong> Do not publish confidential, internal, mission-sensitive, or non-public material here unless access controls are approved by your organization.
  </div>
  {followups_link}
  {latest_section}
  {archive}
</body>
</html>
"""
