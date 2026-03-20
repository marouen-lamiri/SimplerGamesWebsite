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

def generate_html_report(processed_listings):
    """Converts the list of scored jobs into a high-end Dark Mode HTML report."""
    
    # Header Section
    report_html = f"""
    <div style="background-color: #0a0a0a; color: #ffffff; padding: 20px; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;">
        <div style="max-width: 600px; margin: 0 auto;">
            <h1 style="color: #00ffa3; font-size: 24px; margin-bottom: 5px;">Unity Talent Intelligence</h1>
            <p style="color: #888; font-size: 14px; margin-top: 0;">{len(processed_listings)} high-value leads identified for {datetime.now().strftime('%B %d, %Y')}</p>
            <hr style="border: 0; border-top: 1px solid #333; margin: 20px 0;">
    """

    # Job Cards Loop
    for job in processed_listings:
        # Highlight top-tier matches with a glowing border
        border_color = "#00ffa3" if job['score'] >= 80 else "#222"
        
        # Format the skills as small "chips"
        skills_chips = "".join([
            f'<span style="background: #111; color: #00ffa3; padding: 3px 8px; border-radius: 4px; margin-right: 5px; font-size: 11px; border: 1px solid #333; display: inline-block; margin-bottom: 4px;">{skill}</span>' 
            for skill in job['skills']
        ])

        report_html += f"""
            <div style="background: #161616; border: 1px solid {border_color}; border-radius: 12px; padding: 20px; margin-bottom: 20px;">
                <table width="100%" cellspacing="0" cellpadding="0">
                    <tr>
                        <td style="vertical-align: top;">
                            <h2 style="margin: 0; font-size: 18px; color: #ffffff;">{job['title']}</h2>
                            <p style="margin: 5px 0; color: #00ffa3; font-size: 13px; font-weight: bold;">
                                {job['company']} 
                                <span style="color: #555; font-weight: normal; margin-left: 5px;">• {job['location']}</span>
                            </p>
                        </td>
                        <td style="vertical-align: top; text-align: right; width: 60px;">
                            <div style="color: #00ffa3; font-size: 22px; font-weight: bold;">{job['score']}%</div>
                            <div style="font-size: 9px; color: #555; text-transform: uppercase; letter-spacing: 1px;">Match</div>
                        </td>
                    </tr>
                </table>
                
                <div style="margin: 15px 0;">
                    {skills_chips}
                </div>
                
                <table width="100%" cellspacing="0" cellpadding="0" style="margin-top: 10px;">
                    <tr>
                        <td>
                            <span style="background: #222; color: #888; padding: 4px 8px; border-radius: 4px; font-size: 10px; text-transform: uppercase;">
                                Source: {job.get('origin_tag', 'Direct')}
                            </span>
                        </td>
                        <td style="text-align: right;">
                            <a href="{job['job_url']}" style="background-color: #00ffa3; color: #000000; padding: 8px 16px; border-radius: 6px; text-decoration: none; font-weight: bold; font-size: 12px; display: inline-block;">
                                View Position →
                            </a>
                        </td>
                    </tr>
                </table>
            </div>
        """

    # Footer Section
    report_html += """
            <p style="text-align: center; color: #444; font-size: 11px; margin-top: 30px;">
                Automated Intelligence by Simpler Games Agent. <br>
                Targeting: Lead/Senior Unity Roles in Canada & USA.
            </p>
        </div>
    </div>
    """
    
    return report_html

def run_agent():
    """Main execution loop: Scrapes, Parses Feeds, Scores, and Emails."""
    all_data = []
    
    # --- PART A: JOBSPY (LinkedIn & Google) ---
    for market in TARGET_MARKETS:
        print(f"🔍 Searching JobSpy: {market['country'].upper()} ({market['location']})...")
        try:
            # Dynamically set the remote flag based on your location string
            is_remote_search = True if "remote" in market['location'].lower() else False
            
            jobs = scrape_jobs(
                site_name=["linkedin", "google"],
                search_term=SEARCH_QUERY_JOBSPY,
                location=market['location'],
                results_wanted=25,
                hours_old=72,
                country_indeed=market['country'],
                is_remote=is_remote_search,
                description_formatting="markdown"
            )
            
            if not jobs.empty: 
                # Tag the results so we know which search found them
                jobs['market_tag'] = f"{market['country'].upper()}/{market['location']}"
                all_data.append(jobs)
                print(f"✅ Found {len(jobs)} potential leads.")
                
        except Exception as e:
            # This prevents a single site error from killing the whole script
            print(f"⚠️ JobSpy skipped {market['location']} due to error: {e}")

    # --- PART B: STABLE RSS & API FEEDS ---
    # These are your "Unblockable" backups for WWI, Remotive, WWR, and Hacker News
    print("📡 Syncing Stable Feeds (RSS & APIs)...")
    feed_jobs = fetch_stable_feeds()
    if not feed_jobs.empty: 
        feed_jobs['market_tag'] = "Global/StableFeed"
        all_data.append(feed_jobs)

    if not all_data:
        print("📭 No jobs found from any source today.")
        return

    # --- PART C: DEDUPLICATION & ANALYSIS ---
    # Combine everything and drop duplicates based on the URL
    combined = pd.concat(all_data).drop_duplicates(subset=['job_url'])
    processed_listings = []

    for _, row in combined.iterrows():
        # Pass data into your Scoring & Blacklist engine
        score, skills = analyze_job(
            row['title'], 
            row['company'], 
            row.get('description', ''), 
            row.get('location', 'Remote')
        )
        
        # Only keep high-quality matches
        if score >= 1:
            processed_listings.append({
                **row, 
                "score": score, 
                "skills": skills,
                "origin_tag": row.get('market_tag', 'Direct')
            })
    
    # Sort by Score (Highest first)
    processed_listings.sort(key=lambda x: x['score'], reverse=True)

    # --- PART D: EMAIL DISPATCH ---
    if processed_listings:
        # Generate the HTML list from processed_listings
        # (Using the HTML card logic from the previous version)
        html_report = generate_html_report(processed_listings) 
        send_email(html_report, len(processed_listings))
        print(f"🚀 Success! {len(processed_listings)} leads sent to your inbox.")
    else:
        print("🕵️ No jobs passed the score threshold or blacklist today.")

if __name__ == "__main__":
    run_agent()
