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
    
def is_location_valid(job_location):
    loc = str(job_location).lower()
    if any(banned in loc for banned in LOCATION_BLACKLIST): return False
    if any(allowed in loc for allowed in LOCATION_WHITELIST): return True
    return False
    
def analyze_job(title, company, description, location):
    # 1. Location check
    if not is_location_valid(location): return -1, []
    
    title_clean, company_clean, desc_clean = str(title).lower(), str(company).lower(), str(description).lower()
    full_text = f"{title_clean} {desc_clean}"
    
    # 2. Blacklist checks
    if any(item.lower() in company_clean for item in IGNORE_LIST): return -1, []
    if any(item.lower() in title_clean for item in TITLE_BLACKLIST): return -1, []
    if any(re.search(rf'\b{re.escape(bad.lower())}\b', full_text) for bad in SKILLS_BLACKLIST): return -1, []

    # 3. Points
    found_skills, total_points = [], 0
    for skill, weight in SKILL_WEIGHTS.items():
        if re.search(rf'\b{re.escape(skill.lower())}\b', full_text):
            found_skills.append(skill)
            total_points += weight

    score = min(round((total_points / TARGET_SCORE) * 100), 100)
    return score, found_skills

def fetch_stable_feeds():
    """Fetches jobs from sources that rarely block cloud IPs."""
    stable_jobs = []
    FEEDS = [
        {"name": "WorkWithIndies", "url": "https://workwithindies.com/rss"},
        {"name": "Remotive", "url": "https://remotive.com/remote-jobs/feed"},
        {"name": "WWR", "url": "https://weworkremotely.com/categories/remote-programming-jobs.rss"}
    ]

    for feed_info in FEEDS:
        print(f"📡 Syncing {feed_info['name']}...")
        try:
            feed = feedparser.parse(feed_info['url'])
            for entry in feed.entries:
                if re.search(rf"({SEARCH_QUERY_STR})", entry.title, re.IGNORECASE):
                    stable_jobs.append({
                        "title": entry.title,
                        "company": entry.get('author', feed_info['name']),
                        "job_url": entry.link,
                        "location": "Remote",
                        "description": entry.get('summary', ''),
                        "site": feed_info['name']
                    })
        except Exception as e:
            print(f"⚠️ Feed error on {feed_info['name']}: {e}")

    # Hacker News Algolia API (Who is Hiring)
    print("📡 Querying Hacker News API...")
    try:
        thirty_days_ago = int((datetime.now() - timedelta(days=30)).timestamp())
        hn_url = f"https://hn.algolia.com/api/v1/search_by_date?query=Unity&tags=comment&numericFilters=created_at_i>{thirty_days_ago}"
        response = requests.get(hn_url).json()
        for hit in response['hits']:
            # Look for hiring comments
            text = hit.get('comment_text', '')
            if "hiring" in text.lower() or "remote" in text.lower():
                stable_jobs.append({
                    "title": "HN Hiring Thread Listing",
                    "company": "HN Startup",
                    "job_url": f"https://news.ycombinator.com/item?id={hit['objectID']}",
                    "location": "Remote",
                    "description": text,
                    "site": "HackerNews"
                })
    except: pass

    return pd.DataFrame(stable_jobs)

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
    markets = [{"country": "usa", "loc": "Remote"}, {"country": "canada", "loc": "Remote"}]
    for m in markets:
        print(f"🔍 Scraping {m['country'].upper()} (LinkedIn, Indeed, Google)...")
        try:
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed", "google"],
                search_term=SEARCH_QUERY_JOBSPY,
                location=m['loc'],
                results_wanted=30,
                hours_old=72,
                country_indeed=m['country'],
                is_remote=True
            )
            if not jobs.empty: all_results.append(jobs)
        except Exception as e:
            print(f"⚠️ JobSpy error: {e}")

    # 2. Work With Indies
    try:
        wwi_df = fetch_stable_feeds()
        if not wwi_df.empty: all_results.append(wwi_df)
    except Exception as e:
        print(f"⚠️ WWI error: {e}")

    if not all_results:
        print("📭 No jobs found today.")
        return

    combined_jobs = pd.concat(all_results).drop_duplicates(subset=['job_url'])
    processed_listings = []

    for _, row in combined_jobs.iterrows():
        score, found_skills = analyze_job(
            row['title'], row['company'], row.get('description', ''), row.get('location', 'Remote')
        )
        if score >= 35: # Quality threshold
            processed_listings.append({**row, "score": score, "skills": found_skills})
    
    processed_listings.sort(key=lambda x: x['score'], reverse=True)

    # --- HTML GENERATION (Condensed) ---
    job_cards_html = ""
    if processed_listings:
        job_cards = ""
        for job in final_list:
            border = "#00ffa3" if job['score'] >= 80 else "#333"
            skills_html = "".join([f'<span style="background:#222; color:#00ffa3; padding:2px 6px; border-radius:4px; margin-right:4px; font-size:10px; border:1px solid #444;">{s}</span>' for s in job['skills']])
            
            job_cards += f"""
            <div style="background:#1a1a1a; border:1px solid {border}; border-radius:10px; padding:15px; margin-bottom:15px; font-family:sans-serif;">
                <table width="100%">
                    <tr>
                        <td>
                            <h3 style="margin:0; color:#fff; font-size:16px;">{job['title']}</h3>
                            <p style="margin:4px 0; color:#aaa; font-size:12px;">{job['company']} • <span style="color:#00ffa3;">{job.get('site', 'JobBoard')}</span></p>
                        </td>
                        <td align="right" valign="top">
                            <div style="color:#00ffa3; font-weight:bold; font-size:18px;">{job['score']}%</div>
                        </td>
                    </tr>
                </table>
                <div style="margin:10px 0;">{skills_html}</div>
                <a href="{job['job_url']}" style="background:#00ffa3; color:#000; text-decoration:none; padding:6px 12px; border-radius:4px; font-weight:bold; font-size:11px; display:inline-block;">View Role</a>
            </div>
            """
    
        full_html = f"""
        <div style="background:#111; padding:20px;">
            <h2 style="color:#fff; border-bottom:1px solid #333; padding-bottom:10px;">Unity Talent Intelligence</h2>
            {job_cards if job_cards else '<p style="color:#888;">No high-score matches today.</p>'}
        </div>
        """
        send_email(email_html)
        print(f"✅ Success! {len(processed_listings)} jobs passed the location and skill filter.")
    else:
        print("❌ Jobs were found, but none were in the correct location or met skill requirements.")

if __name__ == "__main__":
    run_agent()
