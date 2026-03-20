import os
import smtplib
import feedparser 
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import pandas as pd
from jobspy import scrape_jobs
import re

# --- CONFIGURATION ---
SEARCH_QUERY_STR = "Unity Developer|Unreal Developer|Game Developer|AR Developer|VR Developer|XR Developer|AR/VR Developer|Mobile Developer|Unity Engineer|Unreal Engineer|Game Engineer|AR Engineer|VR Engineer|XR Engineer|AR/VR Engineer|Mobile Engineer"
SEARCH_QUERY_JOBSPY = f"({SEARCH_QUERY_STR.replace('|', ' OR ')})"

SKILL_WEIGHTS = {
    "Unity": 5, "C#": 5, 
    
    "DOTS": 2, "Multiplayer": 2, "Photon": 2, 
    "Normcore": 2, "ECS": 2, "HDRP": 2, "URP": 2, "Graphics": 2,
    "Native": 2, "C++": 2, "Shaders": 2, "Digital Twins": 2, 
    "Android": 2, "AR": 2, "VR": 2, "XR": 2, "Mobile Games": 2, 
    "Unreal": 2, ".Net": 2, "Photon": 2, "WebGL": 2,
    
    "Git": 1, "Perforce": 1, "iOS": 1, "Optimization": 1,
    "Addressables": 1, "Unit Testing": 1, "Python": 1, "Java": 1,
    "JavaScript" : 1, "OpenGL": 1, "PHP": 1, "Normcore": 1, 
    "Multiplayer": 1, "Unity Package Manager": 1, "UPM": 1,
    "NuGet": 1, "Live Ops": 1, "Data Analysis": 1, "Oculus Quest": 1,
    "UI Toolkit": 1, "uGUI": 1, "Zenject": 1, "VContainer": 1,
    "PostgreSQL": 1, "Software measurement": 1, "Software management": 1,
    "Machine Learning": 1
}
SKILLS_BLACKLIST = [
    "NFT", "Gambling", "Casino", "Slot", "Betting", "Unity Catalog", "Databricks"
    "Unpaid", "Equity Only", "Volunteer", "No Salary", "Internship"
]
TARGET_SCORE = 12 # A "100%" match is 12 points

# --- NEW: STRICT GEOFENCING ---
# Any job whose location string doesn't contain one of these will be ignored.
LOCATION_WHITELIST = ["usa", "united states", "america", "canada", "ca", "montreal", "toronto", "vancouver", "quebec", "ontario", "remote"]
# However, we must exclude "Remote" alone if it's paired with a banned country.
LOCATION_BLACKLIST = ["uk", "united kingdom", "india", "germany", "europe", "brazil", "asia"]

IGNORE_LIST = ["CyberCoders", "Jobot", "BairesDev", "Toptal", "Staffing"]
TITLE_BLACKLIST = ["Intern", "Junior", "Associate", "Student", "Graduate", "Artist", "Work-Study", "Fellowship", "Test"]

# Define the targets: (Country Code for Indeed/Glassdoor, Location string)
# "Remote" as a location usually triggers "Remote Anywhere/Worldwide" on LinkedIn/Google
TARGET_MARKETS = [
    {"country": "usa", "location": "Remote"},
    {"country": "canada", "location": "Remote"},
    {"country": "canada", "location": "Montreal"},
    {"country": "usa/ca", "location": "Remote"}
]

def fetch_work_with_indies():
    """Parses the Work With Indies RSS feed."""
    print("🔍 Fetching Work With Indies feed...")
    FEED_URL = "https://workwithindies.com/rss"
    feed = feedparser.parse(FEED_URL)
    
    wwi_jobs = []
    for entry in feed.entries:
        # Simple regex check to see if the title matches your roles
        if re.search(rf"({SEARCH_QUERY_STR})", entry.title, re.IGNORECASE):
            wwi_jobs.append({
                "title": entry.title,
                "company": entry.author if 'author' in entry else "Indie Studio",
                "job_url": entry.link,
                "location": "Remote", # WWI is 95% remote, geofence will catch others
                "description": entry.summary,
                "date_posted": entry.published if 'published' in entry else None,
                "site": "WorkWithIndies"
            })
    return pd.DataFrame(wwi_jobs)
    
def is_location_valid(job_location):
    loc = str(job_location).lower()
    if any(banned in loc for banned in LOCATION_BLACKLIST): return False
    if any(allowed in loc for allowed in LOCATION_WHITELIST): return True
    return False
    
def analyze_job(title, company, description, location):
    # 1. Physical Location Check
    if not is_location_valid(location): return -1, []
    
    title_clean = str(title).lower()
    company_clean = str(company).lower()
    desc_clean = str(description).lower()
    full_text = f"{title_clean} {desc_clean}"
    
    # 2. Hard Disqualifiers (Company, Title, and NOW Skills Blacklist)
    if any(item.lower() in company_clean for item in IGNORE_LIST): return -1, []
    if any(item.lower() in title_clean for item in TITLE_BLACKLIST): return -1, []
    
    # New: Skills Blacklist Check (Regex for whole-word matching)
    for bad_skill in SKILLS_BLACKLIST:
        if re.search(rf'\b{re.escape(bad_skill.lower())}\b', full_text):
            return -1, []

    # 3. Positive Points Calculation
    found_skills, total_points = [], 0
    for skill, weight in SKILL_WEIGHTS.items():
        if re.search(rf'\b{re.escape(skill.lower())}\b', full_text):
            found_skills.append(skill)
            total_points += weight

    # Scoring Formula:
    # $$Score = \min\left(\frac{\text{Total Points}}{\text{Target Score}} \times 100, 100\right)$$
    score = min(round((total_points / TARGET_SCORE) * 100), 100)
    return score, found_skills

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
    msg["Subject"] = f"🌍 Global Remote Job Report: {datetime.now().strftime('%b %d')}"
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
    
    # 1. Fetch from JobSpy (LinkedIn, Indeed, Google)
    markets = [{"country": "usa", "loc": "Remote"}, {"country": "canada", "loc": "Remote"}]
    for m in markets:
        try:
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed", "google"],
                search_term=SEARCH_QUERY_JOBSPY,
                location=m['loc'],
                results_wanted=20,
                hours_old=72,
                country_indeed=m['country'],
                is_remote=True
            )
            if not jobs.empty: all_results.append(jobs)
        except Exception as e:
            print(f"⚠️ JobSpy error: {e}")

    # 2. Fetch from Work With Indies
    try:
        wwi_df = fetch_work_with_indies()
        if not wwi_df.empty: all_results.append(wwi_df)
    except Exception as e:
        print(f"⚠️ WWI error: {e}")

    if not all_results:
        print("No jobs found today.")
        return

    combined_jobs = pd.concat(all_results).drop_duplicates(subset=['job_url'])
    processed_listings = []

    for _, row in combined_jobs.iterrows():
        score, found_skills = analyze_job(row['title'], row['company'], row.get('description', ''), row.get('location', 'Remote'))
        if score >= 30: 
            processed_listings.append({**row, "score": score, "skills": found_skills, "days_ago": "Recent"})
    
    processed_listings.sort(key=lambda x: x['score'], reverse=True)

    # --- HTML GENERATION (Condensed) ---
    job_cards_html = ""
    if processed_listings:
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
                <h2 style="color: #ffffff; margin-bottom: 5px;">Job Search</h2>
                <p style="color: #666; font-size: 14px; margin-bottom: 25px;">Tracking <b>{SEARCH_QUERY_JOBSPY}</b> in USA, Canada, & Worldwide</p>
                {job_cards_html if job_cards_html else '<p style="color:#888;">No high-value global leads found today.</p>'}
            </div>
        </div>
        """
        send_email(email_html)
        print(f"✅ Success! {len(processed_listings)} jobs passed the location and skill filter.")
    else:
        print("❌ Jobs were found, but none were in the correct location or met skill requirements.")

if __name__ == "__main__":
    run_agent()
