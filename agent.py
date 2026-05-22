from __future__ import annotations

import html as html_lib
import os
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from openai import OpenAI

DEFAULT_SOURCE_URL = "https://aperaltaalva.github.io/Kiribati"
DEFAULT_OUTPUT_PATH = Path("site") / "followups.html"


def fetch_page_html(source: str) -> str:
    if source.startswith(("http://", "https://")):
        response = requests.get(source, timeout=30)
        response.raise_for_status()
        return response.text

    source_path = Path(source)
    return source_path.read_text(encoding="utf-8")


def extract_follow_up_tasks(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")

    headers = soup.find_all(["h2", "h3"])
    target_header = None
    for header in headers:
        text = header.get_text(" ", strip=True)
        if "H. Items requiring follow-up" in text or "Items requiring follow-up" in text:
            target_header = header
            break

    if not target_header:
        return []

    tasks = []
    for sibling in target_header.find_next_siblings():
        if sibling.name in ["h1", "h2", "h3"]:
            break
        if sibling.name in ["ul", "ol"]:
            tasks.extend(li.get_text(" ", strip=True) for li in sibling.find_all("li"))
        elif sibling.name == "p":
            tasks.append(sibling.get_text(" ", strip=True))
        elif sibling.name in ["div", "section"]:
            tasks.extend(li.get_text(" ", strip=True) for li in sibling.find_all("li"))
            tasks.extend(p.get_text(" ", strip=True) for p in sibling.find_all("p"))

    return [task for task in tasks if task]

# 1. Scrape only the "H. Items requiring follow-up" section
def fetch_follow_up_tasks(source: str | None = None):
    source = source or os.getenv("FOLLOWUPS_SOURCE_PATH") or os.getenv("FOLLOWUPS_SOURCE_URL") or DEFAULT_SOURCE_URL
    try:
        html_text = fetch_page_html(source)
    except (OSError, requests.exceptions.RequestException) as e:
        print(f"Error fetching follow-up source: {e}")
        return None

    tasks = extract_follow_up_tasks(html_text)
    if not tasks:
        print(f"Could not find the follow-up section in {source}.")
        return None

    return "\n".join([f"- {task}" for task in tasks])


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

# 2. Ask OpenAI to build a beautiful HTML dashboard with the follow-up results
def generate_html_page(tasks_text):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Missing OpenAI API Key.")
        return None

    client = OpenAI(api_key=api_key)
    model = os.getenv("FOLLOWUPS_MODEL") or os.getenv("MODEL") or "gpt-4o"
    
    prompt = f"""
    You are an expert Pacific Macro-Policy & Research Assistant. 
    Below are the pending follow-up items extracted from today's Kiribati Macro Monitor:
    
    ---
    {tasks_text}
    ---
    
    Build a self-contained, highly professional HTML webpage that organizes and "follows up" on these items.
    
    Design Guidelines:
    - Use Tailwind CSS via the CDN link: <script src="https://cdn.tailwindcss.com"></script>
    - Ensure it is a modern, responsive card-based layout.
    - Provide a header: "Kiribati Monitor: Daily Follow-up Dashboard"
    - Organize the tasks clearly by country or topic (e.g., Fiji, Tuvalu, Vanuatu, Regional Climate Financing, Geopolitics).
    - For each item, display:
      1. The original task/headline.
      2. A "Why it matters" analytical brief explaining regional impacts or policy implications.
      3. "Actionable Next Steps" (concrete search strategies, specific agency resources to watch, or suggested follow-up tasks).
    - Return ONLY the raw HTML code, starting with <!DOCTYPE html> and ending with </html>. Do not wrap the code in markdown formatting like ```html.
    """
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a web designer and policy researcher. You output ONLY valid, beautifully formatted, self-contained HTML code using Tailwind CSS."},
                {"role": "user", "content": prompt}
            ]
        )
        return clean_html_response(response.choices[0].message.content or "")
    except Exception as e:
        print(f"Error invoking LLM: {e}")
        return None


def render_fallback_html_page(tasks_text: str) -> str:
    task_items = []
    for line in tasks_text.splitlines():
        task = line.removeprefix("- ").strip()
        if task:
            task_items.append(f"<li>{html_lib.escape(task)}</li>")
    items = "\n".join(task_items) or "<li>No follow-up items were found.</li>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kiribati Monitor: Daily Follow-up Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.55; color: #1f2933; max-width: 900px; margin: 32px auto; padding: 0 20px; }}
    h1 {{ font-size: 1.8rem; }}
    li {{ margin-bottom: 0.75rem; }}
  </style>
</head>
<body>
  <h1>Kiribati Monitor: Daily Follow-up Dashboard</h1>
  <p>The AI-generated dashboard was unavailable, so the extracted follow-up items are listed below.</p>
  <ul>
    {items}
  </ul>
</body>
</html>
"""


def main() -> int:
    print("Extracting follow-ups from Kiribati Macro Monitor...")
    raw_tasks = fetch_follow_up_tasks()
    
    if raw_tasks:
        print("Generating follow-up HTML webpage...")
        html_content = generate_html_page(raw_tasks)
        
        if not html_content:
            print("Failed to generate AI HTML; writing fallback dashboard.")
            html_content = render_fallback_html_page(raw_tasks)

        output_path = Path(os.getenv("FOLLOWUPS_OUTPUT_PATH", str(DEFAULT_OUTPUT_PATH)))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html_content, encoding="utf-8")
        print(f"Successfully created '{output_path}'.")
        return 0
    else:
        print("No tasks found or failed to parse.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
