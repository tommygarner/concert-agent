# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An automated concert discovery agent for Austin, TX, built around a 5-role multi-agent pipeline. It fetches recent Spotify listening history via the Spotify API, scores artists via exponential decay, runs a human approval step, then queries Ticketmaster for upcoming shows, uses Gemini to suggest similar artists, and sends bi-weekly email digests.

## Running the Agent

Each pipeline phase maps to a CLI flag in `agent.py`:

```bash
# Phase 1: Fetch Spotify plays via API and populate the database
# (browser opens for OAuth on first run; token cached in .cache)
python agent.py --historian

# Phase 2: Approve or veto artists (human-in-the-loop via browser UI)
streamlit run gatekeeper_dashboard.py

# Phase 3: Search Ticketmaster for concerts by approved artists
python agent.py --scout

# Phase 4: Use Gemini to suggest similar artists (stored as PENDING)
python agent.py --matchmaker

# Phase 5: Send email digest of NEW concert alerts
python agent.py --secretary

# Run all phases in sequence
python agent.py --historian --scout --matchmaker --secretary

# Standalone Spotify sync utility
python sync_spotify_to_db.py
```

## Architecture

The pipeline is orchestrated by `ConcertDiscoveryAgent` in `agent.py`, which calls into five tool classes in `tools/`:

| Role | File | Class |
|---|---|---|
| Historian | `tools/spotify_parser.py` | `SpotifyAnalystAPI` |
| Database | `tools/db_manager.py` | `DatabaseManager` |
| Scout | `tools/ticketmaster_scout.py` | `TicketmasterScout` |
| Matchmaker | `tools/matchmaker.py` | `MatchmakerDiscovery` |
| Secretary | `tools/secretary_notifier.py` | `SecretaryNotifier` |

**Gatekeeper UI:** `gatekeeper_dashboard.py` — Streamlit dashboard that reads from the SQLite DB and lets the user APPROVE or VETO PENDING artists before the Scout runs.

**State Machine:**
- Artists: `PENDING` → `APPROVED` or `VETOED`
- Concert alerts: `NEW` → `NOTIFIED`

**Database:** `concert_agent.db` (SQLite, auto-created on first run, gitignored)
- `artist_preferences(artist_name PK, interest_score, status, last_updated)`
- `concert_alerts(event_id PK, artist_name FK, venue, date, url, notified_status)`

## Scoring Algorithm

`SpotifyAnalystAPI.get_top_artists()` calls `sp.current_user_recently_played(limit=50)` and applies exponential decay:
```
Score = 0.5 ^ (days_since_play / 90)
```
Only aggregated artist names and scores are ever sent to the LLM — raw Spotify data stays local.

## Environment Variables (`.env`)

```
TICKETMASTER_API_KEY=
GEMINI_API_KEY=

# Spotify API — create an app at https://developer.spotify.com/dashboard
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
SPOTIFY_REDIRECT_URI=http://localhost:8888/callback

SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
EMAIL_USER=
EMAIL_PASSWORD=             # Gmail app-specific password, not account password
RECIPIENT_EMAIL=
```

Spotipy uses `SpotifyOAuth` with scope `user-read-recently-played`. On first run it opens a browser for OAuth and caches the token in `.cache` (gitignored).

## Dependencies

Install via `pip install -r requirements.txt`. Packages: `spotipy`, `pandas`, `requests`, `google-genai`, `streamlit`, `python-dotenv`. Built-ins used: `sqlite3`, `smtplib`, `argparse`.

## Key Conventions

- Each tool class is self-contained and importable independently.
- Database access uses `with self._get_connection() as conn` context manager pattern.
- Manual tests live in `if __name__ == "__main__"` blocks at the bottom of each tool file.
- Gemini model: `gemini-1.5-flash` (used in `matchmaker.py`).
