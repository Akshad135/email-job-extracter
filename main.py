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

# ==========================================
#        GLOBAL CONFIGURATION
# ==========================================

# --- CREDENTIALS ---
load_dotenv()
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# --- SEARCH PARAMETERS ---
EMAIL_SUBJECT_QUERY = "god bless you"
DAYS_BACK_TO_SEARCH = 45

# --- AI MODEL SETTINGS ---
GROQ_MODEL_ID = "llama-3.1-8b-instant"
TEMPERATURE = 0.0
MAX_CONTEXT_CHARS = 6000

# --- JOB FILTERING CRITERIA ---
MIN_SALARY_LPA = 12
TARGET_ROLES = [
    "SDE", "Software Engineer", "AIML", "Machine Learning", "Deep Learning",
    "MLOps", "LLM Engineer", "Generative AI", "Agentic AI", "AI Agent",
    "Research Scientist", "Quant", "Algo Trading", "Founder's Office", 
    "Chief of Staff", "Founding Engineer"
]

# --- FILE PATHS ---
OUTPUT_CSV_FILE = "final_jobs_list.csv"
CHECKPOINT_FILE = "processed_email_ids.txt"

# --- RATE LIMITING & SAFETY ---
# Time to wait (in seconds) between every email to protect Free Tier limits
NORMAL_SLEEP_INTERVAL = 10 
# Time to wait (in seconds) if we hit a Rate Limit error before retrying
RATE_LIMIT_SLEEP = 60      
# How many times to retry an email if the API fails
MAX_RETRIES = 3            

# ==========================================
#           CORE FUNCTIONS
# ==========================================

def load_processed_ids():
    """Reads the list of already processed email IDs to enable resuming."""
    if not os.path.exists(CHECKPOINT_FILE):
        return set()
    with open(CHECKPOINT_FILE, "r") as f:
        return set(line.strip() for line in f)

def mark_as_processed(email_id):
    """Saves an email ID immediately after processing."""
    with open(CHECKPOINT_FILE, "a") as f:
        f.write(f"{email_id}\n")

def save_job_to_csv(job_data):
    """Appends a single job entry to the CSV immediately."""
    file_exists = os.path.isfile(OUTPUT_CSV_FILE)
    fieldnames = ["role", "company", "salary", "experience", "location", "match_reason", "apply_link", "email_date", "source_subject"]
    
    with open(OUTPUT_CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        
        # Ensure the dict only has keys that exist in fieldnames
        clean_job = {k: job_data.get(k, "N/A") for k in fieldnames}
        writer.writerow(clean_job)

def get_cutoff_date(days):
    """Calculates the date string for IMAP search."""
    date_obj = datetime.now() - timedelta(days=days)
    return date_obj.strftime("%d-%b-%Y")

def clean_text(text):
    """Basic cleanup to remove HTML and excessive whitespace."""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def get_email_body(msg):
    """Extracts the plain text payload from the email object."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors='ignore')
    else:
        return msg.get_payload(decode=True).decode(errors='ignore')
    return ""

def call_groq_with_retry(client, prompt):
    """Wrapper for API calls that handles Rate Limits and Retries."""
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL_ID,
                messages=[
                    {"role": "system", "content": "You are a JSON-only extraction API."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=TEMPERATURE
            )
            return json.loads(response.choices[0].message.content).get("jobs", [])
        
        except RateLimitError:
            print(f"[WARNING] Rate limit reached. Sleeping for {RATE_LIMIT_SLEEP} seconds...")
            time.sleep(RATE_LIMIT_SLEEP)
            continue
        except Exception as e:
            print(f"[ERROR] API call failed on attempt {attempt+1}: {e}")
            time.sleep(5) # Short sleep for generic errors
            continue
    
    print("[ERROR] Max retries reached. Skipping this email.")
    return []

def extract_jobs(client, email_body):
    """Prepares the prompt and sends it to the LLM."""
    truncated_body = email_body[:MAX_CONTEXT_CHARS]
    
    prompt = f"""
    Analyze the job listings below.
    
    CRITERIA (Keep if ANY are true):
    1. ROLE: Matches {', '.join(TARGET_ROLES)}.
    2. PAY: > {MIN_SALARY_LPA} LPA (CTC).
    3. REPUTATION: Recognized tech company or high-growth startup.

    OUTPUT JSON format:
    {{
        "jobs": [
            {{
                "role": "Job Title",
                "company": "Company Name",
                "salary": "e.g. 25-35 LPA",
                "experience": "e.g. 2-4 years",
                "location": "City",
                "match_reason": "e.g. High Salary > {MIN_SALARY_LPA} LPA",
                "apply_link": "URL"
            }}
        ]
    }}

    Email Content:
    {truncated_body}
    """
    return call_groq_with_retry(client, prompt)

def main():
    # 1. Validation
    if not EMAIL_USER or not GROQ_API_KEY:
        print("[ERROR] Missing credentials in .env file.")
        return

    # 2. Connection
    print("[INFO] Connecting to Gmail IMAP...")
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        return

    # 3. Search
    since_date = get_cutoff_date(DAYS_BACK_TO_SEARCH)
    print(f"[INFO] Searching for emails with subject '{EMAIL_SUBJECT_QUERY}' since {since_date}...")
    
    search_criteria = f'(SINCE "{since_date}" SUBJECT "{EMAIL_SUBJECT_QUERY}")'
    status, messages = mail.search(None, search_criteria)
    
    if not messages[0]:
        print("[INFO] No emails found matching criteria.")
        return

    email_ids = messages[0].split()
    processed_ids = load_processed_ids()
    
    # 4. Filter (Skip already processed)
    todo_ids = [eid for eid in email_ids if eid.decode() not in processed_ids]
    
    print(f"[INFO] Total emails found: {len(email_ids)}")
    print(f"[INFO] Already processed: {len(processed_ids)}")
    print(f"[INFO] Remaining to process: {len(todo_ids)}")

    client = Groq(api_key=GROQ_API_KEY)

    # 5. Processing Loop
    for i, e_id in enumerate(todo_ids):
        e_id_str = e_id.decode()
        
        try:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_header(msg["Subject"])[0][0]
            if isinstance(subject, bytes): subject = subject.decode()
            date = msg["Date"]

            print(f"[INFO] Processing [{i+1}/{len(todo_ids)}]: {subject[:40]}...")

            body = get_email_body(msg)
            cleaned = clean_text(body)

            # Only process if body has substantial content
            if len(cleaned) > 100:
                jobs = extract_jobs(client, cleaned)
                
                if jobs:
                    print(f"   [SUCCESS] Found {len(jobs)} relevant jobs. Saving...")
                    for job in jobs:
                        job['email_date'] = date
                        job['source_subject'] = subject
                        save_job_to_csv(job)
                else:
                    print("   [INFO] No matching jobs found in this email.")
            
            # Checkpoint: Save progress
            mark_as_processed(e_id_str)
            
            # Rate Limit Safety
            time.sleep(NORMAL_SLEEP_INTERVAL)

        except Exception as e:
            print(f"[ERROR] Critical failure on email ID {e_id_str}: {e}")
            # We continue loop, but do NOT mark as processed, so we can retry later
            continue

    print("[INFO] Processing complete.")
    mail.logout()

if __name__ == "__main__":
    main()