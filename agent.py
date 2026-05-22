import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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
        
    # Gather all list items or paragraphs until the next major section (Section I)
    tasks = []
    current_element = target_header.find_next()
    while current_element and current_element.name not in ['h1', 'h2', 'h3']:
        if current_element.name == 'ul':
            for li in current_element.find_all('li'):
                # Clean up nested links/formatting but keep text
                tasks.append(li.get_text().strip())
        elif current_element.name == 'p':
            tasks.append(current_element.get_text().strip())
        current_element = current_element.find_next()
        
    return "\n".join([f"- {t}" for t in tasks if t])

# 2. Let the Agent process and "follow up" on these tasks
def process_tasks_with_agent(tasks_text):
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
    
    Your job is to "follow up" on these items by:
    1. Organizing them by country/sector (Fiji, Tuvalu, Vanuatu, etc.).
    2. Explaining why each item matters and the critical risks/opportunities to monitor.
    3. Drafting a concrete, immediate action plan for each (e.g., specific search queries to run, official agencies to check, or emails to draft).
    
    Format your response as a polished, highly professional markdown briefing.
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a professional policy researcher and task tracking assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error invoking LLM: {e}")
        return None

# 3. Email the results to you
def send_email(report_content):
    sender = os.getenv("SENDER_EMAIL")
    password = os.getenv("SENDER_PASSWORD") # Remember to use an App Password if using Gmail
    receiver = os.getenv("RECEIVER_EMAIL")
    
    if not all([sender, password, receiver]):
        print("Email configuration environment variables are missing.")
        return

    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = receiver
    msg['Subject'] = "Daily Kiribati Monitor Follow-up Report"
    
    msg.attach(MIMEText(report_content, 'plain'))
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())
        server.quit()
        print("Email report dispatched successfully.")
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == "__main__":
    print("Extracting follow-ups from Kiribati Macro Monitor...")
    raw_tasks = fetch_follow_up_tasks()
    
    if raw_tasks:
        print(f"Tasks extracted:\n{raw_tasks}\n")
        print("Running follow-up analysis...")
        analysis = process_tasks_with_agent(raw_tasks)
        
        if analysis:
            print("Mailing the analysis...")
            send_email(analysis)
        else:
            print("Failed to generate analysis.")
    else:
        print("No tasks found or failed to parse.")
