import imaplib
import email
from email.header import decode_header
import pandas as pd
import json
import re
import time
import os
import csv
from datetime import datetime, timedelta
from dotenv import load_dotenv
from groq import Groq, RateLimitError

load_dotenv()
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

EMAIL_SUBJECT_QUERY = "god bless you"
DAYS_BACK_TO_SEARCH = 30

MODEL_LIST = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "groq/compound",
    "qwen/qwen3-32b",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant"
]
TEMPERATURE = 0.1
MAX_CONTEXT_CHARS = 10000

MIN_SALARY_LPA = 12

TARGET_DOMAINS = [
    "Software Engineering (SDE, DevOps)",
    "Artificial Intelligence (ML, DL, NLP, Vision, Agents)",
    "Data Science & Engineering (Data Scientist, Data Engineer, Analyst)",
    "Algo Trading",
    "Founder's Office",
    "Product Management (Technical PM only)"
]

FORBIDDEN_KEYWORDS = [
    "HR", "Human Resources", "Recruiter", "Talent Acquisition",
    "Sales", "Business Development", "BDA", "BDE", "Inside Sales",
    "Marketing", "Digital Marketing", "SEO", "Content Writer", "Social Media",
    "Graphic Designer", "Video Editor", "UI/UX Designer",
    "Customer Support", "Customer Success", "Operations Executive",
    "Admin", "Accountant", "Finance Executive", "Full Stack", "Backend", "Frontend", "Salesforce"
]

OUTPUT_CSV_FILE = "final_jobs_list.csv"
CHECKPOINT_FILE = "processed_email_ids.txt"

NORMAL_SLEEP_INTERVAL = 10
RATE_LIMIT_SLEEP = 60
MAX_RETRIES = 3
RECONNECT_INTERVAL = 20

current_model_index = 0

def load_processed_ids():
    if not os.path.exists(CHECKPOINT_FILE):
        return set()
    with open(CHECKPOINT_FILE, "r") as f:
        return set(line.strip() for line in f)

def mark_as_processed(email_id):
    with open(CHECKPOINT_FILE, "a") as f:
        f.write(f"{email_id}\n")

def save_job_to_csv(job_data):
    file_exists = os.path.isfile(OUTPUT_CSV_FILE)
    fieldnames = ["role", "company", "salary", "experience", "location", "match_reason", "apply_link", "email_date", "source_subject"]
    with open(OUTPUT_CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        clean_job = {k: job_data.get(k, "N/A") for k in fieldnames}
        writer.writerow(clean_job)

def get_cutoff_date(days):
    date_obj = datetime.now() - timedelta(days=days)
    return date_obj.strftime("%d-%b-%Y")

def clean_text(text):
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def get_email_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors='ignore')
    else:
        return msg.get_payload(decode=True).decode(errors='ignore')
    return ""

def connect_imap():
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")
        return mail
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        return None

def close_imap(mail):
    try:
        mail.logout()
    except:
        pass

def call_groq_with_retry(client, prompt):
    global current_model_index
    
    for attempt in range(MAX_RETRIES):
        current_model = MODEL_LIST[current_model_index]
        try:
            response = client.chat.completions.create(
                model=current_model,
                messages=[
                    {"role": "system", "content": "Extract technical jobs only. Output valid JSON. Reject sales, HR, marketing, design roles."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=TEMPERATURE
            )
            return json.loads(response.choices[0].message.content).get("jobs", [])
        except RateLimitError:
            if current_model_index < len(MODEL_LIST) - 1:
                current_model_index += 1
                next_model = MODEL_LIST[current_model_index]
                print(f"[INFO] Rate limit on {current_model.split('/')[-1]}. Switching to: {next_model.split('/')[-1]}")
                continue
            else:
                print(f"[WARNING] All models exhausted. Sleeping {RATE_LIMIT_SLEEP}s...")
                time.sleep(RATE_LIMIT_SLEEP)
                current_model_index = 0
                continue
        except Exception as e:
            print(f"[ERROR] API call failed (attempt {attempt+1}): {e}")
            time.sleep(5)
            continue
    print("[ERROR] Max retries reached. Skipping.")
    return []

def extract_jobs(client, email_body):
    truncated_body = email_body[:MAX_CONTEXT_CHARS]
    
    prompt = f"""ONLY Extract technical jobs matching these domains:
{json.dumps(TARGET_DOMAINS)}

STRICTLY REJECT roles containing: {json.dumps(FORBIDDEN_KEYWORDS)}

RULES:
- Salary >= {MIN_SALARY_LPA} LPA: Extract if domain matches
- Salary < {MIN_SALARY_LPA} LPA: Reject
- No salary: Extract only if clear tech role from recognised company
- Spam/marketing emails: Return empty

OUTPUT (JSON):
{{
  "jobs": [
    {{
      "role": "exact job title",
      "company": "company name",
      "salary": "X-Y LPA or Not Specified",
      "experience": "X-Y years or Not Specified",
      "location": "city",
      "match_reason": "which TARGET_DOMAIN matches",
      "apply_link": "URL or Not Specified"
    }}
  ]
}}

Return {{"jobs": []}} if no valid technical jobs found.

EMAIL:
{truncated_body}"""
    
    return call_groq_with_retry(client, prompt)

def fetch_email_with_retry(mail, e_id):
    for attempt in range(3):
        try:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            return email.message_from_bytes(msg_data[0][1])
        except Exception as e:
            if attempt < 2:
                print(f"[WARN] Fetch failed (attempt {attempt+1}), retrying...")
                time.sleep(2)
            else:
                raise e

def main():
    global current_model_index
    
    if not EMAIL_USER or not GROQ_API_KEY:
        print("[ERROR] Missing credentials in .env file.")
        return
    
    print("[INFO] Connecting to Gmail IMAP...")
    mail = connect_imap()
    if not mail:
        return
    
    since_date = get_cutoff_date(DAYS_BACK_TO_SEARCH)
    print(f"[INFO] Searching for emails with subject '{EMAIL_SUBJECT_QUERY}' since {since_date}...")
    search_criteria = f'(SINCE "{since_date}" SUBJECT "{EMAIL_SUBJECT_QUERY}")'
    status, messages = mail.search(None, search_criteria)
    
    if not messages[0]:
        print("[INFO] No emails found matching criteria.")
        close_imap(mail)
        return
    
    email_ids = messages[0].split()
    processed_ids = load_processed_ids()
    todo_ids = [eid for eid in email_ids if eid.decode() not in processed_ids]
    
    print(f"[INFO] Total: {len(email_ids)} | Processed: {len(processed_ids)} | Remaining: {len(todo_ids)}")
    print(f"[INFO] Available models: {', '.join([m.split('/')[-1] for m in MODEL_LIST])}")
    print(f"[INFO] Starting with: {MODEL_LIST[0].split('/')[-1]}")
    
    client = Groq(api_key=GROQ_API_KEY)
    
    for i, e_id in enumerate(todo_ids):
        e_id_str = e_id.decode()
        
        if i > 0 and i % RECONNECT_INTERVAL == 0:
            print(f"[INFO] Reconnecting to IMAP (processed {i} emails)...")
            close_imap(mail)
            time.sleep(2)
            mail = connect_imap()
            if not mail:
                print("[ERROR] Reconnection failed. Stopping.")
                return
        
        try:
            msg = fetch_email_with_retry(mail, e_id)
            subject = decode_header(msg["Subject"])[0][0]
            if isinstance(subject, bytes): 
                subject = subject.decode()
            date = msg["Date"]
            
            current_model = MODEL_LIST[current_model_index]
            print(f"[INFO] [{i+1}/{len(todo_ids)}] {subject[:40]}... (Model: {current_model.split('/')[-1]})")
            
            body = get_email_body(msg)
            cleaned = clean_text(body)
            
            if len(cleaned) > 100:
                jobs = extract_jobs(client, cleaned)
                if jobs:
                    print(f" [SUCCESS] Found {len(jobs)} jobs. Saving...")
                    for job in jobs:
                        job['email_date'] = date
                        job['source_subject'] = subject
                        save_job_to_csv(job)
                else:
                    print(" [INFO] No matching jobs.")
            
            mark_as_processed(e_id_str)
            time.sleep(NORMAL_SLEEP_INTERVAL)
            
        except Exception as e:
            print(f"[ERROR] Failed on {e_id_str}: {e}")
            mark_as_processed(e_id_str)
            continue
    
    print("[INFO] Processing complete.")
    close_imap(mail)

if __name__ == "__main__":
    main()
