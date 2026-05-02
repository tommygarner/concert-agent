import streamlit as st
import streamlit.components.v1 as _components
import requests
import os
import json
import time
import re
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from tools import (
    search_concerts, get_distance_to_venue, send_concert_sms, get_venue_details,
    search_small_venue_calendar, search_side_by_side, search_do512, load_artist_profile,
    get_recent_setlist, make_gcal_url, get_presale_alerts, match_artist_to_event,
    add_venue_details, get_similar_artists, get_artist_top_tracks,
    _fetch_do512, _load_setlist_cache,
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
GMAPS_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY", os.getenv("GOOGLE_MAPS_API_KEY"))
CITY = os.getenv("CITY", "Austin")

_genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# ---------- Page config ----------
st.set_page_config(page_title="Austin Concert Agent", page_icon="🎸", layout="wide")

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

# ---------- CSS ----------
with open(Path(__file__).parent / "styles.css") as _css:
    st.markdown(f"<style>{_css.read()}</style>", unsafe_allow_html=True)

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

TIER_TAG = {
    'superfan': "<span class='tier-pill superfan'>Superfan</span>",
    'fan': "<span class='tier-pill fan'>Fan</span>",
    'casual': "<span class='tier-pill casual'>Casual</span>",
}

# ---------- Cached helpers ----------
@st.cache_data(ttl=3600)
def get_picks(city):
    if not TICKETMASTER_API_KEY:
        return []
    try:
        url = (
            f"https://app.ticketmaster.com/discovery/v2/events.json"
            f"?apikey={TICKETMASTER_API_KEY}&city={city}&classificationName=music"
            f"&size=50&sort=date,asc"
        )
        return requests.get(url, timeout=15).json().get("_embedded", {}).get("events", [])
    except Exception:
        return []

# ---------- Profile builder (reads session state / files, no widgets needed) ----------
def _build_profile():
    mode = st.session_state.get("mode", "Connect Spotify")
    if mode == "Connect Spotify" and st.session_state.get("sp_token"):
        return st.session_state.get("sp_profile", {})
    elif mode == "My History (Tommy)":
        if Path("data/artist_profile.json").exists():
            with open("data/artist_profile.json", "r") as f:
                data = json.load(f)
            return {
                item['artist'].lower(): {'score': item['weighted_score'], 'tier': item.get('tier', 'fan')}
                for item in data
            }
    elif mode == "Guest Mode":
        guests = st.session_state.get("guest_artists_input", "Radiohead, Khruangbin")
        return {a.strip().lower(): {'score': 100.0, 'tier': 'superfan'} for a in guests.split(",") if a.strip()}
    return {}

profile = _build_profile()

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
    presale = event.get('presale', '')
    if presale == 'active':
        presale_html = '<div><span class="presale-badge">Presale Active Now</span></div>'
    elif presale == 'upcoming':
        presale_html = '<div><span class="presale-badge upcoming">Presale Coming Soon</span></div>'
    else:
        presale_html = ''
    st.html(f"""
    <div class="concert-card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
            <span class="card-artist">{event['name']}</span>
            {tag}
        </div>
        <div class="card-meta">{meta_str}</div>
        {presale_html}
        <div class="card-links">
            <a href="{event.get('url','#')}" target="_blank" class="ticket-link">Tickets &rarr;</a>
            <a href="{gcal_link}" target="_blank" class="cal-link">+ Calendar</a>
        </div>
    </div>
    """)

# ---------- Rich card helpers ----------
def _find_events_in_text(text, do512_events):
    text_lower = text.lower()
    matched, seen = [], set()
    for evt in do512_events:
        name = evt.get("name", "")
        if not name or len(name) < 3 or name in seen:
            continue
        if name.lower() in text_lower:
            seen.add(name)
            matched.append(evt)
    return matched[:3]

def _get_setlist_snippet(artist_name):
    cache = _load_setlist_cache()
    entry = cache.get(artist_name.lower().strip(), {})
    result = entry.get("result", "")
    if not result:
        return ""
    m = re.search(r'Set \(\d+ songs\): (.+?)(?:\.\s|$)', result)
    if m:
        songs = [s.strip() for s in m.group(1).split(",")][:4]
        return ", ".join(songs)
    return ""

def _get_presale_info(artist_name):
    presale_path = Path("data/presale_cache.json")
    if not presale_path.exists():
        return ""
    try:
        cache = json.loads(presale_path.read_text())
        result = cache.get("result", "")
        artist_lower = artist_name.lower()
        if artist_lower not in result.lower():
            return ""
        for line in result.split("\n"):
            if artist_lower in line.lower():
                return "active" if "ACTIVE NOW" in line else "upcoming"
    except Exception:
        pass
    return ""

def render_rich_card(evt, current_profile):
    from urllib.parse import quote
    from datetime import datetime as _dt

    name = evt.get("name", "")
    venue = evt.get("venue", "")
    address = evt.get("venue_address", "") or evt.get("address", "")
    date_raw = evt.get("date", "")
    price = evt.get("ticket_info", "") or evt.get("price", "")
    ticket_url = evt.get("url", "#")
    image_url = evt.get("image_url", "")

    _, tier = match_artist_to_event(name, current_profile)
    tier_tag = TIER_TAG.get(tier, "")

    date_display = date_raw
    try:
        dt = _dt.strptime(date_raw, "%Y-%m-%d")
        date_display = f"{dt.strftime('%b')} {dt.day}, {dt.year}"
    except Exception:
        pass

    meta_parts = [p for p in [venue, date_display, price] if p]
    meta_str = " &middot; ".join(meta_parts)

    setlist = _get_setlist_snippet(name)
    setlist_html = (
        f'<div class="card-setlist"><span class="card-setlist-label">Last setlist:</span> {setlist}</div>'
        if setlist else ""
    )

    ps = _get_presale_info(name)
    presale_html = ""
    if ps == "active":
        presale_html = '<div class="presale-badge">Presale active now</div>'
    elif ps == "upcoming":
        presale_html = '<div class="presale-badge upcoming">Presale coming soon</div>'

    img_html = (
        f'<div class="rich-card-img"><img src="{image_url}" alt="{name}" /></div>'
        if image_url else ""
    )

    map_query = address if address else (f"{venue}, Austin, TX" if venue else "")
    map_html = ""
    if map_query:
        gmaps_link = f"https://www.google.com/maps/search/?api=1&query={quote(map_query)}"
        if GMAPS_KEY:
            enc = quote(map_query)
            map_src = (
                f"https://maps.googleapis.com/maps/api/staticmap"
                f"?center={enc}&zoom=15&size=600x150"
                f"&markers=color:0xC44D2B%7C{enc}&key={GMAPS_KEY}"
            )
            map_html = (
                f'<div class="rich-card-map">'
                f'<a href="{gmaps_link}" target="_blank">'
                f'<img src="{map_src}" alt="Map of {venue}" /></a></div>'
            )
        else:
            map_html = (
                f'<div style="padding:8px 15px;border-top:1px solid #E5E5E3">'
                f'<a href="{gmaps_link}" target="_blank" '
                f'style="font-size:0.78rem;font-weight:600;color:#2D5F8A;'
                f'text-transform:uppercase;letter-spacing:0.04em;text-decoration:none">'
                f'View on Google Maps</a></div>'
            )

    gcal_url = make_gcal_url(name, date_raw, venue, ticket_url)

    st.html(f"""
    <div class="rich-card">
      <div class="rich-card-top">
        {img_html}
        <div class="rich-card-details">
          <div class="rich-card-header">
            <span class="card-artist">{name}</span>
            {tier_tag}
          </div>
          <div class="card-meta">{meta_str}</div>
          {setlist_html}
          {presale_html}
        </div>
      </div>
      {map_html}
      <div class="rich-card-links">
        <a href="{ticket_url}" target="_blank" class="ticket-link">Tickets &rarr;</a>
        <a href="{gcal_url}" target="_blank" class="cal-link">+ Calendar</a>
      </div>
    </div>
    """)

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

    db_uid = st.session_state.get("db_user_id")
    if db_uid:
        try:
            past = get_past_shows(db_uid, limit=5)
            if past:
                lines = [
                    f"{s.get('event_name','?')} @ {s.get('venue','?')} ({s.get('event_date','')})"
                    for s in past
                ]
                ctx += f" ATTENDED SHOWS: {'; '.join(lines)}."
        except Exception:
            pass

    return ctx


# ========== MAIN CONTENT ==========
st.title("Austin Concert Agent")

tab_chat, tab_browse, tab_presales, tab_shows, tab_settings = st.tabs(
    ["Chat", "Browse Shows", "Presales", "My Shows", "Settings"]
)

# ---------- TAB: Chat ----------
with tab_chat:
    # Consume any queued quick-action query
    _pending_query = st.session_state.pop("_quick_query", None)

    # Scrollable message pane
    chat_pane = st.container(height=520, border=False)
    with chat_pane:
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"] or "")
                if message["role"] == "assistant":
                    for evt in message.get("events", []):
                        render_rich_card(evt, profile)

    # Auto-scroll to bottom
    _components.html("""
    <script>
        (function() {
            function scrollPane() {
                var panes = window.parent.document.querySelectorAll(
                    'section[data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] > div'
                );
                panes.forEach(function(el) {
                    if (el.scrollHeight > el.clientHeight) { el.scrollTop = el.scrollHeight; }
                });
            }
            setTimeout(scrollPane, 150);
        })();
    </script>
    """, height=0)

    # This Weekend quick action
    _today = datetime.now()
    _dow = _today.weekday()
    _days_to_sat = (5 - _dow) if _dow <= 5 else 6
    _sat = _today + timedelta(days=_days_to_sat)
    _sun = _sat + timedelta(days=1)
    _weekend_label = f"This Weekend  —  {_sat.strftime('%b')} {_sat.day}-{_sun.day}"
    if st.button(_weekend_label, key="btn_this_weekend"):
        st.session_state["_quick_query"] = (
            f"What are the best shows this weekend "
            f"({_sat.strftime('%B')} {_sat.day}-{_sun.day}) in Austin? "
            f"Search Ticketmaster, Do512, and Side By Side Shows. "
            f"Use start_date={_sat.strftime('%Y-%m-%d')} and end_date={_sun.strftime('%Y-%m-%d')}."
        )
        st.rerun()

    user_input = st.chat_input("Ask about a show, artist, or venue...") or _pending_query
    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        db_uid = st.session_state.get("db_user_id")
        if db_uid:
            save_message(db_uid, "user", user_input)
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            if not _genai_client:
                st.error("Missing Gemini API Key")
            else:
                with st.spinner("Agent is working..."):
                    profile_ctx = get_profile_context()

                    _has_setlist = bool(
                        st.secrets.get("SETLISTFM_API_KEY", os.getenv("SETLISTFM_API_KEY", ""))
                    )
                    _has_maps = bool(
                        st.secrets.get("GOOGLE_MAPS_API_KEY", os.getenv("GOOGLE_MAPS_API_KEY", ""))
                    )
                    _has_lastfm = bool(
                        st.secrets.get("LASTFM_API_KEY", os.getenv("LASTFM_API_KEY", ""))
                    )

                    _mandatory = ["get_venue_details for the venue", "get_artist_top_tracks for the artist"]
                    if _has_setlist:
                        _mandatory.append("get_recent_setlist for the artist")
                    if _has_maps:
                        _mandatory.append("get_distance_to_venue for travel time")
                    _mandatory_str = " + ".join(_mandatory)

                    _discovery_rule = (
                        "- When the user mentions an unfamiliar artist: call get_similar_artists to surface related acts.\n"
                        if _has_lastfm else ""
                    )

                    sys_instr = f"""You are a professional Austin Concert Concierge.
USER TASTE: {profile_ctx}

TOOLS (use them proactively — never ask permission first):
1. search_concerts: Ticketmaster + Do512 fallback. Supports genre, start_date, end_date. Returns prices.
2. search_small_venue_calendar: Indie/small venue shows (Showlist Austin + Side By Side Shows + Do512). Requires a venue name.
3. search_side_by_side: All upcoming indie/niche shows from sidebysideshows.com. No venue filter needed.
4. search_do512: All upcoming Austin music events from do512.com. Covers acts not on Ticketmaster.
5. get_distance_to_venue: Driving time from home.{"" if _has_maps else " (NOT configured — do not call)"}
6. get_venue_details: Parking, vibe, age limits.
7. get_recent_setlist: Recent setlist from setlist.fm.{"" if _has_setlist else " (NOT configured — do not call)"}
8. make_gcal_url: Google Calendar link for a show.
9. get_presale_alerts: Active/upcoming presales for superfan artists.
10. add_venue_details: Add new venue to knowledge base.
11. get_similar_artists: Find artists similar to a given artist (Last.fm).{"" if _has_lastfm else " (NOT configured — do not call)"}
12. get_artist_top_tracks: Top 5 Spotify tracks for any artist. Call when recommending a show to preview their music.

RULES:
- NEVER say "Would you like me to find more details?" or "Would you like me to search for...?" — just do it immediately.
- A complete show recommendation ALWAYS includes: call {_mandatory_str} in the same response, before writing any prose. No exceptions.
- For venue-specific queries (Mohawk, Hole in the Wall, etc.), use search_small_venue_calendar.
- For indie/small venue shows, call BOTH search_side_by_side AND search_do512 for full coverage.
- ALWAYS when user asks about upcoming shows, what to see this week/weekend, or anything forward-looking: call get_presale_alerts alongside the show search.
{_discovery_rule}- Always include a make_gcal_url link for any recommended show. No LaTeX. No em dashes.
- Use price field when user asks about budget. Use start_date/end_date when user asks about time ranges.
- Search for artists using the EXACT name the user provides. Do NOT rephrase, correct, or substitute artist names.
- If search_concerts returns no results, always try search_do512 before saying nothing was found.
- If get_venue_details returns no data for a venue, omit that section — do not tell the user you have no data.
- EFFICIENCY: Call all needed tools in parallel in a single round when possible."""

                    models = ['gemini-2.5-flash-lite', 'gemini-2.5-flash']
                    success = False
                    retry_wait = 0

                    if "last_gemini_call" in st.session_state:
                        elapsed = time.time() - st.session_state.last_gemini_call
                        if elapsed < 2.0:
                            time.sleep(2.0 - elapsed)

                    prior_msgs = st.session_state.messages[:-1][-20:]
                    chat_history = []
                    for msg in prior_msgs:
                        role = "user" if msg["role"] == "user" else "model"
                        text = msg.get("content") or ""
                        if text:
                            chat_history.append(
                                genai_types.Content(
                                    role=role,
                                    parts=[genai_types.Part(text=text)],
                                )
                            )

                    for model_name in models:
                        try:
                            chat = _genai_client.chats.create(
                                model=model_name,
                                config=genai_types.GenerateContentConfig(
                                    system_instruction=sys_instr,
                                    tools=[search_concerts, get_distance_to_venue, send_concert_sms,
                                           get_venue_details, search_small_venue_calendar,
                                           search_side_by_side, search_do512,
                                           get_recent_setlist, make_gcal_url, get_presale_alerts,
                                           add_venue_details, get_similar_artists,
                                           get_artist_top_tracks],
                                ),
                                history=chat_history,
                            )
                            st.session_state.last_gemini_call = time.time()
                            response = chat.send_message(user_input)
                            raw_text = response.text or ""
                            clean_text = re.sub(r'\$(.*?)\$', r'\1', raw_text)
                            clean_text = clean_text.replace('—', ',').replace('–', ',')
                            st.markdown(clean_text)

                            _NEGATIVE = ("cannot find", "no results", "unable to find",
                                         "couldn't find", "no upcoming", "no shows",
                                         "no concerts", "no events", "check the spelling",
                                         "don't have any information", "no information")
                            if any(p in clean_text.lower() for p in _NEGATIVE):
                                matched_events = []
                            else:
                                try:
                                    do512_evts = _fetch_do512()
                                    matched_events = _find_events_in_text(clean_text, do512_evts)
                                except Exception:
                                    matched_events = []

                            for evt in matched_events:
                                render_rich_card(evt, profile)

                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": clean_text,
                                "events": matched_events,
                            })
                            if db_uid:
                                save_message(db_uid, "assistant", clean_text)
                            success = True
                            st.rerun()
                            break
                        except Exception as e:
                            err_str = str(e)
                            if "429" in err_str or "quota" in err_str.lower():
                                match = re.search(r"retry in (\d+\.?\d*)s", err_str)
                                if match:
                                    retry_wait = max(retry_wait, float(match.group(1)))
                                time.sleep(5)
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
        try:
            get_presale_alerts(CITY)
        except Exception:
            pass

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
                "presale": _get_presale_info(name) if score > 0 else "",
            })

        matched = [r for r in ranked if r['score'] > 0]
        matched.sort(key=lambda x: x['score'], reverse=True)
        unmatched = [r for r in ranked if r['score'] == 0]
        unmatched.sort(key=lambda x: x['date'] or '')

        _browse_uid = st.session_state.get("db_user_id")
        if _browse_uid:
            try:
                _past = get_past_shows(_browse_uid, limit=5)
                _because_sections = {}
                for _show in _past:
                    _artist = _show.get("event_name", "")
                    if not _artist:
                        continue
                    _hits = [
                        r for r in ranked
                        if _artist.lower() in r["name"].lower() and r not in matched
                    ]
                    if _hits:
                        _because_sections[_artist] = _hits[:2]
                for _seed_artist, _hits in _because_sections.items():
                    st.subheader(f"Because you saw {_seed_artist}")
                    cols = st.columns(2)
                    for i, event in enumerate(_hits):
                        with cols[i % 2]:
                            render_concert_card(event)
            except Exception:
                pass

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
        if any(p in alerts_text for p in ("No upcoming presales", "Missing", "No superfan", "No artist")):
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

        st.subheader("Show History")
        past = get_past_shows(db_uid, limit=20)
        if past:
            for s in past:
                icon = "+" if s.get('attended') else "-"
                st.write(f"{icon} **{s['event_name']}** at {s.get('venue', '?')} ({s.get('event_date', '?')})")
        else:
            st.caption("No shows logged yet. When you click ticket links and attend shows, they'll appear here.")


# ---------- TAB: Settings ----------
with tab_settings:
    st.subheader("Profile")

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
            _p = st.session_state.get("sp_profile", {})
            n_sf = sum(1 for v in _p.values() if v.get("tier") == "superfan")
            superfans = [n.title() for n, info in _p.items() if info.get("tier") == "superfan"][:10]
            st.caption(f"{n_sf} superfans | {len(_p)} total artists tracked")
            if superfans:
                st.caption("Top superfans: " + ", ".join(superfans))
            if st.button("Disconnect Spotify"):
                for key in ["sp_client", "sp_user_id", "sp_display_name", "sp_profile",
                            "sp_token", "db_user_id", "sp_artist_ids"]:
                    st.session_state.pop(key, None)
                st.rerun()
        else:
            st.info("Connect your Spotify account for personalized picks based on your listening history.")
            auth_url = get_auth_url()
            st.link_button("Connect Spotify", auth_url)

    elif mode == "Guest Mode":
        st.text_input(
            "Favorite Artists (comma-separated)",
            "Radiohead, Khruangbin",
            key="guest_artists_input",
        )

    elif mode == "My History (Tommy)":
        if Path("data/artist_profile.json").exists():
            n_sf = sum(1 for v in profile.values() if v.get('tier') == 'superfan')
            st.success(f"Profile loaded: {n_sf} superfans, {len(profile)} total artists")
        else:
            st.warning("No profile found. Run `python ingest_spotify.py` to generate one.")

    st.divider()
    st.subheader("Preferences")
    st.radio(
        "Recommendation Mode",
        ["Superfan", "Discovery"],
        help="Superfan: your known artists only. Discovery: includes similar artists you might like.",
        key="rec_mode",
    )
    st.caption(f"City: {CITY}")
    user_addr = st.text_input(
        "Home Address (for driving time estimates)",
        value=os.getenv("HOME_ADDRESS", "303 E 38th St, Austin, TX, 78705"),
    )
    os.environ["HOME_ADDRESS"] = user_addr

    st.divider()
    st.subheader("Actions")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Clear Chat History"):
            st.session_state.messages = []
            uid = st.session_state.get("db_user_id")
            if uid:
                clear_chat_history(uid)
            st.success("Chat cleared.")
    with col2:
        if st.button("Send Weekly Digest"):
            from weekly_digest import send_digest
            with st.spinner("Sending..."):
                send_digest()
                st.success("Sent!")
