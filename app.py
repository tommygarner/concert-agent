import streamlit as st
import pandas as pd
import requests
import os
import json
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai
from tools import search_concerts, get_distance_to_venue, send_concert_sms, get_venue_details, load_artist_profile

# Load configuration
load_dotenv()
TICKETMASTER_API_KEY = st.secrets.get("TICKETMASTER_API_KEY", os.getenv("TICKETMASTER_API_KEY"))
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", os.getenv("GEMINI_API_KEY"))
CITY = os.getenv("CITY", "Austin")

# Configure Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

st.set_page_config(page_title="Austin Concert Agent", page_icon="🎸", layout="wide")

# --- SESSION STATE ---
if "messages" not in st.session_state:
    st.session_state.messages = []
if "query_made" not in st.session_state:
    st.session_state.query_made = False

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    /* Main area text white */
    .main .stMarkdown, .main p, .main span { color: white !important; }
    .stChatMessage p { color: white !important; }
    
    /* Sidebar text black */
    [data-testid="stSidebar"] .stMarkdown, [data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] label { 
        color: black !important; 
    }
    
    .concert-card { border: 1px solid #444; padding: 12px; border-radius: 8px; margin-bottom: 8px; background-color: #1a1c24; color: white !important; }
    .match-tag { background-color: #1DB954; color: black !important; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 0.75em; }
    div[data-testid="stSidebarNav"] { display: none; }
    .concert-card span, .concert-card div { color: #ccc !important; }
    .concert-card b, .concert-card strong { color: white !important; }
    </style>
    """, unsafe_allow_html=True)

# --- SIDEBAR ---
with st.sidebar:
    st.title("🎸 Settings")
    mode = st.selectbox("Persona", ["My History (Tommy)", "Guest Mode"])
    
    if mode == "Guest Mode":
        guest_artists = st.text_input("Favorite Artists", "Radiohead, Khruangbin")
        profile = {a.strip().lower(): 100.0 for a in guest_artists.split(",")}
    else:
        if Path("data/artist_profile.json").exists():
            with open("data/artist_profile.json", "r") as f:
                data = json.load(f)
                profile = {item['artist'].lower(): item['weighted_score'] for item in data}
            st.success("Loaded 10-year History")
        else:
            profile = {}

    st.divider()
    st.info(f"📍 City: {CITY}")
    user_addr = st.text_input("Home Address", value=os.getenv("HOME_ADDRESS", "303 E 38th St, Austin, TX, 78705"))
    os.environ["HOME_ADDRESS"] = user_addr 
    
    if st.button("Clear Chat"):
        st.session_state.messages = []
        st.session_state.query_made = False
        st.rerun()

    st.write("---")
    st.caption("Tools: Ticketmaster, Maps, Twilio, Venue RAG")

# --- MAIN UI ---
st.title("Austin Concert Agent")
if not st.session_state.query_made:
    st.markdown("##### Personalized Discovery via 10-Year Streaming History")

# --- PROFILE CONTEXT ---
def get_profile_context():
    if Path("data/artist_profile.json").exists():
        with open("data/artist_profile.json", "r") as f:
            data = json.load(f)
            top = sorted(data, key=lambda x: x['weighted_score'], reverse=True)[:20]
            return ", ".join([f"{a['artist']}" for a in top])
    return ""

# --- CHAT DISPLAY ---
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- CHAT ENGINE ---
user_input = st.chat_input("How far is the Mt. Joy show from my house?")

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
                try:
                    profile_ctx = get_profile_context()
                    sys_instr = f"""
                    You are a professional Austin Concert Concierge.
                    USER TASTE: {profile_ctx}
                    TOOLS: `search_concerts`, `get_distance_to_venue`, `send_concert_sms`, `get_venue_details`.
                    RULES: Be proactive with distances and venue info. NO LaTeX.
                    """
                    model = genai.GenerativeModel(
                        model_name='gemini-flash-latest',
                        tools=[search_concerts, get_distance_to_venue, send_concert_sms, get_venue_details],
                        system_instruction=sys_instr
                    )
                    chat = model.start_chat(enable_automatic_function_calling=True)
                    response = chat.send_message(user_input)
                    st.markdown(response.text)
                    st.session_state.messages.append({"role": "assistant", "content": response.text})
                except Exception as e:
                    st.error(f"Error: {e}")

# --- STATIC RECOMMENDATIONS (Only visible before first query) ---
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
        name = e['name']
        for art, w in profile.items():
            if art in name.lower(): score = w; break
        ranked.append({
            "name": name, "venue": e['_embedded']['venues'][0]['name'],
            "date": e['dates']['start'].get('localDate', 'TBD'),
            "url": e['url'], "score": score
        })

    ranked.sort(key=lambda x: x['score'], reverse=True)
    cols = st.columns(2)
    for i, event in enumerate(ranked[:6]):
        with cols[i % 2]:
            st.markdown(f"""
            <div class="concert-card">
                <div style="display: flex; justify-content: space-between;">
                    <span style="font-size: 1em; font-weight: bold;">{event['name']}</span>
                    {"<span class='match-tag'>MATCH</span>" if event['score'] > 0 else ""}
                </div>
                <div style="color: #888; font-size: 0.85em; margin: 4px 0;">📍 {event['venue']} | 📅 {event['date']}</div>
                <a href="{event['url']}" target="_blank" style="color: #1DB954; text-decoration: none; font-size: 0.85em;">Tickets →</a>
            </div>
            """, unsafe_allow_html=True)
