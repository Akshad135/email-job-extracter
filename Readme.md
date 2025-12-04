# README

## Description

This project contains a Python script that connects to a Gmail inbox, searches for specific emails, extracts job-related information using the Groq API, and saves the results to a CSV file.

## Setup and Installation

### 1. Clone the project

```
git clone https://github.com/Akshad135/email-job-extracter.git
cd <project-folder>
```

### 2. Install dependencies

```
pip install python-dotenv groq pandas
```

### 3. Gmail App Password Setup

Gmail no longer supports normal password login for IMAP. You must generate an App Password.

Steps:

1. Go to your Google Account settings.
2. Enable 2-Step Verification (required for app passwords).
3. After enabling it, navigate to "Security" → "App Passwords".
4. Create a new App Password.
5. Copy the generated 16-character password.

Use this password as `EMAIL_PASS`.

### 4. Create a `.env` file

Create a file named `.env` in the project folder and add:

```
EMAIL_USER=your_email@gmail.com
EMAIL_PASS=your_gmail_app_password
GROQ_API_KEY=your_groq_api_key
```

## Running the Script

Run the main file:

```
python main.py
```

This will connect to Gmail, fetch emails that match the configured subject and date filters, send them for processing, and write extracted job data to a CSV file.

## Output Files

- `final_jobs_list.csv` – Contains extracted job entries.
- `processed_email_ids.txt` – Tracks which email IDs have already been processed to prevent duplicate work.
