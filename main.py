import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import pandas as pd
from jobspy import scrape_jobs
import re

# --- CONFIGURATION ---
SEARCH_QUERY = "(Unity Developer OR Unreal Developer OR Game Developer OR AR Developer OR VR Developer OR XR Developer OR AR/VR Developer OR Mobile Developer)"
SKILL_WEIGHTS = {
    # Platinum Tier (5 points)
    "Unity": 5, "C#": 5, 
    
    # Gold Tier (3 points)
    "DOTS": 3, "Multiplayer": 3, "Photon": 3, 
    "Normcore": 3, "ECS": 3, "HDRP": 3, "URP": 3, "Graphics": 3,
    "Native": 3, "C++": 3, "Shaders": 3, "Digital Twins": 3, 
    "Android": 3, "AR": 3, "VR": 3, "XR": 3, "Mobile Games": 3, 
    "Unreal": 3, ".Net": 3, "Photon": 3, "WebGL": 3,
    
    # Silver Tier (1 point)
    "Git": 1, "Perforce": 1, "iOS": 1, "Optimization": 1,
    "Addressables": 1, "Unit Testing": 1, "Python": 1, "Java": 1,
    "JavaScript" : 1, "OpenGL": 1, "PHP": 1, "Normcore": 1, 
    "Multiplayer": 1, "Unity Package Manager": 1, "UPM": 1,
    "NuGet": 1, "Live Ops": 1, "Data Analysis": 1, "Oculus Quest": 1,
    "UI Toolkit": 1, "uGUI": 1, "Zenject": 1, "VContainer": 1,
    "PostgreSQL": 1, "Software measurement": 1, "Software management": 1,
    "Machine Learning": 1
}
TARGET_SCORE = 12  # A "100%" match is roughly one Platinum + two Gold + one Silver

# --- NEW: STRICT GEOFENCING ---
# Any job whose location string doesn't contain one of these will be ignored.
LOCATION_WHITELIST = ["usa", "united states", "america", "canada", "ca", "montreal", "toronto", "vancouver", "quebec", "ontario", "remote"]
# However, we must exclude "Remote" alone if it's paired with a banned country.
LOCATION_BLACKLIST = ["uk", "united kingdom", "india", "germany", "europe", "brazil", "asia"]

IGNORE_LIST = ["CyberCoders", "Jobot", "BairesDev", "Toptal", "Staffing"]
TITLE_BLACKLIST = ["Intern", "Junior", "Associate", "Student", "Graduate"]

# Define the targets: (Country Code for Indeed/Glassdoor, Location string)
# "Remote" as a location usually triggers "Remote Anywhere/Worldwide" on LinkedIn/Google
TARGET_MARKETS = [
    {"country": "usa", "location": "Remote"},
    {"country": "canada", "location": "Remote"},
    {"country": "canada", "location": "Montreal"},
    {"country": "usa/ca", "location": "Remote"}
]

def is_location_valid(job_location):
    """Ensures the job is actually in our target territory."""
    loc = str(job_location).lower()
    
    # Check if any blacklisted country is mentioned (e.g., "Remote, UK")
    if any(banned in loc for banned in LOCATION_BLACKLIST):
        return False
        
    # Check if it mentions our allowed regions
    if any(allowed in loc for allowed in LOCATION_WHITELIST):
        return True
        
    return False

def analyze_job(title, company, description, location):
    # 1. Physical Location Check
    if not is_location_valid(location):
        return -1, []

    title_clean = str(title).lower()
    company_clean = str(company).lower()
    desc_clean = str(description).lower()

    # 2. Company/Title Check
    if any(item.lower() in company_clean for item in IGNORE_LIST): return -1, []
    if any(item.lower() in title_clean for item in TITLE_BLACKLIST): return -1, []

    # 3. Points Calculation
    text = f"{title_clean} {desc_clean}"
    found_skills = []
    total_points = 0
    for skill, weight in SKILL_WEIGHTS.items():
        if re.search(rf'\b{re.escape(skill.lower())}\b', text):
            found_skills.append(skill)
            total_points += weight

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
    for market in TARGET_MARKETS:
        print(f"🔍 Fetching {market['country'].upper()}...")
        try:
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed", "google"],
                search_term=SEARCH_QUERY,
                location=market['location'],
                results_wanted=30, # Increased sample size to account for more filtered-out jobs
                hours_old=72,
                country_indeed=market['country'],
                is_remote=(market['location'] == "Remote"),
                description_formatting="markdown"
            )
            if not jobs.empty:
                all_results.append(jobs)
        except Exception as e:
            print(f"⚠️ Market error: {e}")

    if not all_results:
        print("No raw data found.")
        return

    combined_jobs = pd.concat(all_results).drop_duplicates(subset=['job_url'])
    processed_listings = []

    for _, row in combined_jobs.iterrows():
        # Pass location into the analysis function
        score, found_skills = analyze_job(
            row['title'], 
            row['company'], 
            row.get('description', ''), 
            row.get('location', 'Unknown')
        )
        
        if score >= 30: 
            days_ago = get_days_ago(row.get('date_posted'))
            processed_listings.append({**row, "score": score, "skills": found_skills, "days_ago": days_ago})
    
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
                <p style="color: #666; font-size: 14px; margin-bottom: 25px;">Tracking <b>{SEARCH_QUERY}</b> in USA, Canada, & Worldwide</p>
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
