"""
Weekly Concert Digest — standalone script.

Scans Ticketmaster for upcoming shows matching the user's artist profile,
checks for active presales, and sends a summary via Twilio SMS.

Schedule with:
  - Windows Task Scheduler: run every Sunday at 10am
  - cron (Linux/Mac): 0 10 * * 0 python weekly_digest.py
  - GitHub Actions: see .github/workflows/weekly-digest.yml
"""

import os
from dotenv import load_dotenv
from tools import search_concerts, get_presale_alerts, load_artist_profile, match_artist_to_event, send_concert_sms

load_dotenv()

CITY = os.getenv("CITY", "Austin")


def build_digest():
    """Build the weekly digest text. Returns (str, bool) — message and whether anything notable was found."""
    profile = load_artist_profile()
    if not profile:
        return "No artist profile found. Run ingest_spotify.py first.", False

    # Get top concerts
    concerts = search_concerts(city=CITY)
    if isinstance(concerts, str):
        matched = []
    else:
        matched = [c for c in concerts if c.get("score", 0) > 0]

    # Get presale alerts
    presales = get_presale_alerts(CITY)
    has_presales = presales and "No upcoming presales" not in presales and "Missing" not in presales

    if not matched and not has_presales:
        return None, False

    lines = ["Your weekly concert digest:"]

    if matched:
        lines.append("")
        lines.append(f"SHOWS ({len(matched)} matches):")
        for c in matched[:5]:
            tier_label = f" [{c['tier'].upper()}]" if c.get("tier") else ""
            price_label = f" {c['price']}" if c.get("price") else ""
            lines.append(f"- {c['name']}{tier_label} @ {c.get('venue', '?')} on {c.get('date', 'TBD')}{price_label}")

    if has_presales:
        lines.append("")
        lines.append("PRESALES:")
        for line in presales.strip().split("\n")[:3]:
            lines.append(f"- {line}")

    return "\n".join(lines), True


def send_digest():
    """Build and send the weekly digest via SMS."""
    message, has_content = build_digest()
    if not has_content:
        print("Nothing notable this week — no SMS sent.")
        return

    # Truncate to SMS-friendly length (Twilio handles multi-part, but keep it readable)
    if len(message) > 1500:
        message = message[:1497] + "..."

    result = send_concert_sms(message)
    print(result)


if __name__ == "__main__":
    send_digest()
