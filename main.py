import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import pandas as pd
from jobspy import scrape_jobs

# --- CONFIGURATION ---
# Added Architect/Staff to search and skills
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
import pandas as pd
import feedparser
from jobspy import scrape_jobs

# --- CONFIGURATION ---
SEARCH_QUERY = "Unity Developer"
MY_SKILLS = [
    "C#", "Unity", "Unreal", "Git", "URP", "HDRP", "Android", "AR", "VR", "XR"
    "Perforce", "C++", "DOTS", "Addressables", "iOS", ".Net", "JavaScript"
    "Optimization", "Architect", "Staff", "Lead", "Python", "Java", "OpenGL",
    "PHP", "Photon", "Normcore", "Multiplayer", "Unity Package Manager", "uGUI",
    "NuGet", "Live Ops", "Data Analysis", "Mobile Games", "Oculus Quest", "WebGL",
    "PostgreSQL", "Software measurement", "Software management", "Unit Testing",
    "Machine Learning", "UI Toolkit", "Digital Twins"
]
MIN_SALARY = 100000 
IGNORE_LIST = ["CyberCoders", "Jobot", "BairesDev", "Toptal", "Staffing", "Recruitment"]
TITLE_BLACKLIST = ["Intern", "Junior", "Associate", "Student", "Graduate"]

def extract_salary(text):
    if not text: return None
    patterns = [r'\$(\d{1,3}(?:,\d{3})+)', r'\$(\d{2,3})k', r'(\d{2,3})k', r'\$(\d{5,6})']
    found_values = []
    for pattern in patterns:
        matches = re.findall(pattern, str(text), re.IGNORECASE)
        for m in matches:
            val = int(m.replace(',', ''))
            if val < 1000: val *= 1000
            found_values.append(val)
    return max(found_values) if found_values else None

def analyze_job(title, company, description):
    t_clean, c_clean, d_clean = str(title).lower(), str(company).lower(), str(description).lower()
    for item in IGNORE_LIST:
        if item.lower() in c_clean: return -1, [], None
    for item in TITLE_BLACKLIST:
        if item.lower() in t_clean: return -1, [], None
    
    sal = extract_salary(d_clean)
    if sal and sal < MIN_SALARY: return -1, [], None

    text = f"{t_clean} {d_clean}"
    found = [s for s in MY_SKILLS if s.lower() in text]
    score = round((len(found) / len(MY_SKILLS)) * 100)
    return score, found, sal

def run_agent():
    # 1. Broad Scrape (including RemoteRocketship targets via Google)
    # We add "remoterocketship" to the search to catch their indexed pages
    jobs = scrape_jobs(
        site_name=["linkedin", "indeed", "google"],
        search_term=f"{SEARCH_QUERY} (site:remoterocketship.com OR remote)",
        location="Remote",
        results_wanted=40,
        hours_old=48,
        description_formatting="markdown"
    )

    processed = []
    if not jobs.empty:
        for _, row in jobs.iterrows():
            score, skills, sal = analyze_job(row['title'], row['company'], row.get('description', ''))
            if score >= 15:
                processed.append({
                    "title": row['title'], "company": row['company'], "url": row['job_url'],
                    "score": score, "skills": skills, "salary": sal,
                    "source": "Aggregator"
                })

    # 2. Direct Niche Feeds
    feeds = ["https://www.workwithindies.com/feed", "https://www.games-career.com/FeedsRss/Programming"]
    for url in feeds:
        f = feedparser.parse(url)
        for entry in f.entries:
            score, skills, sal = analyze_job(entry.title, "", entry.get('summary', ''))
            if score >= 15:
                processed.append({
                    "title": entry.title, "company": "Niche Board", "url": entry.link,
                    "score": score, "skills": skills, "salary": sal, "source": "Direct Feed"
                })

    # Deduplicate & Sort
    unique = {j['url']: j for j in processed}.values()
    sorted_jobs = sorted(unique, key=lambda x: x['score'], reverse=True)

    # --- HTML Generator ---
    job_cards = ""
    for j in sorted_jobs:
        sal_text = f"💰 ${j['salary']:,}" if j['salary'] else "💰 Salary: Competitive/Not Listed"
        tags = "".join([f'<span style="background:#333; color:#00ffa3; padding:2px 6px; border-radius:4px; margin-right:4px; font-size:10px;">{s}</span>' for s in j['skills']])
        
        job_cards += f"""
        <div style="background:#1e1e1e; border:1px solid #333; border-radius:10px; padding:15px; margin-bottom:15px; font-family:sans-serif; color:#fff;">
            <div style="float:right; border:2px solid #00ffa3; border-radius:50%; width:40px; height:40px; line-height:40px; text-align:center; color:#00ffa3; font-weight:bold; font-size:12px;">{j['score']}%</div>
            <h3 style="margin:0; font-size:16px;">{j['title']}</h3>
            <p style="margin:3px 0; color:#888; font-size:12px;">{j['company']} • {j['source']}</p>
            <p style="margin:8px 0; color:#00ffa3; font-size:13px; font-weight:bold;">{sal_text}</p>
            <div style="margin:10px 0;">{tags}</div>
            <a href="{j['url']}" style="background:#00ffa3; color:#000; text-decoration:none; padding:8px 15px; border-radius:4px; font-weight:bold; font-size:11px; display:inline-block;">View Posting</a>
        </div>
        """

    # [Insert send_email function here with job_cards]
    print(f"Report generated with {len(sorted_jobs)} jobs.")

if __name__ == "__main__":
    run_agent()