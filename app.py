import streamlit as st
import pandas as pd
import requests
import os
import json
import time
import re
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai
from tools import search_concerts, get_distance_to_venue, send_concert_sms, get_venue_details, search_small_venue_calendar, load_artist_profile
from spotify_auth import get_auth_url, exchange_code, build_live_profile, get_related_artists

# Load configuration
load_dotenv()
TICKETMASTER_API_KEY = st.secrets.get("TICKETMASTER_API_KEY", os.getenv("TICKETMASTER_API_KEY"))
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", os.getenv("GEMINI_API_KEY"))
CITY = os.getenv("CITY", "Austin")

# Configure Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- SPOTIFY OAUTH CALLBACK ---
# Spotify redirects back with ?code=... — exchange it immediately before anything renders
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
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Spotify connection failed: {e}")
            st.query_params.clear()

st.set_page_config(page_title="Austin Concert Agent", page_icon="🎸", layout="wide")

# --- CACHED SCRAPER ---
@st.cache_data(ttl=21600) # 6 hour cache
def get_cached_small_venue_data(venue_name):
    return search_small_venue_calendar(venue_name)

# --- SESSION STATE ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "query_made" not in st.session_state:
    st.session_state.query_made = False
if "mode" not in st.session_state:
    st.session_state.mode = "Connect Spotify"

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    .main { background-color: white !important; color: black !important; }
    .main .stMarkdown, .main p, .main span, .main label, .main div { color: black !important; }
    .stChatMessage p { color: black !important; }
    [data-testid="stSidebar"] .stMarkdown, [data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] label { 
        color: black !important; 
    }
    .concert-card { border: 1px solid #ddd; padding: 12px; border-radius: 8px; margin-bottom: 8px; background-color: #1a1c24; color: white !important; }
    .concert-card span { color: white !important; }
    .concert-card div { color: #ccc !important; }
    .match-tag { background-color: #1DB954; color: black !important; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 0.75em; }
    .countdown-box { background-color: #fff3cd; color: #856404; padding: 10px; border-radius: 5px; border: 1px solid #ffeeba; margin: 10px 0; font-weight: bold; text-align: center; }
    div[data-testid="stSidebarNav"] { display: none; }
    </style>
    """, unsafe_allow_html=True)

# --- SIDEBAR ---
with st.sidebar:
    st.title("🎸 Settings")
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
            superfans = [
                name.title() for name, info in profile.items()
                if info.get("tier") == "superfan"
            ][:8]
            n_superfans = sum(1 for v in profile.values() if v.get("tier") == "superfan")
            st.caption(f"{n_superfans} superfans from your Spotify history")
            if superfans:
                st.caption("Superfans: " + ", ".join(superfans))
            if st.button("Disconnect"):
                for key in ["sp_client", "sp_user_id", "sp_display_name", "sp_profile", "sp_token"]:
                    st.session_state.pop(key, None)
                st.rerun()
        else:
            st.info("Connect your Spotify account to get personalized recommendations.")
            auth_url = get_auth_url()
            st.link_button("Connect Spotify", auth_url)
            profile = {}

    elif mode == "Guest Mode":
        guest_artists = st.text_input("Favorite Artists", "Radiohead, Khruangbin")
        profile = {a.strip().lower(): {'score': 100.0, 'tier': 'superfan'} for a in guest_artists.split(",")}

    elif mode == "My History (Tommy)":
        if Path("data/artist_profile.json").exists():
            with open("data/artist_profile.json", "r") as f:
                data = json.load(f)
                profile = {
                    item['artist'].lower(): {
                        'score': item['weighted_score'],
                        'tier': item.get('tier', 'fan')
                    }
                    for item in data
                }
            superfans = [item['artist'] for item in data if item.get('tier') == 'superfan'][:8]
            st.success(f"Loaded history — {len([v for v in profile.values() if v['tier'] == 'superfan'])} superfans")
            if superfans:
                st.caption("Superfans: " + ", ".join(superfans))
        else:
            profile = {}

    st.divider()
    rec_mode = st.radio(
        "Recommendation Mode",
        ["Superfan", "Discovery"],
        help="Superfan: only shows for artists you know and love. Discovery: includes artists similar to your superfans.",
        key="rec_mode",
    )

    st.info(f"📍 City: {CITY}")
    user_addr = st.text_input("Home Address", value=os.getenv("HOME_ADDRESS", "303 E 38th St, Austin, TX, 78705"))
    os.environ["HOME_ADDRESS"] = user_addr 
    
    if st.button("Clear Chat"):
        st.session_state.messages = []
        st.session_state.query_made = False
        st.rerun()

    st.write("---")
    st.caption("Tools: Ticketmaster, Maps, Twilio, Venue RAG, Showlist Austin")

# --- MAIN UI ---
st.title("Austin Concert Agent")
if not st.session_state.query_made:
    st.markdown("##### Personalized Discovery via 10-Year Streaming History")

# --- PROFILE CONTEXT ---
def get_profile_context():
    rec_mode = st.session_state.get("rec_mode", "Superfan")

    # Build base lists from whichever profile source is active
    if st.session_state.get("sp_token") and st.session_state.get("sp_profile"):
        live = st.session_state["sp_profile"]
        sorted_artists = sorted(live.items(), key=lambda x: x[1]["score"], reverse=True)[:30]
        superfans = [name.title() for name, info in sorted_artists if info.get("tier") == "superfan"]
        fans = [name.title() for name, info in sorted_artists if info.get("tier") == "fan"]
    elif Path("data/artist_profile.json").exists():
        with open("data/artist_profile.json", "r") as f:
            data = json.load(f)
        top = sorted(data, key=lambda x: x['weighted_score'], reverse=True)[:30]
        superfans = [a['artist'] for a in top if a.get('tier') == 'superfan']
        fans = [a['artist'] for a in top if a.get('tier') == 'fan']
    else:
        return ""

    ctx = ""
    if superfans:
        ctx += f"SUPERFANS (always flag these shows): {', '.join(superfans)}. "
    if fans:
        ctx += f"FANS (strong interest): {', '.join(fans[:15])}. "

    if rec_mode == "Discovery":
        # For Spotify-connected users, fetch Spotify-derived related artists
        sp = st.session_state.get("sp_client")
        artist_ids = st.session_state.get("sp_artist_ids", {})
        if sp and artist_ids:
            profile = st.session_state.get("sp_profile", {})
            discovery = get_related_artists(sp, artist_ids, profile)
        else:
            # Fall back to instructing the agent to reason about similar artists
            discovery = []

        if discovery:
            ctx += f"DISCOVERY TARGETS (artists similar to your superfans — actively search for their shows): {', '.join(discovery)}. "
        ctx += (
            "MODE: Discovery — surface shows for both the user's known artists AND the discovery targets above. "
            "For each discovery recommendation, briefly explain the connection to a superfan artist they know."
        )
    else:
        ctx += "MODE: Superfan — only recommend shows for the user's known artists. Do not suggest artists they haven't heard of."

    return ctx

# --- CHAT DISPLAY ---
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- CHAT ENGINE ---
user_input = st.chat_input("What's happening at Mohawk this week?")

if user_input:
    st.session_state.query_made = True
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
            
    with st.chat_message("assistant"):
        if not GEMINI_API_KEY:
            st.error("Missing API Key")
        else:
            with st.spinner("Agent is working..."):
                profile_ctx = get_profile_context()
                sys_instr = f"""
                You are a professional Austin Concert Concierge.
                USER TASTE: {profile_ctx}
                
                TOOLS: 
                1. `search_concerts`: Major tours (Ticketmaster).
                2. `search_small_venue_calendar`: Local/indie shows (Showlist Austin).
                3. `get_distance_to_venue`: Maps travel time.
                4. `get_venue_details`: Insider parking/vibe info.
                
                RULES: 
                - If a user asks for a specific small venue (like Mohawk, Hole in the Wall, Vegas), use `search_small_venue_calendar`.
                - If Ticketmaster returns nothing, try `search_small_venue_calendar` as a backup.
                - Be proactive with distances. NO LaTeX.
                """
                
                models_to_try = ['gemini-2.0-flash', 'gemini-2.0-flash-lite-001', 'gemini-pro-latest', 'gemini-flash-latest']
                success = False
                retry_wait = 0
                
                for model_name in models_to_try:
                    try:
                        # Wrap the scraper tool to use Streamlit caching
                        def cached_small_venue_tool(venue_name: str):
                            return get_cached_small_venue_data(venue_name)

                        model = genai.GenerativeModel(
                            model_name=model_name,
                            tools=[search_concerts, get_distance_to_venue, send_concert_sms, get_venue_details, cached_small_venue_tool],
                            system_instruction=sys_instr
                        )
                        chat = model.start_chat(enable_automatic_function_calling=True)
                        response = chat.send_message(user_input)
                        
                        clean_text = re.sub(r'\$(.*?)\$', r'\1', response.text)
                        st.markdown(clean_text)
                        st.session_state.messages.append({"role": "assistant", "content": clean_text})
                        success = True
                        break
                    except Exception as e:
                        err_str = str(e)
                        if "429" in err_str or "quota" in err_str.lower():
                            match = re.search(r"retry in (\d+\.?\d*)s", err_str)
                            if match: retry_wait = max(retry_wait, float(match.group(1)))
                            continue
                        elif "404" in err_str: continue
                        else:
                            st.error(f"Error: {e}")
                            break
                
                if not success:
                    if retry_wait > 0:
                        st.warning(f"All models are busy. Starting cooldown...")
                        placeholder = st.empty()
                        for i in range(int(retry_wait), 0, -1):
                            placeholder.html(f'<div class="countdown-box">🕒 Quota Cooldown: {i}s remaining</div>')
                            time.sleep(1)
                        placeholder.empty()
                        st.info("🔄 Cooldown complete! Please try your query again.")
                    else:
                        st.error("All models are currently at their limit. Please try again in 60 seconds.")

# --- STATIC RECOMMENDATIONS ---
if not st.session_state.query_made:
    st.divider()
    st.subheader("🔥 Top Picks for You")
    @st.cache_data(ttl=3600)
    def get_picks(city):
        if not TICKETMASTER_API_KEY: return []
        try:
            url = f"https://app.ticketmaster.com/discovery/v2/events.json?apikey={TICKETMASTER_API_KEY}&city={city}&classificationName=music&size=50&sort=date,asc"
            return requests.get(url).json().get("_embedded", {}).get("events", [])
        except: return []

    events = get_picks(CITY)
    ranked = []
    for e in events:
        score = 0
        tier = None
        name = e['name']
        for art, info in profile.items():
            if art in name.lower():
                score = info['score']
                tier = info['tier']
                break
        ranked.append({
            "name": name, "venue": e['_embedded']['venues'][0]['name'],
            "date": e['dates']['start'].get('localDate', 'TBD'),
            "url": e['url'], "score": score, "tier": tier
        })

    ranked.sort(key=lambda x: x['score'], reverse=True)

    TIER_TAG = {
        'superfan': "<span class='match-tag' style='background:#ff6b35;color:white;'>SUPERFAN</span>",
        'fan':      "<span class='match-tag'>FAN</span>",
    }

    cols = st.columns(2)
    for i, event in enumerate(ranked[:6]):
        with cols[i % 2]:
            tag = TIER_TAG.get(event['tier'], "")
            st.html(f"""
            <div class="concert-card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 1em; font-weight: bold; color: white;">{event['name']}</span>
                    {tag}
                </div>
                <div style="color: #aaa; font-size: 0.85em; margin: 4px 0;">📍 {event['venue']} | 📅 {event['date']}</div>
                <a href="{event['url']}" target="_blank" style="color: #1DB954; text-decoration: none; font-size: 0.85em;">Tickets →</a>
            </div>
            """)
