import os
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# 1. Scrape only the "H. Items requiring follow-up" section
def fetch_follow_up_tasks():
    url = "https://aperaltaalva.github.io/Kiribati"
    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching website: {e}")
        return None
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Locate the heading for Section H
    headers = soup.find_all(['h2', 'h3'])
    target_header = None
    for h in headers:
        text = h.get_text()
        if "H. Items requiring follow-up" in text or "Items requiring follow-up" in text:
            target_header = h
            break
            
    if not target_header:
        print("Could not find the follow-up section on the page.")
        return None
        
    # Gather all list items or paragraphs until the next major section
    tasks = []
    current_element = target_header.find_next()
    while current_element and current_element.name not in ['h1', 'h2', 'h3']:
        if current_element.name == 'ul':
            for li in current_element.find_all('li'):
                tasks.append(li.get_text().strip())
        elif current_element.name == 'p':
            tasks.append(current_element.get_text().strip())
        current_element = current_element.find_next()
        
    return "\n".join([f"- {t}" for t in tasks if t])

# 2. Ask OpenAI to build a beautiful HTML dashboard with the follow-up results
def generate_html_page(tasks_text):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Missing OpenAI API Key.")
        return None

    client = OpenAI(api_key=api_key)
    
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
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a web designer and policy researcher. You output ONLY valid, beautifully formatted, self-contained HTML code using Tailwind CSS."},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error invoking LLM: {e}")
        return None

if __name__ == "__main__":
    print("Extracting follow-ups from Kiribati Macro Monitor...")
    raw_tasks = fetch_follow_up_tasks()
    
    if raw_tasks:
        print("Generating follow-up HTML webpage...")
        html_content = generate_html_page(raw_tasks)
        
        if html_content:
            # Ensure the "site" directory exists
            os.makedirs("site", exist_ok=True)
            
            # Save directly inside the site folder
            with open("site/followups.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            print("Successfully created 'site/followups.html'.")
        else:
            print("Failed to generate HTML.")
    else:
        print("No tasks found or failed to parse.")
