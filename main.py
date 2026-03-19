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

# Define the targets: (Country Code for Indeed/Glassdoor, Location string)
# "Remote" as a location usually triggers "Remote Anywhere/Worldwide" on LinkedIn/Google
TARGET_MARKETS = [
    {"country": "usa", "location": "Remote"},
    {"country": "canada", "location": "Remote"}
]

def analyze_job(title, company, description):
    title_clean = str(title).lower()
    company_clean = str(company).lower()
    desc_clean = str(description).lower()

    if any(item.lower() in company_clean for item in IGNORE_LIST): return -1, []
    if any(item.lower() in title_clean for item in TITLE_BLACKLIST): return -1, []

    text = f"{title_clean} {desc_clean}"
    found = [skill for skill in MY_SKILLS if re.search(rf'\b{re.escape(skill.lower())}\b', text)]
    
    # Scoring normalized against a "perfect" 7-skill match
    score = min(round((len(found) / 7) * 100), 100) 
    return score, found

def get_days_ago(date_posted):
    if pd.isna(date_posted): return "New"
    try:
        now = datetime.now(timezone.utc)
        posted = pd.to_datetime(date_posted, utc=True)
        delta = (now - posted).days
        return f"{delta}d ago" if delta > 0 else "Today"
    except:
        return "Recent"

def send_email(html_content):
    sender = os.getenv("EMAIL_SENDER")
    receiver = os.getenv("EMAIL_RECEIVER")
    password = os.getenv("EMAIL_PASSWORD")
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌍 Unity Global Remote Report: {datetime.now().strftime('%b %d')}"
    msg["From"] = sender
    msg["To"] = receiver
    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
    except Exception as e:
        print(f"Failed to send email: {e}")

def run_agent():
    all_results = []

    for market in TARGET_MARKETS:
        print(f"Searching {market['country'].upper()} market...")
        try:
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed", "google"],
                search_term=SEARCH_QUERY,
                location=market['location'],
                results_wanted=20,
                hours_old=168,
                country_indeed=market['country'],
                is_remote=True, # Forces the remote filter
                description_formatting="markdown"
            )
            if not jobs.empty:
                all_results.append(jobs)
        except Exception as e:
            print(f"Error scraping {market['country']}: {e}")

    if not all_results:
        print("No jobs found in any market.")
        return

    # Combine and Deduplicate based on job URL
    combined_jobs = pd.concat(all_results).drop_duplicates(subset=['job_url'])

    processed_listings = []
    for _, row in combined_jobs.iterrows():
        score, found_skills = analyze_job(row['title'], row['company'], row.get('description', ''))
        if score >= 25: 
            days_ago = get_days_ago(row.get('date_posted'))
            processed_listings.append({**row, "score": score, "skills": found_skills, "days_ago": days_ago})
    
    processed_listings.sort(key=lambda x: x['score'], reverse=True)

    # --- HTML GENERATION (Condensed) ---
    job_cards_html = ""
    for job in processed_listings:
        skills_tags = "".join([f'<span style="background:#333; color:#00ffa3; padding:2px 8px; border-radius:4px; margin-right:5px; font-size:11px; display:inline-block; margin-top:4px;">{s}</span>' for s in job['skills']])
        # Added a location badge to the UI
        loc_info = f"{job.get('location', 'Remote')}"
        
        job_cards_html += f"""
        <div style="background: #1e1e1e; border: 1px solid #333; border-radius: 12px; padding: 20px; margin-bottom: 15px; color: #ffffff; font-family: sans-serif;">
            <table width="100%">
                <tr>
                    <td>
                        <h3 style="margin: 0; color: #ffffff; font-size: 18px;">{job['title']}</h3>
                        <p style="margin: 4px 0; color: #888; font-size: 13px;">
                            {job['company']} • <span style="color: #00ffa3;">{loc_info}</span> • <span style="color: #ffaa00;">{job['days_ago']}</span>
                        </p>
                    </td>
                    <td style="text-align: right; vertical-align: top; width: 60px;">
                        <div style="border: 2px solid #00ffa3; border-radius: 50%; width: 45px; height: 45px; line-height: 45px; text-align: center; color: #00ffa3; font-weight: bold; font-size: 14px;">
                            {job['score']}%
                        </div>
                    </td>
                </tr>
            </table>
            <div style="margin: 12px 0;">{skills_tags}</div>
            <a href="{job['job_url']}" style="display: inline-block; background: #00ffa3; color: #000; text-decoration: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; font-size: 12px;">View Opportunity</a>
        </div>
        """

    email_html = f"""
    <div style="background: #121212; padding: 20px; font-family: sans-serif;">
        <div style="max-width: 600px; margin: 0 auto;">
            <h2 style="color: #ffffff; margin-bottom: 5px;">Unity Talent Intelligence</h2>
            <p style="color: #666; font-size: 14px; margin-bottom: 25px;">Tracking <b>{SEARCH_QUERY}</b> in USA, Canada, & Worldwide</p>
            {job_cards_html if job_cards_html else '<p style="color:#888;">No high-value global leads found today.</p>'}
        </div>
    </div>
    """
    send_email(email_html)

if __name__ == "__main__":
    run_agent()
