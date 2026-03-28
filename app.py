import streamlit as st
import requests
import os
import json
import time
import re
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai
from tools import (
    search_concerts, get_distance_to_venue, send_concert_sms, get_venue_details,
    search_small_venue_calendar, search_side_by_side, load_artist_profile,
    get_recent_setlist, make_gcal_url, get_presale_alerts, match_artist_to_event,
    add_venue_details,
)
from spotify_auth import get_auth_url, exchange_code, build_live_profile, get_related_artists
from db import (
    get_or_create_user, get_past_shows, log_attendance, get_unconfirmed_clicks,
    save_message, load_chat_history, clear_chat_history, mark_purchased,
)

# ---------- Config ----------
load_dotenv()
TICKETMASTER_API_KEY = st.secrets.get("TICKETMASTER_API_KEY", os.getenv("TICKETMASTER_API_KEY"))
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", os.getenv("GEMINI_API_KEY"))
CITY = os.getenv("CITY", "Austin")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ---------- Spotify OAuth Callback ----------
query_params = st.query_params
if "code" in query_params and "sp_token" not in st.session_state:
    with st.spinner("Connecting to Spotify..."):
        try:
            sp, user_id, display_name = exchange_code(query_params["code"])
            st.session_state["sp_client"] = sp
            st.session_state["sp_user_id"] = user_id
            st.session_state["sp_display_name"] = display_name
            sp_profile, sp_artist_ids = build_live_profile(sp)
            st.session_state["sp_profile"] = sp_profile
            st.session_state["sp_artist_ids"] = sp_artist_ids
            st.session_state["sp_token"] = True
            st.session_state["mode"] = "Connect Spotify"
            get_or_create_user(user_id, display_name)
            st.session_state["db_user_id"] = user_id
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Spotify connection failed: {e}")
            st.query_params.clear()

st.set_page_config(page_title="Austin Concert Agent", page_icon="🎸", layout="wide")

# ---------- Cached helpers ----------
# Named to match what Gemini calls — must match tool name in system prompt
@st.cache_data(ttl=3600)
def search_small_venue_calendar_cached(venue_name: str):
    """Search indie/small venue shows from Showlist Austin + Side By Side Shows."""
    return search_small_venue_calendar(venue_name)

@st.cache_data(ttl=3600)
def search_side_by_side_cached():
    """Browse all upcoming indie/niche shows from sidebysideshows.com."""
    return search_side_by_side()

@st.cache_data(ttl=3600)
def get_picks(city):
    if not TICKETMASTER_API_KEY:
        return []
    try:
        url = f"https://app.ticketmaster.com/discovery/v2/events.json?apikey={TICKETMASTER_API_KEY}&city={city}&classificationName=music&size=50&sort=date,asc"
        return requests.get(url, timeout=15).json().get("_embedded", {}).get("events", [])
    except Exception:
        return []

# ---------- Session state ----------
if "messages" not in st.session_state:
    db_uid = st.session_state.get("db_user_id")
    if db_uid:
        saved = load_chat_history(db_uid, limit=20)
        st.session_state.messages = saved if saved else []
    else:
        st.session_state.messages = []
if "mode" not in st.session_state:
    st.session_state.mode = "Connect Spotify"

# ---------- CSS ----------
with open(Path(__file__).parent / "styles.css") as _css:
    st.markdown(f"<style>{_css.read()}</style>", unsafe_allow_html=True)

TIER_TAG = {
    'superfan': "<span class='tier-pill superfan'>Superfan</span>",
    'fan': "<span class='tier-pill fan'>Fan</span>",
    'casual': "<span class='tier-pill casual'>Casual</span>",
}

# ---------- Sidebar ----------
with st.sidebar:
    st.title("Concert Agent")
    profile = {}

    MODES = ["Connect Spotify", "My History (Tommy)", "Guest Mode"]
    mode = st.selectbox(
        "Persona",
        MODES,
        index=MODES.index(st.session_state.get("mode", "Connect Spotify")),
        key="mode",
    )

    if mode == "Connect Spotify":
        if st.session_state.get("sp_token"):
            display_name = st.session_state.get("sp_display_name", "Spotify User")
            st.success(f"Connected as {display_name}")
            profile = st.session_state.get("sp_profile", {})
            n_superfans = sum(1 for v in profile.values() if v.get("tier") == "superfan")
            superfans = [name.title() for name, info in profile.items() if info.get("tier") == "superfan"][:8]
            st.caption(f"{n_superfans} superfans | {len(profile)} total artists")
            if superfans:
                st.caption("Top: " + ", ".join(superfans))
            if st.button("Disconnect"):
                for key in ["sp_client", "sp_user_id", "sp_display_name", "sp_profile", "sp_token", "db_user_id"]:
                    st.session_state.pop(key, None)
                st.rerun()
        else:
            st.info("Connect Spotify for personalized picks.")
            auth_url = get_auth_url()
            st.link_button("Connect Spotify", auth_url)

    elif mode == "Guest Mode":
        guest_artists = st.text_input("Favorite Artists", "Radiohead, Khruangbin")
        profile = {a.strip().lower(): {'score': 100.0, 'tier': 'superfan'} for a in guest_artists.split(",") if a.strip()}

    elif mode == "My History (Tommy)":
        if Path("data/artist_profile.json").exists():
            with open("data/artist_profile.json", "r") as f:
                data = json.load(f)
            profile = {
                item['artist'].lower(): {'score': item['weighted_score'], 'tier': item.get('tier', 'fan')}
                for item in data
            }
            n_sf = sum(1 for v in profile.values() if v['tier'] == 'superfan')
            st.success(f"Loaded: {n_sf} superfans, {len(profile)} artists")

    st.divider()
    rec_mode = st.radio(
        "Mode", ["Superfan", "Discovery"],
        help="Superfan: known artists only. Discovery: includes similar artists.",
        key="rec_mode",
    )
    st.caption(f"City: {CITY}")
    user_addr = st.text_input("Home Address", value=os.getenv("HOME_ADDRESS", "303 E 38th St, Austin, TX, 78705"))
    os.environ["HOME_ADDRESS"] = user_addr

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Clear Chat"):
            st.session_state.messages = []
            uid = st.session_state.get("db_user_id")
            if uid:
                clear_chat_history(uid)
            st.rerun()
    with col2:
        if st.button("Send Digest"):
            from weekly_digest import send_digest
            with st.spinner("Sending..."):
                send_digest()
                st.success("Sent!")


# ---------- Profile context builder ----------
def get_profile_context():
    rec = st.session_state.get("rec_mode", "Superfan")
    if st.session_state.get("sp_token") and st.session_state.get("sp_profile"):
        live = st.session_state["sp_profile"]
        sorted_artists = sorted(live.items(), key=lambda x: x[1]["score"], reverse=True)[:30]
        superfans = [name.title() for name, info in sorted_artists if info.get("tier") == "superfan"]
        fans = [name.title() for name, info in sorted_artists if info.get("tier") == "fan"]
    elif Path("data/artist_profile.json").exists():
        with open("data/artist_profile.json", "r") as f:
            d = json.load(f)
        top = sorted(d, key=lambda x: x['weighted_score'], reverse=True)[:30]
        superfans = [a['artist'] for a in top if a.get('tier') == 'superfan']
        fans = [a['artist'] for a in top if a.get('tier') == 'fan']
    else:
        return ""

    ctx = ""
    if superfans:
        ctx += f"SUPERFANS (always flag): {', '.join(superfans)}. "
    if fans:
        ctx += f"FANS (strong interest): {', '.join(fans[:15])}. "

    if rec == "Discovery":
        sp = st.session_state.get("sp_client")
        artist_ids = st.session_state.get("sp_artist_ids", {})
        discovery = []
        if sp and artist_ids:
            p = st.session_state.get("sp_profile", {})
            discovery = get_related_artists(sp, artist_ids, p)
        if discovery:
            ctx += f"DISCOVERY TARGETS: {', '.join(discovery)}. "
        ctx += "MODE: Discovery. Surface shows for known artists AND discovery targets. Explain connections."
    else:
        ctx += "MODE: Superfan. Only recommend shows for known artists."
    return ctx


# ---------- Concert card renderer ----------
def render_concert_card(event):
    tag = TIER_TAG.get(event.get('tier'), "")
    gcal_link = make_gcal_url(event['name'], event.get('date', ''), event.get('venue', ''), event.get('url', ''))
    venue = event.get('venue', '')
    date = event.get('date', 'TBD')
    price = event.get('price', '')
    meta_parts = [venue, date]
    if price:
        meta_parts.append(price)
    meta_str = " &middot; ".join(p for p in meta_parts if p)
    st.html(f"""
    <div class="concert-card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
            <span class="card-artist">{event['name']}</span>
            {tag}
        </div>
        <div class="card-meta">{meta_str}</div>
        <div class="card-links">
            <a href="{event.get('url','#')}" target="_blank" class="ticket-link">Tickets &rarr;</a>
            <a href="{gcal_link}" target="_blank" class="cal-link">+ Calendar</a>
        </div>
    </div>
    """)


# ========== MAIN CONTENT ==========
st.title("Austin Concert Agent")

tab_chat, tab_browse, tab_presales, tab_shows = st.tabs(["Chat", "Browse Shows", "Presales", "My Shows"])

# ---------- TAB: Chat ----------
with tab_chat:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    user_input = st.chat_input("What's happening at Mohawk this week?")
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        db_uid = st.session_state.get("db_user_id")
        if db_uid:
            save_message(db_uid, "user", user_input)
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            if not GEMINI_API_KEY:
                st.error("Missing Gemini API Key")
            else:
                with st.spinner("Agent is working..."):
                    profile_ctx = get_profile_context()
                    sys_instr = f"""You are a professional Austin Concert Concierge.
USER TASTE: {profile_ctx}

TOOLS:
1. search_concerts: Ticketmaster. Supports genre, start_date, end_date. Returns prices.
2. search_small_venue_calendar_cached: Indie/small venue shows from Showlist Austin + Side By Side Shows. Requires a venue name.
3. search_side_by_side_cached: Browse ALL upcoming indie/niche shows from sidebysideshows.com. No venue filter needed. Great for discovering niche artists.
4. get_distance_to_venue: Driving time from home.
5. get_venue_details: Parking, vibe, age limits.
6. get_recent_setlist: Recent setlist from setlist.fm.
7. make_gcal_url: Google Calendar link for a show.
8. get_presale_alerts: Active/upcoming presales for superfan artists.
9. add_venue_details: Add new venue to knowledge base.

RULES:
- For specific small venues (Mohawk, Hole in the Wall, etc.), use search_small_venue_calendar_cached.
- For browsing all indie/niche shows (no specific venue), use search_side_by_side_cached.
- If Ticketmaster returns nothing, fall back to search_small_venue_calendar_cached.
- When recommending a known artist's show, call get_recent_setlist.
- Use price field when user asks about budget.
- Use start_date/end_date when user asks about time ranges.
- Be proactive with distances and calendar links. NO LaTeX.
- EFFICIENCY: When answering a query, call all needed tools in a single round when possible (e.g., search_concerts + get_presale_alerts together) rather than one at a time. Minimize total API round-trips."""

                    models = ['gemini-2.5-flash-lite', 'gemini-2.5-flash']
                    success = False
                    retry_wait = 0

                    # Throttle: enforce minimum 2s between Gemini API calls
                    if "last_gemini_call" in st.session_state:
                        elapsed = time.time() - st.session_state.last_gemini_call
                        if elapsed < 2.0:
                            time.sleep(2.0 - elapsed)

                    for model_name in models:
                        try:
                            model = genai.GenerativeModel(
                                model_name=model_name,
                                tools=[search_concerts, get_distance_to_venue, send_concert_sms,
                                       get_venue_details, search_small_venue_calendar_cached,
                                       search_side_by_side_cached, get_recent_setlist,
                                       make_gcal_url, get_presale_alerts, add_venue_details],
                                system_instruction=sys_instr,
                            )
                            chat = model.start_chat(enable_automatic_function_calling=True)
                            st.session_state.last_gemini_call = time.time()
                            response = chat.send_message(user_input)
                            clean_text = re.sub(r'\$(.*?)\$', r'\1', response.text)
                            st.markdown(clean_text)
                            st.session_state.messages.append({"role": "assistant", "content": clean_text})
                            if db_uid:
                                save_message(db_uid, "assistant", clean_text)
                            success = True
                            break
                        except Exception as e:
                            err_str = str(e)
                            if "429" in err_str or "quota" in err_str.lower():
                                match = re.search(r"retry in (\d+\.?\d*)s", err_str)
                                if match:
                                    retry_wait = max(retry_wait, float(match.group(1)))
                                time.sleep(5)  # brief pause before trying next model
                                continue
                            elif "404" in err_str:
                                continue
                            else:
                                st.error(f"Error: {e}")
                                break

                    if not success:
                        if retry_wait > 0:
                            placeholder = st.empty()
                            for i in range(int(retry_wait), 0, -1):
                                placeholder.html(f'<div class="countdown-box">Quota cooldown: {i}s</div>')
                                time.sleep(1)
                            placeholder.empty()
                            st.info("Cooldown complete. Try again.")
                        else:
                            st.error("All models busy. Try again in 60 seconds.")


# ---------- TAB: Browse Shows ----------
with tab_browse:
    with st.spinner("Loading upcoming shows..."):
        events = get_picks(CITY)

    if not events:
        st.info("No upcoming shows found. Check back later.")
    else:
        ranked = []
        for e in events:
            name = e.get('name', '')
            score, tier = match_artist_to_event(name, profile)
            price_ranges = e.get("priceRanges", [])
            price_str = ""
            if price_ranges:
                low = price_ranges[0].get("min")
                high = price_ranges[0].get("max")
                if low and high:
                    price_str = f"${low:.0f}-${high:.0f}"
                elif low:
                    price_str = f"From ${low:.0f}"
            ranked.append({
                "name": name,
                "venue": e.get('_embedded', {}).get('venues', [{}])[0].get('name', ''),
                "date": e.get('dates', {}).get('start', {}).get('localDate', 'TBD'),
                "url": e.get('url', ''),
                "score": score, "tier": tier, "price": price_str,
            })

        # Split into matched and all
        matched = [r for r in ranked if r['score'] > 0]
        matched.sort(key=lambda x: x['score'], reverse=True)
        unmatched = [r for r in ranked if r['score'] == 0]
        unmatched.sort(key=lambda x: x['date'] or '')

        if matched:
            st.subheader(f"Your Matches ({len(matched)})")
            cols = st.columns(2)
            for i, event in enumerate(matched[:8]):
                with cols[i % 2]:
                    render_concert_card(event)

        st.subheader("All Upcoming Shows")
        display_list = unmatched if matched else ranked
        cols = st.columns(2)
        for i, event in enumerate(display_list[:12]):
            with cols[i % 2]:
                render_concert_card(event)


# ---------- TAB: Presales ----------
with tab_presales:
    st.subheader("Presale Alerts")
    st.caption("Active or upcoming presales for your superfan artists.")

    if st.button("Scan Presales", key="presale_scan"):
        with st.spinner("Scanning Ticketmaster for presales..."):
            alerts = get_presale_alerts(CITY)
            st.session_state["presale_alerts"] = alerts

    alerts_text = st.session_state.get("presale_alerts", "")
    if alerts_text:
        if "No upcoming presales" in alerts_text or "Missing" in alerts_text or "No superfan" in alerts_text or "No artist" in alerts_text:
            st.info(alerts_text)
        else:
            for line in alerts_text.strip().split("\n"):
                if line.startswith("[ACTIVE NOW]"):
                    st.html(f'<div class="presale-active">{line}</div>')
                elif line.startswith("[Starts"):
                    st.html(f'<div class="presale-upcoming">{line}</div>')
                else:
                    st.write(line)
    else:
        st.info("Click 'Scan Presales' to check for upcoming presale windows.")


# ---------- TAB: My Shows ----------
with tab_shows:
    db_uid = st.session_state.get("db_user_id")
    if not db_uid:
        st.info("Connect your Spotify account to track shows.")
    else:
        # Attendance prompts
        unconfirmed = get_unconfirmed_clicks(db_uid, days_old=0)
        from datetime import date as date_type
        today = date_type.today()
        past_events = []
        for ev in unconfirmed:
            try:
                ev_date = date_type.fromisoformat(ev["event_date"]) if ev.get("event_date") else None
                if ev_date and ev_date < today:
                    past_events.append(ev)
            except (ValueError, TypeError):
                pass

        if past_events:
            st.subheader("Did you go?")
            for ev in past_events[:5]:
                col1, col2, col3 = st.columns([4, 1, 1])
                with col1:
                    st.write(f"**{ev['event_name']}** at {ev.get('venue', '?')} ({ev['event_date']})")
                with col2:
                    if st.button("Went", key=f"went_{ev['event_id']}"):
                        log_attendance(db_uid, ev["event_name"], ev.get("venue", ""), ev["event_date"], True)
                        mark_purchased(db_uid, ev["event_id"], True)
                        st.rerun()
                with col3:
                    if st.button("Skipped", key=f"skip_{ev['event_id']}"):
                        log_attendance(db_uid, ev["event_name"], ev.get("venue", ""), ev["event_date"], False)
                        mark_purchased(db_uid, ev["event_id"], False)
                        st.rerun()
            st.divider()

        # Show history
        st.subheader("Show History")
        past = get_past_shows(db_uid, limit=20)
        if past:
            for s in past:
                icon = "+" if s.get('attended') else "-"
                st.write(f"{icon} **{s['event_name']}** at {s.get('venue', '?')} ({s.get('event_date', '?')})")
        else:
            st.caption("No shows logged yet. When you click ticket links and attend shows, they'll appear here.")
