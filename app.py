import streamlit as st
import pandas as pd
import requests
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load env but prioritize Streamlit Secrets for cloud hosting
load_dotenv()
TICKETMASTER_API_KEY = st.secrets.get("TICKETMASTER_API_KEY", os.getenv("TICKETMASTER_API_KEY"))
CITY = os.getenv("CITY", "Austin")

st.set_page_config(page_title="Austin Concert Agent", page_icon="🎸", layout="wide")

# --- CUSTOM CSS ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; color: white; }
    .stButton>button { width: 100%; border-radius: 5px; height: 3em; background-color: #1DB954; color: white; }
    .concert-card { border: 1px solid #333; padding: 15px; border-radius: 10px; margin-bottom: 10px; background-color: #1a1c24; }
    .match-tag { background-color: #1DB954; color: black; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8em; }
    </style>
    """, unsafe_allow_index=True)

st.title("🎸 Austin Concert Agent")
st.markdown("### Personalized Show Discovery powered by 10 Years of Streaming History")

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

# --- SEARCH LOGIC ---
@st.cache_data(ttl=3600)
def get_concerts(city):
    if not TICKETMASTER_API_KEY:
        return []
    url = f"https://app.ticketmaster.com/discovery/v2/events.json?apikey={TICKETMASTER_API_KEY}&city={city}&classificationName=music&size=100&sort=date,asc"
    resp = requests.get(url).json()
    return resp.get("_embedded", {}).get("events", [])

events = get_concerts(CITY)

# --- RANKING ---
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

# --- DISPLAY ---
st.write(f"Showing top results for **{CITY}**:")

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
        """, unsafe_allow_index=True)

if not ranked_events:
    st.info("No concerts found. Check your API key!")
