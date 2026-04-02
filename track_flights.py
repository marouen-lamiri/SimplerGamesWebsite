import os
import smtplib
import feedparser
import requests
from bs4 import BeautifulSoup
import time
import random
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
import json
import math
import hashlib
import re

# --- CONFIGURATION ---
FLIGHTS_CONFIG = os.getenv("FLIGHTS_CONFIG", json.dumps([
    {"from": "YUL", "to": "TUN", "date": "2025-06-20", "return_date": "2025-07-11", "label": "YUL → TUN"},
    {"from": "YUL", "to": "TUN", "date": "2025-06-20", "return_date": "2025-07-18", "label": "YUL → TUN"},
    {"from": "YUL", "to": "TUN", "date": "2025-06-29", "return_date": "2025-08-03", "label": "YUL → TUN"},
]))
FLIGHTS = json.loads(FLIGHTS_CONFIG)

# SerpAPI key – free tier gives 100 searches/month (serpapi.com)
# If not set, the Kayak scraper is used as fallback.
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

# Alert when price drops this % or more vs. the first recorded price (0 = off)
ALERT_THRESHOLD_PCT = float(os.getenv("ALERT_THRESHOLD_PCT", "10"))

# Path to the rolling price history JSON (committed back to the repo by the workflow)
PRICE_HISTORY_FILE = os.getenv("PRICE_HISTORY_FILE", "price_history.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# --- PRICE SOURCES ---

def fetch_price_serpapi(origin, destination, dep_date, return_date=None):
    """
    Fetch cheapest price via SerpAPI's Google Flights engine.
    Free tier: 100 searches/month – https://serpapi.com/google-flights-api
    Fetches round-trip if return_date is provided, one-way otherwise.
    """
    try:
        params = {
            "engine":         "google_flights",
            "departure_id":   origin,
            "arrival_id":     destination,
            "outbound_date":  dep_date,
            "currency":       "USD",
            "hl":             "en",
            "type":           "1" if return_date else "2",  # 1 = round-trip, 2 = one-way
            "api_key":        SERPAPI_KEY,
        }
        if return_date:
            params["return_date"] = return_date
        resp = requests.get("https://serpapi.com/search", params=params, timeout=20)
        data = resp.json()

        # Pull the lowest price from best_flights or other_flights
        prices = []
        for section in ("best_flights", "other_flights"):
            for itinerary in data.get(section, []):
                p = itinerary.get("price")
                if p:
                    prices.append(float(p))

        return min(prices) if prices else None
    except Exception as e:
        print(f"⚠️ SerpAPI error for {origin}→{destination}: {e}")
        return None


def fetch_price_kayak(origin, destination, dep_date, return_date=None):
    """
    Scrape the lowest price shown on Kayak's flight search page.
    Uses BeautifulSoup – falls back gracefully if the page structure changes.
    Supports round-trip when return_date is provided.
    """
    try:
        if return_date:
            url = f"https://www.kayak.com/flights/{origin}-{destination}/{dep_date}/{return_date}?sort=price_a"
        else:
            url = f"https://www.kayak.com/flights/{origin}-{destination}/{dep_date}?sort=price_a"

        print(f"  🌐 Scraping Kayak for {origin}→{destination}...", end=" ")
        time.sleep(random.uniform(2, 4))   # polite delay, same pattern as main.py

        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Kayak renders prices in elements with class containing "price-text" or "above-button"
        candidates = []
        for tag in soup.select("[class*='price-text'], [class*='above-button'], [class*='totalPrice']"):
            text = tag.get_text(strip=True).replace("$", "").replace(",", "")
            m = re.search(r"\d{2,5}", text)
            if m:
                candidates.append(float(m.group()))

        return min(candidates) if candidates else None
    except Exception as e:
        print(f"⚠️ Kayak scrape error for {origin}→{destination}: {e}")
        return None


def fetch_price_tunisair(origin, destination, dep_date, return_date=None):
    """
    Scrape lowest price from Tunisair's public booking search.
    Tunisair serves routes through Tunis (TUN) – most useful for TUN-origin flights.
    Supports round-trip when return_date is provided.
    """
    try:
        dep_fmt = datetime.strptime(dep_date, "%Y-%m-%d").strftime("%d/%m/%Y")
        trip_type = "OW"
        ret_param = ""
        if return_date:
            trip_type = "RT"
            ret_fmt   = datetime.strptime(return_date, "%Y-%m-%d").strftime("%d/%m/%Y")
            ret_param = f"&retDate={ret_fmt}"

        url = (
            "https://booking.tunisair.com/IBE5/IBE5/#/search?"
            f"lang=en&origin={origin}&destination={destination}"
            f"&depDate={dep_fmt}{ret_param}&paxAdult=1&paxChild=0&paxInfant=0&tripType={trip_type}"
        )
        print(f"  🌐 Scraping Tunisair for {origin}→{destination}...", end=" ")
        time.sleep(random.uniform(2, 4))

        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        candidates = []
        # Look for price patterns like "350 TND" or "$420" in any text node
        for tag in soup.find_all(string=re.compile(r"\d{2,5}")):
            m = re.search(r"(\d{2,5}(?:\.\d{1,2})?)", tag)
            if m:
                val = float(m.group(1))
                if 50 < val < 5000:   # sanity range
                    candidates.append(val)

        return min(candidates) if candidates else None
    except Exception as e:
        print(f"⚠️ Tunisair scrape error for {origin}→{destination}: {e}")
        return None


def fetch_price_airfrance(origin, destination, dep_date, return_date=None):
    """
    Scrape lowest price from Air France's public low-fare finder.
    Air France exposes a JSON endpoint used by their booking widget.
    Supports round-trip when return_date is provided.
    """
    try:
        ret_param = f"&returnDate={return_date}" if return_date else ""
        trip_type = "ROUND_TRIP" if return_date else "ONE_WAY"
        url = (
            "https://www.airfrance.com/api/offers/searchFlights"
            f"?origin={origin}&destination={destination}"
            f"&departureDate={dep_date}{ret_param}&tripType={trip_type}"
            f"&cabinClass=ECONOMY&adults=1&children=0&infants=0&directFlight=false&currency=USD"
        )
        print(f"  🌐 Scraping Air France for {origin}→{destination}...", end=" ")
        time.sleep(random.uniform(2, 4))

        resp = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=20)

        # Try JSON first (API response)
        try:
            data = resp.json()
            prices = []
            # Walk common Air France API shapes
            for key in ("offers", "flights", "results", "data"):
                items = data.get(key, [])
                if isinstance(items, list):
                    for item in items:
                        for price_key in ("price", "amount", "totalPrice", "lowestPrice"):
                            p = item.get(price_key)
                            if p:
                                try:
                                    prices.append(float(str(p).replace(",", "")))
                                except ValueError:
                                    pass
            if prices:
                return min(prices)
        except ValueError:
            pass

        # Fallback: HTML scrape
        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = []
        for tag in soup.select("[class*='price'], [class*='Price'], [class*='fare'], [class*='amount']"):
            text = tag.get_text(strip=True).replace("$", "").replace(",", "").replace("€", "").replace("USD", "")
            m = re.search(r"(\d{2,5}(?:\.\d{1,2})?)", text)
            if m:
                val = float(m.group(1))
                if 50 < val < 5000:
                    candidates.append(val)

        return min(candidates) if candidates else None
    except Exception as e:
        print(f"⚠️ Air France scrape error for {origin}→{destination}: {e}")
        return None


def fetch_price_royalairmaroc(origin, destination, dep_date, return_date=None):
    """
    Scrape lowest price from Royal Air Maroc's booking search.
    RAM serves routes through Casablanca (CMN).
    Supports round-trip when return_date is provided.
    """
    try:
        trip_type = "ROUND_TRIP" if return_date else "ONE_WAY"
        ret_param = f"&returnDate={return_date}" if return_date else ""
        url = (
            f"https://www.royalairmaroc.com/us-en/booking/flight-search"
            f"?origin={origin}&destination={destination}"
            f"&departureDate={dep_date}{ret_param}&adults=1&children=0&infants=0&tripType={trip_type}"
        )
        print(f"  🌐 Scraping Royal Air Maroc for {origin}→{destination}...", end=" ")
        time.sleep(random.uniform(2, 4))

        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        candidates = []
        for tag in soup.select(
            "[class*='price'], [class*='Price'], [class*='fare'], "
            "[class*='amount'], [class*='Amount'], [class*='total']"
        ):
            text = tag.get_text(strip=True).replace("$", "").replace(",", "").replace("USD", "").replace("MAD", "")
            m = re.search(r"(\d{2,5}(?:\.\d{1,2})?)", text)
            if m:
                val = float(m.group(1))
                if 50 < val < 5000:
                    candidates.append(val)

        return min(candidates) if candidates else None
    except Exception as e:
        print(f"⚠️ Royal Air Maroc scrape error for {origin}→{destination}: {e}")
        return None


def fetch_price(origin, destination, dep_date, return_date=None):
    """
    Cascade through all price sources, return the lowest confirmed price found.
    Order: SerpAPI (Google Flights) → Air France → Royal Air Maroc → Tunisair → Kayak
    Passes return_date to each scraper when provided for round-trip pricing.
    Returns (price, source_label).
    """
    results = []

    if SERPAPI_KEY:
        print(f"📡 SerpAPI → {origin}→{destination}...", end=" ")
        p = fetch_price_serpapi(origin, destination, dep_date, return_date)
        if p:
            print(f"${p:.0f}")
            results.append((p, "Google Flights"))

    p = fetch_price_airfrance(origin, destination, dep_date, return_date)
    if p:
        print(f"${p:.0f}")
        results.append((p, "Air France"))

    p = fetch_price_royalairmaroc(origin, destination, dep_date, return_date)
    if p:
        print(f"${p:.0f}")
        results.append((p, "Royal Air Maroc"))

    p = fetch_price_tunisair(origin, destination, dep_date, return_date)
    if p:
        print(f"${p:.0f}")
        results.append((p, "Tunisair"))

    p = fetch_price_kayak(origin, destination, dep_date, return_date)
    if p:
        print(f"${p:.0f}")
        results.append((p, "Kayak"))

    if not results:
        return None, None

    best_price, best_source = min(results, key=lambda x: x[0])
    return best_price, best_source


# --- HISTORY HELPERS ---
def load_history():
    if os.path.exists(PRICE_HISTORY_FILE):
        with open(PRICE_HISTORY_FILE) as f:
            return json.load(f)
    return {}


def save_history(history):
    with open(PRICE_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


# --- SPARKLINE SVG ---
def _sparkline(prices):
    if len(prices) < 2:
        return ""
    w, h   = 72, 24
    mn, mx = min(prices), max(prices)
    rng    = mx - mn or 1
    n      = len(prices)
    xs     = [round(i * w / (n - 1)) for i in range(n)]
    ys     = [round(h - (p - mn) / rng * h) for p in prices]
    lines  = "".join(
        f'<line x1="{xs[i]}" y1="{ys[i]}" x2="{xs[i+1]}" y2="{ys[i+1]}" '
        f'style="stroke:#00ffa3;stroke-width:1.5;stroke-linecap:round;"/>'
        for i in range(n - 1)
    )
    lx, ly = xs[-1], ys[-1]
    return (
        f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'{lines}'
        f'<circle cx="{lx}" cy="{ly}" r="2.5" fill="#00ffa3"/>'
        f'</svg>'
    )


# --- EMAIL ---
def send_email(html_content, count):
    sender   = os.getenv("EMAIL_SENDER")
    receiver = os.getenv("EMAIL_RECEIVER")
    password = os.getenv("EMAIL_PASSWORD")

    if not all([sender, receiver, password]):
        print("❌ Missing Email Env Vars. Check your GitHub Secrets.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"✈ {count} Flight Prices Tracked: {datetime.now().strftime('%b %d')}"
    msg["From"]    = sender
    msg["To"]      = receiver

    msg.attach(MIMEText(html_content, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, receiver, msg.as_string())
        print(f"✅ Email dispatched with {count} flights.")
    except Exception as e:
        print(f"❌ Email failed: {e}")


# --- HTML REPORT ---
def generate_html_report(processed_listings):
    """Premium Dark Mode report matching the Simpler Intelligence design system."""

    report_html = f"""
    <div style="background-color: #050505; color: #ffffff; padding: 20px; font-family: 'Inter', 'Segoe UI', Helvetica, Arial, sans-serif;">
        <div style="max-width: 600px; margin: 0 auto;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h1 style="color: #00ffa3; font-size: 22px; margin-bottom: 2px; letter-spacing: -0.5px;">Flight Price Tracker</h1>
                    <p style="color: #666; font-size: 12px; margin-top: 0; text-transform: uppercase; letter-spacing: 1px;">
                        {len(processed_listings)} Routes Monitored • {datetime.now().strftime('%d %b %Y')}
                    </p>
                </div>
            </div>
            <hr style="border: 0; border-top: 1px solid #1a1a1a; margin: 20px 0;">
    """

    for flight in processed_listings:
        is_alert     = flight.get("alert", False)
        glow_style   = "box-shadow: 0 0 15px rgba(0, 255, 163, 0.1);" if is_alert else ""
        border_style = "2px solid #00ffa3" if is_alert else "1px solid #222"

        delta     = flight["delta"]
        delta_dir = "▼" if delta < -1 else "▲" if delta > 1 else "—"
        delta_col = "#00ffa3" if delta < -1 else "#ef4444" if delta > 1 else "#444"
        delta_html = (
            f'<span style="color:{delta_col};">{delta_dir} ${abs(delta):.0f} vs. first check</span>'
            if delta_dir != "—" else
            f'<span style="color:{delta_col};">— no change</span>'
        )

        alert_badge = (
            '<span style="background:#eab308;color:#000;font-size:10px;font-weight:700;'
            'padding:2px 8px;border-radius:999px;margin-left:8px;">⚡ DROP</span>'
            if is_alert else ""
        )

        sim_note = (
            f'<span style="color:#444;font-size:10px;"> via {flight.get("source", "")}</span>'
        )

        spark = _sparkline(flight.get("recent_prices", []))

        report_html += f"""
            <div style="background: #0f0f0f; border: {border_style}; border-radius: 12px; padding: 20px; margin-bottom: 16px; {glow_style}">
                <table width="100%" cellspacing="0" cellpadding="0">
                    <tr>
                        <td style="vertical-align: top;">
                            <h2 style="margin: 0; font-size: 17px; color: #ffffff; font-weight: 600;">{flight['label']}{alert_badge}</h2>
                            <p style="margin: 6px 0; color: #888; font-size: 13px;">
                                <strong style="color: #fff;">{flight['from']} → {flight['to']}</strong> • Departs {flight['date']}
                                {"<br><span style='color:#888;'>Returns " + flight['return_date'] + "</span>" if flight.get('return_date') else ""}
                            </p>
                            <div style="margin-top: 4px;">
                                <span style="color: #00ffa3; font-size: 11px; background: rgba(0, 255, 163, 0.1); padding: 2px 6px; border-radius: 3px; font-weight: bold; text-transform: uppercase;">
                                    {"Round Trip" if flight.get('return_date') else "One Way"} • Economy
                                </span>
                            </div>
                        </td>
                        <td style="vertical-align: top; text-align: right; width: 80px;">
                            <div style="color: #00ffa3; font-size: 22px; font-weight: 800;">${flight['price']:.0f}{sim_note}</div>
                            <div style="font-size: 9px; color: #444; text-transform: uppercase; font-weight: bold;">USD</div>
                        </td>
                    </tr>
                </table>

                <div style="margin: 14px 0 8px 0; font-size: 12px;">
                    {delta_html}
                    &nbsp;&nbsp;
                    <span style="color: #444; font-size: 11px;">7-day trend: </span>{spark}
                </div>

                <table width="100%" cellspacing="0" cellpadding="0" style="margin-top: 10px; border-top: 1px solid #1a1a1a; padding-top: 15px;">
                    <tr>
                        <td style="color: #444; font-size: 10px; font-weight: bold; text-transform: uppercase;">
                            Lowest recorded: ${flight['lowest']:.0f}
                        </td>
                        <td style="text-align: right;">
                            <a href="{'https://www.google.com/flights?q=' + urllib.parse.quote(flight['from'] + ' to ' + flight['to']) + ('&return=' + flight['return_date'] if flight.get('return_date') else '')}"
                               style="background-color: #00ffa3; color: #000; padding: 9px 20px; border-radius: 6px; text-decoration: none; font-weight: 800; font-size: 12px; display: inline-block;">
                                Search Flights →
                            </a>
                        </td>
                    </tr>
                </table>
            </div>
        """

    report_html += """
            <div style="text-align: center; margin-top: 40px; padding-top: 20px; border-top: 1px solid #1a1a1a;">
                <p style="color: #333; font-size: 10px; font-weight: bold; text-transform: uppercase; letter-spacing: 2px;">
                    Flight Price Tracker • Powered by GitHub Actions
                </p>
            </div>
        </div>
    </div>
    """
    return report_html


# --- MAIN ---
def run_agent():
    """Main execution loop: Fetches prices, tracks drops, and emails the digest."""
    print(f"\n{'='*56}")
    print(f"  Flight Price Tracker  ·  {datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'='*56}\n")

    history = load_history()
    today   = datetime.now().strftime("%Y-%m-%d")
    processed_listings = []

    for flight in FLIGHTS:
        origin      = flight["from"]
        dest        = flight["to"]
        dep         = flight["date"]
        ret         = flight.get("return_date")   # None = one-way
        label       = flight.get("label", f"{origin} → {dest}")
        trip_type   = "return" if ret else "one-way"
        key         = f"{origin}_{dest}_{dep}" + (f"_{ret}" if ret else "")

        price, source = fetch_price(origin, dest, dep, ret)

        if price is None:
            print(f"⚠️ Could not fetch price for {label} – skipping.")
            continue

        print(f"${price:.2f} [{source}]")

        # Update history
        hist = history.setdefault(key, {"label": label, "date": dep, "prices": []})
        hist["prices"].append({"date": today, "price": price})
        hist["prices"] = hist["prices"][-90:]  # keep 90 days

        all_prices    = [p["price"] for p in hist["prices"]]
        first_price   = all_prices[0]
        lowest_price  = min(all_prices)
        recent_prices = all_prices[-7:]
        delta         = price - first_price
        drop_pct      = (first_price - price) / first_price * 100
        alert         = ALERT_THRESHOLD_PCT > 0 and drop_pct >= ALERT_THRESHOLD_PCT

        if alert:
            print(f"⚡ Price drop alert! {drop_pct:.1f}% below first recorded price.")

        processed_listings.append({
            "key":           key,
            "label":         label,
            "from":          origin,
            "to":            dest,
            "date":          dep,
            "return_date":   ret,
            "trip_type":     trip_type,
            "price":         price,
            "source":        source,
            "delta":         delta,
            "lowest":        lowest_price,
            "recent_prices": recent_prices,
            "alert":         alert,
        })

    save_history(history)
    print(f"\n✅ History saved → {PRICE_HISTORY_FILE}")

    # Sort: alerts first, then by price ascending
    processed_listings.sort(key=lambda x: (not x["alert"], x["price"]))

    if processed_listings:
        html_report = generate_html_report(processed_listings)
        send_email(html_report, len(processed_listings))
        print(f"🚀 Success! {len(processed_listings)} routes sent to your inbox.")
    else:
        print("📭 No flights to report today.")


if __name__ == "__main__":
    run_agent()
