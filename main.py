import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import pandas as pd
from jobspy import scrape_jobs

# --- CONFIGURATION ---
# Added Architect/Staff to search and skills
SEARCH_QUERY = "(Senior OR Lead OR Staff OR Architect) Unity Developer"
MY_SKILLS = [
    "C#", "Unity", "Unreal", "Git", "URP", "HDRP", "Android", "AR", "VR", "XR"
    "Perforce", "C++", "DOTS", "Addressables", "iOS", ".Net", "JavaScript"
    "Optimization", "Architect", "Staff", "Lead", "Python", "Java", "OpenGL",
    "PHP", "Photon", "Normcore", "Multiplayer", "Unity Package Manager", "uGUI",
    "NuGet", "Live Ops", "Data Analysis", "Mobile Games", "Oculus Quest", "WebGL",
    "PostgreSQL", "Software measurement", "Software management", "Unit Testing",
    "Machine Learning", "UI Toolkit", "Digital Twins"
]

IGNORE_LIST = ["CyberCoders", "Jobot", "BairesDev", "Toptal", "Staffing"]
TITLE_BLACKLIST = ["Intern", "Junior", "Associate", "Student", "Graduate"]

def analyze_job(title, company, description):
    title_clean = str(title).lower()
    company_clean = str(company).lower()
    desc_clean = str(description).lower()

    for item in IGNORE_LIST:
        if item.lower() in company_clean: return -1, []
    for item in TITLE_BLACKLIST:
        if item.lower() in title_clean: return -1, []

    text = f"{title_clean} {desc_clean}"
    found = [skill for skill in MY_SKILLS if skill.lower() in text]
    score = round((len(found) / len(MY_SKILLS)) * 100) if MY_SKILLS else 0
    return score, found

def get_days_ago(date_posted):
    """Calculates how many days ago the job was posted."""
    if not date_posted:
        return "New"
    try:
        now = datetime.now(timezone.utc)
        # Ensure date_posted is a datetime object
        posted = pd.to_datetime(date_posted).tz_localize('UTC') if not hasattr(date_posted, 'tzinfo') else pd.to_datetime(date_posted)
        delta = (now - posted).days
        return f"{delta}d ago" if delta > 0 else "Today"
    except:
        return "Recent"

def send_email(html_content):
    sender = os.getenv("EMAIL_SENDER")
    receiver = os.getenv("EMAIL_RECEIVER")
    password = os.getenv("EMAIL_PASSWORD")
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚀 Unity Lead/Architect Report: {datetime.now().strftime('%b %d')}"
    msg["From"] = sender
    msg["To"] = receiver
    msg.attach(MIMEText(html_content, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())

def run_agent():
    jobs = scrape_jobs(
        site_name=["linkedin", "indeed", "google"],
        search_term=SEARCH_QUERY,
        location="Remote",
        results_wanted=25,
        hours_old=48, # Expanded to 48h to ensure no weekend drops are missed
        description_formatting="markdown"
    )

    processed_listings = []
    if not jobs.empty:
        for _, row in jobs.iterrows():
            score, found_skills = analyze_job(row['title'], row['company'], row.get('description', ''))
            if score >= 15: 
                days_ago = get_days_ago(row.get('date_posted'))
                processed_listings.append({**row, "score": score, "skills": found_skills, "days_ago": days_ago})
        
        processed_listings.sort(key=lambda x: x['score'], reverse=True)

    # --- HTML GENERATION ---
    job_cards_html = ""
    for job in processed_listings:
        skills_tags = "".join([f'<span style="background:#333; color:#00ffa3; padding:2px 8px; border-radius:4px; margin-right:5px; font-size:11px; display:inline-block; margin-top:4px;">{s}</span>' for s in job['skills']])
        
        job_cards_html += f"""
        <div style="background: #1e1e1e; border: 1px solid #333; border-radius: 12px; padding: 20px; margin-bottom: 15px; color: #ffffff; font-family: sans-serif;">
            <table width="100%">
                <tr>
                    <td>
                        <h3 style="margin: 0; color: #ffffff; font-size: 18px;">{job['title']}</h3>
                        <p style="margin: 4px 0; color: #888; font-size: 13px;">
                            {job['company']} • <span style="color: #ffaa00;">{job['days_ago']}</span>
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
            <a href="{job['job_url']}" style="display: inline-block; background: #00ffa3; color: #000; text-decoration: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; font-size: 12px;">Quick Apply</a>
        </div>
        """

    email_html = f"""
    <div style="background: #121212; padding: 20px; font-family: sans-serif;">
        <div style="max-width: 600px; margin: 0 auto;">
            <h2 style="color: #ffffff; margin-bottom: 5px;">Unity Talent Intelligence</h2>
            <p style="color: #666; font-size: 14px; margin-bottom: 25px;">Tracking {SEARCH_QUERY}</p>
            {job_cards_html if job_cards_html else '<p style="color:#888;">No high-value leads found today.</p>'}
        </div>
    </div>
    """
    send_email(email_html)

if __name__ == "__main__":
    run_agent()