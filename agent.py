from __future__ import annotations

import html as html_lib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel, Field

DEFAULT_SOURCE_URL = "https://aperaltaalva.github.io/Kiribati"
DEFAULT_OUTPUT_PATH = Path("site") / "followups.html"
DEFAULT_FOLLOWUPS_MODEL = "gpt-5.5"


@dataclass(frozen=True)
class FollowUpTask:
    title: str
    action: str
    source_url: str | None = None


class EvidenceLink(BaseModel):
    title: str = Field(description="Human-readable title of the source.")
    url: str = Field(description="Public URL used as evidence.")
    publisher: str = Field(description="Publisher, agency, ministry, or organization.")
    date: str = Field(description="Publication or access date if known; otherwise 'unknown'.")
    finding: str = Field(description="The specific fact supported by this source.")


class InvestigatedFollowUp(BaseModel):
    original_title: str
    original_action: str
    status: Literal[
        "answered",
        "partly answered",
        "not found",
        "conflicting evidence",
        "blocked",
    ]
    result: str = Field(description="Direct answer to the original follow-up action.")
    macro_policy_implication: str = Field(description="Short macro-policy implication, or 'None identified'.")
    evidence: list[EvidenceLink] = Field(default_factory=list)
    searched_queries: list[str] = Field(default_factory=list)
    remaining_gap: str = Field(description="Only the unresolved evidence gap, not suggested next steps.")


class FollowUpResearchReport(BaseModel):
    executive_summary: str
    items: list[InvestigatedFollowUp]


def fetch_page_html(source: str) -> str:
    if source.startswith(("http://", "https://")):
        response = requests.get(source, timeout=30)
        response.raise_for_status()
        return response.text

    return Path(source).read_text(encoding="utf-8")


def extract_follow_up_items(html_text: str) -> list[FollowUpTask]:
    soup = BeautifulSoup(html_text, "html.parser")

    target_header = None
    for header in soup.find_all(["h2", "h3"]):
        text = header.get_text(" ", strip=True)
        if "H. Items requiring follow-up" in text or "Items requiring follow-up" in text:
            target_header = header
            break

    if not target_header:
        return []

    tasks: list[FollowUpTask] = []
    for sibling in target_header.find_next_siblings():
        if sibling.name in ["h1", "h2", "h3"]:
            break
        if sibling.name in ["ul", "ol"]:
            tasks.extend(parse_task_item(li) for li in sibling.find_all("li"))
        elif sibling.name == "p":
            task_text = sibling.get_text(" ", strip=True)
            if task_text:
                tasks.append(FollowUpTask(title=task_text, action=task_text))
        elif sibling.name in ["div", "section"]:
            tasks.extend(parse_task_item(li) for li in sibling.find_all("li"))
            for paragraph in sibling.find_all("p"):
                task_text = paragraph.get_text(" ", strip=True)
                if task_text:
                    tasks.append(FollowUpTask(title=task_text, action=task_text))

    return [task for task in tasks if task.action.strip()]


def parse_task_item(item) -> FollowUpTask:
    text = item.get_text(" ", strip=True)
    link = item.find("a")
    if not link:
        return FollowUpTask(title=text, action=text)

    title = link.get_text(" ", strip=True)
    source_url = link.get("href")
    action = text
    if title and text.startswith(title):
        action = text[len(title) :].lstrip(" -:;")
    return FollowUpTask(title=title or text, action=action or text, source_url=source_url)


def extract_follow_up_tasks(html_text: str) -> list[str]:
    return [task.action for task in extract_follow_up_items(html_text)]


def fetch_follow_up_items(source: str | None = None) -> list[FollowUpTask] | None:
    source = source or os.getenv("FOLLOWUPS_SOURCE_PATH") or os.getenv("FOLLOWUPS_SOURCE_URL") or DEFAULT_SOURCE_URL
    try:
        html_text = fetch_page_html(source)
    except (OSError, requests.exceptions.RequestException) as exc:
        print(f"Error fetching follow-up source: {exc}")
        return None

    tasks = extract_follow_up_items(html_text)
    if not tasks:
        print(f"Could not find the follow-up section in {source}.")
        return None
    return tasks


def fetch_follow_up_tasks(source: str | None = None) -> str | None:
    tasks = fetch_follow_up_items(source)
    if not tasks:
        return None
    return "\n".join(f"- {task.action}" for task in tasks)


def investigate_follow_ups(tasks: list[FollowUpTask]) -> FollowUpResearchReport | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Missing OpenAI API Key.")
        return None

    client = OpenAI(api_key=api_key)
    model = get_followups_model()
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tasks": [asdict(task) for task in tasks],
    }

    try:
        response = client.responses.parse(
            model=model,
            instructions=build_research_instructions(),
            input=json.dumps(payload, ensure_ascii=False, indent=2),
            tools=[
                {
                    "type": "web_search",
                    "search_context_size": os.getenv("FOLLOWUPS_SEARCH_CONTEXT_SIZE", "medium"),
                }
            ],
            tool_choice="required",
            include=["web_search_call.action.sources"],
            max_tool_calls=int(os.getenv("FOLLOWUPS_MAX_TOOL_CALLS", "20")),
            max_output_tokens=int(os.getenv("FOLLOWUPS_MAX_OUTPUT_TOKENS", "9000")),
            text_format=FollowUpResearchReport,
        )
        return response.output_parsed
    except Exception as exc:
        print(f"Error running follow-up research: {exc}")
        return None


def get_followups_model() -> str:
    return os.getenv("FOLLOWUPS_MODEL") or os.getenv("MODEL") or DEFAULT_FOLLOWUPS_MODEL


def build_research_instructions() -> str:
    return """
You are a Pacific macro-policy research agent.

Your job is to execute the follow-up actions from a daily Kiribati/Pacific public-source newsletter.
For every item:
- Use web search. Treat the task as incomplete until you have searched.
- Prefer official government, donor, multilateral, regulator, or project pages over media summaries.
- If an original source URL is supplied, use it as context, then search for official confirmation.
- Answer what the follow-up action asked for. Do not produce a new to-do list.
- If evidence is absent, say "not found" and explain what was searched.
- If pages are blocked, conflicting, or too thin, say so directly.
- Include public evidence URLs for every substantive claim.
- Keep each result concise enough for a mission workflow.
- Do not use a section named "Actionable Next Steps".
- Do not tell the reader to monitor, track, or check something unless you are describing what you already checked.
""".strip()


def render_research_html_page(report: FollowUpResearchReport, *, model: str | None = None) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    model_text = model or get_followups_model()
    item_cards = "\n".join(render_result_card(index, item) for index, item in enumerate(report.items, start=1))
    status_counts = build_status_counts(report.items)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kiribati Monitor: Follow-up Investigation Results</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17202a;
      --muted: #52606d;
      --line: #d9e2ec;
      --paper: #f5f7fa;
      --panel: #ffffff;
      --blue: #0b5cad;
      --green: #0e7c66;
      --amber: #b35c00;
      --red: #b42318;
    }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.55;
    }}
    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }}
    main, .wrap {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 0 18px;
    }}
    header .wrap {{
      padding-top: 28px;
      padding-bottom: 22px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(1.7rem, 3vw, 2.4rem);
      line-height: 1.15;
    }}
    h2 {{
      margin: 0 0 8px;
      font-size: 1.08rem;
    }}
    a {{
      color: var(--blue);
    }}
    .meta, .muted {{
      color: var(--muted);
    }}
    .summary {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }}
    .summary .wrap {{
      padding-top: 18px;
      padding-bottom: 18px;
      display: grid;
      gap: 16px;
      grid-template-columns: 1fr;
    }}
    .counts {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .pill {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 10px;
      background: #ffffff;
      font-size: 0.86rem;
    }}
    main {{
      padding-top: 22px;
      padding-bottom: 36px;
    }}
    article {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      margin-bottom: 16px;
    }}
    .card-top {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }}
    .status {{
      border-radius: 999px;
      padding: 4px 10px;
      color: #ffffff;
      font-size: 0.78rem;
      white-space: nowrap;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .answered {{ background: var(--green); }}
    .partly {{ background: var(--amber); }}
    .not-found, .blocked, .conflicting {{ background: var(--red); }}
    .label {{
      margin-top: 14px;
      margin-bottom: 4px;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }}
    ul {{
      padding-left: 1.2rem;
    }}
    li {{
      margin: 0.35rem 0;
    }}
    .queries {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding: 0;
      list-style: none;
    }}
    .queries li {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 9px;
      color: var(--muted);
      font-size: 0.82rem;
      margin: 0;
    }}
    @media (max-width: 640px) {{
      .card-top {{
        display: block;
      }}
      .status {{
        display: inline-block;
        margin-top: 10px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <p class="meta">Generated {escape(generated_at)} with {escape(model_text)}</p>
      <h1>Kiribati Monitor: Follow-up Investigation Results</h1>
    </div>
  </header>
  <section class="summary">
    <div class="wrap">
      <div>
        <h2>Research Summary</h2>
        <p>{escape(report.executive_summary)}</p>
      </div>
      <div class="counts">{status_counts}</div>
    </div>
  </section>
  <main>
    {item_cards or '<p>No investigated follow-up items were returned.</p>'}
  </main>
</body>
</html>
"""


def render_result_card(index: int, item: InvestigatedFollowUp) -> str:
    evidence = "\n".join(render_evidence_link(link) for link in item.evidence)
    if not evidence:
        evidence = "<li>No public evidence link returned by the research pass.</li>"
    queries = "\n".join(f"<li>{escape(query)}</li>" for query in item.searched_queries)
    if not queries:
        queries = "<li>No search query metadata returned.</li>"
    status_class = status_css_class(item.status)

    return f"""<article>
  <div class="card-top">
    <div>
      <h2>{index}. {escape(item.original_title)}</h2>
      <p class="muted">{escape(item.original_action)}</p>
    </div>
    <span class="status {status_class}">{escape(item.status)}</span>
  </div>

  <div class="label">Investigation Result</div>
  <p>{escape(item.result)}</p>

  <div class="label">Macro-Policy Implication</div>
  <p>{escape(item.macro_policy_implication)}</p>

  <div class="label">Evidence Checked</div>
  <ul>
    {evidence}
  </ul>

  <div class="label">Searches Run</div>
  <ul class="queries">
    {queries}
  </ul>

  <div class="label">Remaining Evidence Gap</div>
  <p>{escape(item.remaining_gap)}</p>
</article>"""


def render_evidence_link(link: EvidenceLink) -> str:
    source = f"{link.publisher}; {link.date}" if link.date else link.publisher
    if link.url.startswith(("http://", "https://")):
        title = f'<a href="{escape_attr(link.url)}">{escape(link.title)}</a>'
    else:
        title = escape(link.title)
    return f"<li>{title} <span class=\"muted\">({escape(source)})</span>: {escape(link.finding)}</li>"


def build_status_counts(items: list[InvestigatedFollowUp]) -> str:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    if not counts:
        return '<span class="pill">0 items</span>'
    return "\n".join(f'<span class="pill">{escape(status)}: {count}</span>' for status, count in sorted(counts.items()))


def status_css_class(status: str) -> str:
    if status == "answered":
        return "answered"
    if status == "partly answered":
        return "partly"
    if status == "not found":
        return "not-found"
    if status == "conflicting evidence":
        return "conflicting"
    if status == "blocked":
        return "blocked"
    return "blocked"


def render_unavailable_html_page(tasks: list[FollowUpTask]) -> str:
    task_items = "\n".join(
        f"<li><strong>{escape(task.title)}</strong>: {escape(task.action)}</li>" for task in tasks
    )
    task_items = task_items or "<li>No follow-up items were found.</li>"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kiribati Monitor: Follow-up Investigation Unavailable</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.55; color: #1f2933; max-width: 900px; margin: 32px auto; padding: 0 20px; }}
    h1 {{ font-size: 1.8rem; }}
    li {{ margin-bottom: 0.75rem; }}
    .warning {{ background: #fff7e6; border-left: 4px solid #d97706; padding: 12px 14px; margin: 18px 0; }}
  </style>
</head>
<body>
  <h1>Kiribati Monitor: Follow-up Investigation Unavailable</h1>
  <p>Generated {escape(generated_at)}.</p>
  <div class="warning">
    The follow-up research agent could not run. The items below were extracted but not investigated.
  </div>
  <ul>
    {task_items}
  </ul>
</body>
</html>
"""


def render_fallback_html_page(tasks_text: str) -> str:
    tasks = [FollowUpTask(title=line.removeprefix("- ").strip(), action=line.removeprefix("- ").strip()) for line in tasks_text.splitlines()]
    return render_unavailable_html_page([task for task in tasks if task.action])


def clean_html_response(html_content: str) -> str:
    cleaned = html_content.strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def escape(value: str) -> str:
    return html_lib.escape(value or "", quote=False)


def escape_attr(value: str) -> str:
    return html_lib.escape(value or "", quote=True)


def main() -> int:
    print("Extracting follow-ups from Kiribati Macro Monitor...")
    tasks = fetch_follow_up_items()

    if not tasks:
        print("No tasks found or failed to parse.")
        return 1

    print(f"Running web research for {len(tasks)} follow-up items...")
    report = investigate_follow_ups(tasks)

    if report:
        html_content = render_research_html_page(report, model=get_followups_model())
    else:
        print("Follow-up research failed; writing unavailable page.")
        html_content = render_unavailable_html_page(tasks)

    output_path = Path(os.getenv("FOLLOWUPS_OUTPUT_PATH", str(DEFAULT_OUTPUT_PATH)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
    print(f"Successfully created '{output_path}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
