# 🎸 Austin Concert Agent

An AI-driven concert discovery system that analyzes **10 years of Spotify streaming history** to rank local shows in Austin, TX.

---

## Live Interactive Demo
Try the agent below! You can toggle between **My History** (Tommy's 10-year profile) or **Guest Mode** to test it with your own favorite artists.

<iframe 
    src="https://your-app-name.streamlit.app/?embed=true" 
    style="width:100%; height:700px; border:none; border-radius:10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);"
></iframe>

---

## How It Works
1. **Data Ingestion**: I processed 48,000+ entries from a Spotify "Extended Streaming History" export.
2. **Preference Engine**: Applied a **Time Decay** algorithm (1-year half-life) to prioritize current taste while respecting long-term trends.
3. **Agentic Tooling**: Built as an **MCP (Model Context Protocol)** server that interfaces with the Ticketmaster Discovery API.
4. **Ranking Logic**: The agent cross-references live Ticketmaster data with the preference engine to surface "High Match" shows before they sell out.

### Tech Stack
- **Python / Pandas**: Data processing & ranking logic.
- **FastMCP**: Standardized agent protocol for LLM tool-calling.
- **Streamlit**: Interactive frontend.
- **Ticketmaster API**: Real-time event data.
