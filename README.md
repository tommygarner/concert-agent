# 🎸 Austin Concert Agent

An AI-driven concert discovery system that analyzes **10 years of Spotify streaming history** to rank local shows in Austin, TX. Built with the **Google Gemini SDK** and **FastMCP**.

---

## 🚀 Getting Started (for Elton)

### 1. Clone & Install
```bash
git clone https://github.com/tommygarner/concert-agent.git
cd concert-agent
pip install -r requirements.txt
```

### 2. Configure Your Environment
Create a `.env` file in the root directory and add your keys:
```toml
TICKETMASTER_API_KEY=your_key
GEMINI_API_KEY=your_key
GOOGLE_MAPS_API_KEY=your_key
HOME_ADDRESS="Your Address, Austin, TX"

# Optional: For SMS Alerts
TWILIO_ACCOUNT_SID=your_sid
TWILIO_AUTH_TOKEN=your_token
TWILIO_PHONE_NUMBER=your_twilio_number
MY_PHONE_NUMBER=your_personal_number
```

### 3. Personalize with your Spotify History
1. Download your **Extended Streaming History** from Spotify (Privacy Settings).
2. Place the JSON files into a folder named `my_spotify_data/`.
3. Run the ingestion engine:
```bash
python ingest_spotify.py
```
This will generate your personal `data/artist_profile.json`.

### 4. Run the Agent
**Web UI**:
```bash
streamlit run app.py
```

**Terminal Agent**:
```bash
python gemini_agent.py "What shows should I see this weekend?"
```

---

## 🛠️ Tech Stack
- **Google Gemini SDK**: The "Brain" for natural language understanding and tool orchestration.
- **FastMCP**: The protocol layer for AI tool-calling.
- **Ticketmaster API**: Live event discovery.
- **Google Maps API**: Distance and travel time calculations.
- **Twilio API**: Proactive SMS alerts.
- **Pandas**: 10-year streaming history analysis with Time-Decay weighted scoring.
