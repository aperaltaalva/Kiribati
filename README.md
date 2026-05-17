# Kiribati Macro Monitor

`kiribati-macro-monitor` is a daily public-source monitoring agent for Kiribati, designed for an IMF mission-chief workflow. It fetches official, multilateral, donor, regional, media, and data updates; deduplicates them in SQLite; classifies macro relevance with the OpenAI Responses API; and writes concise Markdown/HTML briefs plus a GitHub Pages website.

## Quick Start

Use Python 3.11 or newer. If your machine's `python` points to an older interpreter, replace `python` below with `python3.11`, `python3.12`, or another 3.11+ executable.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env` and add at least:

```bash
OPENAI_API_KEY=sk-...
MODEL=gpt-5.4-mini
```

Run the monitor:

```bash
python -m kiribati_monitor.run_daily --since-hours 36
```

Dry run without persistent storage:

```bash
python -m kiribati_monitor.run_daily --dry-run --max-items 10
```

Generated briefs are written to `output/daily_brief_YYYY-MM-DD.md`, `output/daily_brief_YYYY-MM-DD.html`, and `output/daily_email_YYYY-MM-DD.html`. The scheduled GitHub workflow also publishes a short static site.

## Configuration

Copy `.env.example` to `.env` and fill in only the values you need.

Required for OpenAI classification:

```bash
OPENAI_API_KEY=
MODEL=gpt-5.4-mini
```

Optional local settings:

```bash
DB_PATH=data/kiribati_monitor.sqlite
OUTPUT_DIR=output
SITE_DIR=site
LOG_LEVEL=INFO
REQUEST_TIMEOUT_SECONDS=20
HTTP_MAX_ATTEMPTS=3
HTTP_BACKOFF_SECONDS=1.0
```

Optional SMTP email:

```bash
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
EMAIL_FROM=
EMAIL_TO=person@example.org,team@example.org
```

Email is optional and disabled in the default GitHub workflow. If SMTP variables are missing, the app skips email and prints the generated brief paths.

## How This Looks in Practice

For normal use, you do not need to run commands every morning.

- GitHub stores the code in a private repository.
- GitHub Actions runs the code automatically every weekday morning.
- The app checks the public sources, classifies new items, and creates the daily brief.
- The latest brief is published to the repository's GitHub Pages website.
- The same output is saved in GitHub Actions as an artifact, so you can download the Markdown, web HTML, and email HTML later.
- The source health report shows which sources were checked, which failed, and which produced new items.
- The scheduled workflow filters to dated items from the last 24 hours, so old static pages do not appear as daily news.
- The website is only for public-source-only material unless your organization has approved access controls.

## Sources

Sources live in `sources.yaml`. Each source has:

```yaml
- name: Example Source
  url: https://example.org/news
  source_type: official
  fetch_method: html
  tags: [fiscal, government]
  importance: 5
  enabled: true
```

Supported `source_type` values are `official`, `statistics`, `multilateral`, `donor`, `regional`, `media`, and `data`.

Supported `fetch_method` values are `rss`, `html`, `gdelt`, and `manual`. Disabled or manual sources remain in the registry and source log but are not fetched.

The fetcher is intentionally fault-tolerant: one failing source logs a warning and does not stop the full daily run.

## One-Page Operating Guide

Daily production run:

```bash
python -m kiribati_monitor.run_daily --since-hours 24 --fresh-hours 24 --no-email --publish-site
```

Pre-flight check:

- Confirm `.env` has `OPENAI_API_KEY`, `MODEL`, `DB_PATH`, and `OUTPUT_DIR`.
- Email is optional. The default GitHub workflow uses the website and artifacts instead.
- Run `python -m pytest` after source, prompt, or code changes.
- Use `python -m kiribati_monitor.run_daily --dry-run --max-items 10` to test fetching and brief rendering without SQLite persistence, OpenAI API calls, or email.
- Use `python -m kiribati_monitor.run_daily --publish-site` only when you want to generate a local `site/` folder for public-source-only publishing.

Operational interpretation:

- The terminal summary reports fetched items, new inserts, duplicates, classified items, email status, source failures, and no-new-item sources.
- The brief includes a **confidence and caveats** line. Low confidence usually means thin source text, blocked pages, or dry-run heuristic classification.
- The **source health report** shows each source as `Succeeded`, `Failed`, `No new items`, or `Skipped`, with fetched/new counts and retry attempts.
- A source with `Failed` should be checked for changed URLs, anti-bot blocking, DNS failures, or temporary outages.
- A source with `No new items` is not necessarily broken; it may have returned no fresh articles or only already-seen URLs.

Reliability controls:

- `REQUEST_TIMEOUT_SECONDS` controls each HTTP request timeout.
- `HTTP_MAX_ATTEMPTS` controls retry attempts for timeouts, connection errors, HTTP 429, and HTTP 5xx responses.
- `HTTP_BACKOFF_SECONDS` controls exponential backoff. A value of `1.0` sleeps about 1, then 2 seconds between three attempts.
- `LOG_LEVEL=INFO` is recommended for operations; use `DEBUG` only for troubleshooting stack traces.

Daily checks for a mission workflow:

- Review Top signals first, then Items requiring follow-up.
- Open source links for any item used in mission notes.
- Treat media-only items as leads unless confirmed by official, statistical, donor, or multilateral sources.
- Do not add confidential IMF material to prompts, sources, SQLite storage, GitHub Actions logs, or generated briefs unless deployed in an approved environment.

Static website:

```bash
python -m kiribati_monitor.run_daily --since-hours 24 --fresh-hours 24 --no-email --publish-site
```

This writes `site/index.html` and one HTML page per generated daily brief. The command only creates files; it does not enable GitHub Pages or publish anything by itself.

Warning: GitHub Pages should not be used for confidential, internal, mission-sensitive, or non-public material unless access controls are approved by your organization.

## Tests

```bash
python -m pytest
```

Tests cover source-registry validation, classification-schema validation, URL deduplication, and brief generation.

## GitHub Actions

The workflow in `.github/workflows/daily.yml` supports manual runs and a weekday scheduled run. It installs dependencies, runs tests, runs:

```bash
python -m kiribati_monitor.run_daily --since-hours 24 --fresh-hours 24 --no-email --publish-site
```

It uploads the full `output/` folder as an artifact and deploys `site/` to GitHub Pages.

Set repository secrets under **Settings > Secrets and variables > Actions**:

- `OPENAI_API_KEY`
- `MODEL` optional

SMTP/email secrets are optional and are not used by the default scheduled workflow.

Keep the repository private by default. Do not put secrets in `.env`, source files, workflow files, prompts, or `sources.yaml`. Use GitHub Actions secrets for credentials.

## Limitations and Handling Rules

This project is public-source-only by default. Do not add confidential IMF material, mission notes, draft documents, or non-public contacts unless the system is deployed in an approved environment with appropriate controls.

The classifier must not invent facts beyond article text and metadata. Every daily-brief item includes a source link. Public websites can change structure or fail intermittently; the app logs failures and continues.

The `.gitignore` excludes `.env`, generated output, local databases, logs, and local virtual environments. Do not commit `.env`.
