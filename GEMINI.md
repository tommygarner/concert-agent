    1 # Austin Concert Discovery Agent (Google ADK)
    2
    3 ## Project Overview
    4 An automated agent built with the **Google Agent Development Kit (ADK)** that monitors upcoming concerts in Austin
      TX, tailored to the user's personal taste. It combines historical listening data with a human-in-the-loop approval
      system.
    5
    6 ## Core Tech Stack
    7 - **Agent Framework:** Google ADK (Agent Development Kit)
    8 - **Model:** Gemini 1.5 Pro / Flash
    9 - **Data Source:** Spotify Extended Streaming History (JSON)
   10 - **APIs:** Ticketmaster Discovery API, SeatGeek API
   11 - **UI/UX:** Streamlit (Human-in-the-loop dashboard)
   12 - **State Management:** SQLite (Local database)
   13 - **Notification:** SMTP/Email (Bi-weekly)
   14
   15 ## The Multi-Agent Orchestration
   16 1.  **The Historian (Spotify Analyst):**
   17     - Analyzes history back to 2016.
   18     - Applies **Exponential Decay** to prioritize recent favorites over "zombie" artists.
   19     - Formula: `Score = 0.5 ^ (days_since_play / 90)`.
   20 2.  **The Gatekeeper (Human-in-the-Loop):**
   21     - Moves new high-scoring artists to a `PENDING` state in SQLite.
   22     - Interfaces with the Streamlit UI for user approval/veto.
   23 3.  **The Scout (Concert Finder):**
   24     - Queries Ticketmaster/SeatGeek for approved artists.
   25     - Filters specifically for the Austin area.
   26 4.  **The Matchmaker (Discovery):**
   27     - Suggests similar artists for shows in Austin based on approved favorites.
   28 5.  **The Secretary (Notifier):**
   29     - Aggregates findings and sends bi-weekly emails.
   30
   31 ## Database Schema (SQLite)
   32 - **`artist_preferences`**: `artist_name`, `interest_score`, `status` (PENDING, APPROVED, VETOED), `last_updated`.
   33 - **`concert_alerts`**: `event_id`, `artist_name`, `venue`, `date`, `url`, `notified_status`.
   34
   35 ## Current Status & Roadmap
   36 - [x] Spotify Parser Logic (Pandas + Exponential Decay)
   37 - [ ] SQLite Database Setup & Management Tool
   38 - [ ] Ticketmaster/SeatGeek Tool Integration
   39 - [ ] ADK Agent definitions (`agent.py`)
   40 - [ ] Streamlit "Gatekeeper" Dashboard
   41 - [ ] Email Notification Service
   42
   43 ## Development Mandates
   44 - **Surgical Updates:** Use Google ADK's modular tool system for all external calls.
   45 - **Privacy:** Never upload raw Spotify JSONs to an LLM; only send aggregated scores/names.
   46 - **Persistence:** Ensure state is saved so users aren't alerted to the same show twice.