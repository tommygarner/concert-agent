import streamlit as st
import pandas as pd
import requests
import os
import json
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai
from server import core_search_concerts as search_concerts, load_artist_profile

# Load configuration
load_dotenv()
TICKETMASTER_API_KEY = st.secrets.get("TICKETMASTER_API_KEY", os.getenv("TICKETMASTER_API_KEY"))
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", os.getenv("GEMINI_API_KEY"))
CITY = os.getenv("CITY", "Austin")

# Configure Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

st.set_page_config(page_title="Austin Concert Agent", page_icon="🎸", layout="wide")

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; color: white; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #1DB954; color: white; }
    .concert-card { border: 1px solid #333; padding: 15px; border-radius: 10px; margin-bottom: 10px; background-color: #1a1c24; }
    .match-tag { background-color: #1DB954; color: black; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8em; }
    </style>
    """, unsafe_allow_html=True)

st.title("🎸 Austin Concert Agent")
st.markdown("### Personalized Show Discovery powered by 10 Years of Streaming History")

# --- PROFILE HANDLING ---
def get_profile_context():
    if Path("data/artist_profile.json").exists():
        with open("data/artist_profile.json", "r") as f:
            data = json.load(f)
            # Just top 20 for context
            top = sorted(data, key=lambda x: x['weighted_score'], reverse=True)[:20]
            return ", ".join([f"{a['artist']}" for a in top])
    return "No history found."

# --- CHAT INTERFACE ---
st.subheader("🤖 Ask your Concert Agent")
user_input = st.chat_input("Ask: 'Any shows this weekend?' or 'Find indie concerts under $50'")

if user_input:
    if not GEMINI_API_KEY:
        st.error("Please add your GEMINI_API_KEY to secrets/.env")
    else:
        # Display user message
        with st.chat_message("user"):
            st.write(user_input)
            
        # Display assistant response
        with st.chat_message("assistant"):
            with st.spinner("Searching Ticketmaster..."):
                try:
                    profile_context = get_profile_context()
                    system_instruction = f"""
                    You are the Austin Concert Agent. Your ONLY job is to help the user find live music events in Austin.
                    
                    USER'S PREFERRED ARTISTS (for ranking context):
                    {profile_context}
                    
                    GUIDELINES:
                    1. Use the `search_concerts` tool to find live data.
                    2. If you find a show for one of the preferred artists, highlight it!
                    3. Do not discuss personal history details unless they relate to a concert recommendation.
                    4. Keep your output professional, readable, and focused on ticket links.
                    """
                    model = genai.GenerativeModel(
                        model_name='gemini-flash-latest',
                        tools=[search_concerts],
                        system_instruction=system_instruction
                    )
                    chat = model.start_chat(enable_automatic_function_calling=True)
                    response = chat.send_message(user_input)
                    st.write(response.text)
                except Exception as e:
                    st.error(f"Error: {e}")

st.divider()

# --- SIDEBAR: Profile Selection ---
with st.sidebar:
    st.header("Settings")
    mode = st.radio("Persona", ["My History (Tommy)", "Guest Mode (Test it yourself)"])
    
    if mode == "Guest Mode (Test it yourself)":
        guest_artists = st.text_input("Enter 3 favorite artists (comma separated)", "Radiohead, Khruangbin, Leon Bridges")
        profile = {a.strip().lower(): 100.0 for a in guest_artists.split(",")}
    else:
        # Load the Tommy profile we generated
        if Path("data/artist_profile.json").exists():
            with open("data/artist_profile.json", "r") as f:
                data = json.load(f)
                profile = {item['artist'].lower(): item['weighted_score'] for item in data}
            st.success("Loaded 10-year Spotify history!")
        else:
            st.warning("Profile not found. Defaulting to empty.")
            profile = {}

# --- STATIC RANKING (Backup) ---
@st.cache_data(ttl=3600)
def get_static_concerts(city):
    if not TICKETMASTER_API_KEY:
        return []
    url = f"https://app.ticketmaster.com/discovery/v2/events.json?apikey={TICKETMASTER_API_KEY}&city={city}&classificationName=music&size=100&sort=date,asc"
    resp = requests.get(url).json()
    return resp.get("_embedded", {}).get("events", [])

st.subheader("🔥 Top Recommendations for You")
events = get_static_concerts(CITY)

ranked_events = []
for e in events:
    name = e['name']
    score = 0
    matched_artist = None
    for artist, weight in profile.items():
        if artist in name.lower():
            score = weight
            matched_artist = artist
            break
    
    ranked_events.append({
        "name": name,
        "venue": e['_embedded']['venues'][0]['name'],
        "date": e['dates']['start'].get('localDate', 'TBD'),
        "url": e['url'],
        "score": score,
        "matched": matched_artist
    })

ranked_events.sort(key=lambda x: x['score'], reverse=True)

for event in ranked_events[:10]:
    with st.container():
        st.markdown(f"""
        <div class="concert-card">
            <div style="display: flex; justify-content: space-between;">
                <span style="font-size: 1.2em; font-weight: bold;">{event['name']}</span>
                {"<span class='match-tag'>🔥 HIGH MATCH</span>" if event['score'] > 50 else ""}
            </div>
            <div style="color: #888; margin-top: 5px;">📍 {event['venue']} | 📅 {event['date']}</div>
            <a href="{event['url']}" target="_blank" style="color: #1DB954; text-decoration: none; font-size: 0.9em;">Get Tickets →</a>
        </div>
        """, unsafe_allow_html=True)
