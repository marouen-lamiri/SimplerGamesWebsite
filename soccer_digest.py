#!/usr/bin/env python3
"""
Soccer transfer + Juventus news digest.

Pulls headlines from a set of RSS feeds, keeps only the items published in
the lookback window, splits them into two buckets:
  1. "Big transfers today"  -> any item matching transfer-related keywords
  2. "Juventus news"        -> any item mentioning Juventus, plus everything
                               from Juventus-dedicated feeds

An AI-written summary paragraph is generated for each bucket (via the
Claude API) and placed at the top of the email, with the full headline
list kept below for reference.

Designed to run twice a day from a GitHub Action (see
.github/workflows/soccer-digest.yml). No external state/database is used:
each run just looks back LOOKBACK_HOURS hours, so as long as that overlaps
the schedule interval you won't miss anything (a little overlap/duplication
between the morning and evening run is expected and fine).
"""

import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser

try:
    import anthropic
except ImportError:  # library is only needed if AI summaries are enabled
    anthropic = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# General football feeds. Scanned for BOTH transfer keywords and Juventus
# keywords. Feel free to add/remove/replace — if one goes stale or 404s the
# script just skips it and keeps going.
GENERAL_FEEDS = [
    ("BBC Sport Football", "http://feeds.bbci.co.uk/sport/football/rss.xml"),
    ("Sky Sports Football", "https://www.skysports.com/rss/11095"),
    ("ESPN FC", "https://www.espn.com/espn/rss/soccer/news"),
    ("Football Italia", "https://www.football-italia.net/feed"),
]

# Feeds that are already 100% Juventus-focused — everything from these goes
# straight into the Juventus bucket, no keyword filtering needed.
JUVENTUS_FEEDS = [
    ("JuveFC", "https://www.juvefc.com/feed/"),
]

TRANSFER_KEYWORDS = [
    "transfer", "signs", "signing", "signed", "sign for", "deal agreed",
    "medical", "here we go", "move to", "loan move", "loan deal",
    "swap deal", "release clause", "buy-out clause", "official:",
    "unveiled", "unveiling", "announce the signing", "fee agreed",
    "agrees personal terms", "contract terms",
]

JUVENTUS_KEYWORDS = ["juventus", "juve", "bianconeri", "old lady"]

LOOKBACK_HOURS = float(os.environ.get("LOOKBACK_HOURS") or "13")
MAX_ITEMS_PER_SECTION = int(os.environ.get("MAX_ITEMS_PER_SECTION") or "20")

# AI summary settings. Set ANTHROPIC_API_KEY to enable; if it's missing (or
# the call fails for any reason) the script just falls back to the plain
# headline lists with no summary paragraph, rather than failing the job.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
AI_SUMMARY_MAX_TOKENS = 500


# ---------------------------------------------------------------------------
# Fetching & filtering
# ---------------------------------------------------------------------------

def entry_datetime(entry):
    """Return a timezone-aware UTC datetime for an entry, or None."""
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def fetch_feed(name, url):
    try:
        parsed = feedparser.parse(url)
        if parsed.bozo and not parsed.entries:
            print(f"  [warn] {name}: failed to parse ({parsed.bozo_exception})", file=sys.stderr)
            return []
        return parsed.entries
    except Exception as exc:  # noqa: BLE001 - keep the job alive on any feed error
        print(f"  [warn] {name}: {exc}", file=sys.stderr)
        return []


def recent_entries(name, url, cutoff):
    items = []
    for entry in fetch_feed(name, url):
        dt = entry_datetime(entry)
        if dt is None or dt < cutoff:
            continue
        items.append({
            "title": entry.get("title", "(untitled)").strip(),
            "link": entry.get("link", ""),
            "source": name,
            "published": dt,
        })
    return items


def matches_any(text, keywords):
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def collect():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    transfers, juventus = [], []
    seen_transfer_links, seen_juve_links = set(), set()

    print(f"Looking back {LOOKBACK_HOURS}h (since {cutoff.isoformat()})")

    for name, url in GENERAL_FEEDS:
        print(f"Fetching {name} ...")
        for item in recent_entries(name, url, cutoff):
            title = item["title"]
            if matches_any(title, TRANSFER_KEYWORDS) and item["link"] not in seen_transfer_links:
                transfers.append(item)
                seen_transfer_links.add(item["link"])
            if matches_any(title, JUVENTUS_KEYWORDS) and item["link"] not in seen_juve_links:
                juventus.append(item)
                seen_juve_links.add(item["link"])

    for name, url in JUVENTUS_FEEDS:
        print(f"Fetching {name} ...")
        for item in recent_entries(name, url, cutoff):
            if item["link"] not in seen_juve_links:
                juventus.append(item)
                seen_juve_links.add(item["link"])

    transfers.sort(key=lambda i: i["published"], reverse=True)
    juventus.sort(key=lambda i: i["published"], reverse=True)

    return transfers[:MAX_ITEMS_PER_SECTION], juventus[:MAX_ITEMS_PER_SECTION]


# ---------------------------------------------------------------------------
# AI summary
# ---------------------------------------------------------------------------

def _headline_block(items):
    return "\n".join(f"- {item['title']} ({item['source']})" for item in items) or "(none)"


def generate_ai_summary(transfers, juventus):
    """Ask Claude for a short prose summary of the two headline lists.

    Returns None if summarization isn't available/enabled or the call
    fails, so callers can fall back to showing the plain headline lists
    without a summary. Never raises.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("No ANTHROPIC_API_KEY set — skipping AI summary.")
        return None
    if anthropic is None:
        print("anthropic package not installed — skipping AI summary.")
        return None
    if not transfers and not juventus:
        return None

    prompt = f"""You write a short, punchy soccer news briefing for a fan's email digest.

Below are two lists of real headlines gathered from RSS feeds in the last several hours:
one about major transfers across world football, one about Juventus specifically.

TRANSFER HEADLINES:
{_headline_block(transfers)}

JUVENTUS HEADLINES:
{_headline_block(juventus)}

Write two short paragraphs (2-4 sentences each), in your own words:
1. "Transfers" — the biggest/most notable transfer storylines from the list above.
2. "Juventus" — what's happening with Juventus specifically from the list above.

Rules:
- Base this ONLY on the headlines given, don't invent facts or scores not implied by them.
- Don't quote headlines verbatim, paraphrase them.
- If a list is "(none)", say briefly that there's nothing notable in that category right now.
- Plain text only, no markdown, no headers — just the two paragraphs separated by a blank line.
- Skip any preamble like "Here's a summary" — start directly with the first paragraph."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=AI_SUMMARY_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 - never let a summary failure kill the run
        print(f"  [warn] AI summary failed: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Email formatting & sending
# ---------------------------------------------------------------------------

def format_section_html(title, items, empty_msg):
    if not items:
        return f"<h2>{title}</h2><p style='color:#666;'>{empty_msg}</p>"
    rows = []
    for item in items:
        time_str = item["published"].strftime("%a %H:%M UTC")
        rows.append(
            "<li style='margin-bottom:10px;'>"
            f"<a href='{item['link']}' style='font-weight:600;text-decoration:none;color:#1a0dab;'>{item['title']}</a>"
            f"<br><span style='color:#666;font-size:12px;'>{item['source']} &middot; {time_str}</span>"
            "</li>"
        )
    return f"<h2>{title}</h2><ul style='list-style:none;padding-left:0;'>{''.join(rows)}</ul>"


def format_summary_html(summary):
    if not summary:
        return ""
    paragraphs = "".join(f"<p style='margin:0 0 12px 0;'>{p.strip()}</p>" for p in summary.split("\n\n") if p.strip())
    return (
        "<div style='background:#f5f7fa;border-radius:8px;padding:16px 18px;margin-bottom:20px;'>"
        "<p style='margin:0 0 8px 0;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#888;'>AI Summary</p>"
        f"{paragraphs}"
        "</div>"
    )


def build_email_html(transfers, juventus, summary=None):
    now_str = datetime.now(timezone.utc).strftime("%A %d %B %Y, %H:%M UTC")
    summary_html = format_summary_html(summary)
    transfers_html = format_section_html(
        "🌍 Big Transfers Today", transfers, "No major transfer headlines in this window."
    )
    juventus_html = format_section_html(
        "⚪⚫ Juventus News", juventus, "No Juventus headlines in this window."
    )
    return f"""
    <html>
      <body style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:auto;">
        <p style="color:#666;font-size:12px;">Soccer digest &middot; {now_str}</p>
        {summary_html}
        {transfers_html}
        <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
        {juventus_html}
      </body>
    </html>
    """


def build_email_text(transfers, juventus, summary=None):
    lines = []
    if summary:
        lines.append("AI SUMMARY")
        lines.append(summary)
        lines.append("")
    lines.append("BIG TRANSFERS TODAY")
    if transfers:
        for item in transfers:
            lines.append(f"- {item['title']} ({item['source']})\n  {item['link']}")
    else:
        lines.append("(none in this window)")
    lines.append("")
    lines.append("JUVENTUS NEWS")
    if juventus:
        for item in juventus:
            lines.append(f"- {item['title']} ({item['source']})\n  {item['link']}")
    else:
        lines.append("(none in this window)")
    return "\n".join(lines)


def send_email(subject, html_body, text_body):
    # SMTP host/port aren't secrets by default — Gmail's are used unless
    # overridden. Override via repo *variables* (not secrets) if you use a
    # different provider, e.g. Outlook: smtp-mail.outlook.com / 587.
    smtp_host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    smtp_port = int(os.environ.get("SMTP_PORT") or "587")
    email_sender = os.environ["EMAIL_SENDER"]
    email_password = os.environ["EMAIL_PASSWORD"]
    email_receiver = os.environ["EMAIL_RECEIVER"]  # comma-separated list allowed

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_sender
    msg["To"] = email_receiver
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    recipients = [addr.strip() for addr in email_receiver.split(",") if addr.strip()]

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(email_sender, email_password)
        server.sendmail(email_sender, recipients, msg.as_string())

    print(f"Email sent to {email_receiver}")


def main():
    transfers, juventus = collect()
    print(f"Found {len(transfers)} transfer item(s), {len(juventus)} Juventus item(s)")

    summary = generate_ai_summary(transfers, juventus)
    print("AI summary generated." if summary else "No AI summary (skipped or unavailable).")

    subject = f"⚽ Soccer Digest — {len(transfers)} transfers, {len(juventus)} Juventus"
    html_body = build_email_html(transfers, juventus, summary)
    text_body = build_email_text(transfers, juventus, summary)

    dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    if dry_run:
        print("DRY_RUN set — not sending email. Preview:\n")
        print(text_body)
        return

    send_email(subject, html_body, text_body)


if __name__ == "__main__":
    main()