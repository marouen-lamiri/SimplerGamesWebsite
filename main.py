import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import pandas as pd
from jobspy import scrape_jobs
import re

# --- CONFIGURATION ---
SEARCH_QUERY = "Unity Developer"
# Skills moved to a set for O(1) lookup performance
MY_SKILLS = {
    "C#", "Unity", "Unreal", "Git", "URP", "HDRP", "Android", "AR", "VR", "XR",
    "Perforce", "C++", "DOTS", "Addressables", "iOS", ".Net", "JavaScript",
    "Optimization", "Architect", "Staff", "Lead", "Python", "Java", "OpenGL",
    "PHP", "Photon", "Normcore", "Multiplayer", "Unity Package Manager", "uGUI",
    "NuGet", "Live Ops", "Data Analysis", "Mobile Games", "Oculus Quest", "WebGL",
    "PostgreSQL", "Software measurement", "Software management", "Unit Testing",
    "Machine Learning", "UI Toolkit", "Digital Twins"
}

IGNORE_LIST = ["CyberCoders", "Jobot", "BairesDev", "Toptal", "Staffing"]
TITLE_BLACKLIST = ["Intern", "Junior", "Associate", "Student", "Graduate"]

def analyze_job(title, company, description):
    title_clean = str(title).lower()
    company_clean = str(company).lower()
    desc_clean = str(description).lower()

    if any(item.lower() in company_clean for item in IGNORE_LIST): return -1, []
    if any(item.lower() in title_clean for item in TITLE_BLACKLIST): return -1, []

    # Use regex to find whole words only (prevents "C" matching "Clean")
    found = [skill for skill in MY_SKILLS if re.search(rf'\b{re.escape(skill.lower())}\b', desc_clean + " " + title_clean)]
    
    # Adjusted Scoring: 5+ matches is usually a "Strong Fit" (80%+)
    # We divide by 7 instead of 42 to make the score more meaningful
    score = min(round((len(found) / 7) * 100), 100) 
    return score, found

def get_days_ago(date_posted):
    if pd.isna(date_posted):
        return "New"
    try:
        now = datetime.now(timezone.utc)
        # pd.to_datetime with utc=True handles both aware and naive strings
        posted = pd.to_datetime(date_posted, utc=True)
        delta = (now - posted).days
        return f"{delta}d ago" if delta > 0 else "Today"
    except:
        return "Recent"

# ... [send_email function remains the same] ...

def run_agent():
    try:
        jobs = scrape_jobs(
            site_name=["linkedin", "indeed", "google"],
            search_term=SEARCH_QUERY,
            location="Remote",
            results_wanted=25,
            hours_old=168,
            description_formatting="markdown",
            country_indeed="canada" 
        )
    except Exception as e:
        print(f"Scraping failed: {e}")
        return

    processed_listings = []
    if not jobs.empty:
        for _, row in jobs.iterrows():
            score, found_skills = analyze_job(row.get('title', ''), row.get('company', ''), row.get('description', ''))
            
            # Lowering threshold slightly because of the new scoring math
            if score >= 30: 
                days_ago = get_days_ago(row.get('date_posted'))
                processed_listings.append({
                    "title": row.get('title'),
                    "company": row.get('company'),
                    "job_url": row.get('job_url'),
                    "score": score, 
                    "skills": found_skills, 
                    "days_ago": days_ago
                })
        
        processed_listings.sort(key=lambda x: x['score'], reverse=True)

    # --- HTML GENERATION ---
    # (Same as your logic, but ensure it handles empty processed_listings)
    # ... 

if __name__ == "__main__":
    run_agent()
