# Concert Agent Improvement Spec

Three workstreams: (1) fix Gemini rate limits, (2) add Side By Side Shows scraper, (3) editorial CSS overhaul.

---

## 1. Gemini Rate Limit Fixes

### Problem
On the free tier, `gemini-2.0-flash` allows only **15 RPM** (requests per minute). The app's model fallback list cycles through all 4 models on 429 errors, burning through limits on each.

### Recommendations

**Quick win (free tier):** Swap `gemini-2.0-flash-lite-001` to the primary position in the fallback list. Flash Lite gets **30 RPM** on free tier (double Flash) and is sufficient for tool orchestration.

**Paid tier (if needed):** Pay-as-you-go bumps Flash to **2,000 RPM** and Flash Lite to **4,000 RPM**. Cost is negligible for a personal app:
- Flash: $0.10/1M input tokens, $0.40/1M output tokens
- Flash Lite: $0.075/1M input, $0.30/1M output
- A typical session (5-10 queries) costs well under $0.01

**Code changes (regardless of tier):**

1. **Reorder model fallback list** in `app.py`:
   ```python
   # Before
   ['gemini-2.0-flash', 'gemini-2.0-flash-lite-001', 'gemini-pro-latest', 'gemini-flash-latest']
   # After
   ['gemini-2.0-flash-lite-001', 'gemini-2.0-flash', 'gemini-flash-latest']
   ```
   Drop `gemini-pro-latest` (highest rate limit pressure, not needed for tool calls).

2. **Cache Gemini responses** for the Browse Shows tab. Currently `get_top_picks()` calls `search_concerts()` (which is cached) but the Browse tab re-renders on every tab switch. Add `@st.cache_data(ttl=3600)` to the browse data fetch.

3. **Reduce redundant tool calls.** The system prompt should instruct Gemini to batch related lookups (e.g., search + venue details) into fewer round-trips rather than sequential single-tool calls.

4. **Add request throttling** in `app.py`: track timestamps of Gemini calls in session state, enforce a minimum interval (e.g., 2s between requests on free tier) with a user-visible "thinking..." indicator instead of hammering the API.

---

## 2. Side By Side Shows Scraper

### Site Analysis
- **Platform:** Next.js (React). Server-renders an `initialShows` JSON array embedded in the page source.
- **Data per event:** artist name(s), venue, date (YYYY-MM-DD), time, price, age restriction, ticket link, event slug.
- **Scraping strategy:** Extract the embedded JSON from the page source using regex or a `<script>` tag parser. No Selenium needed since the data is in the initial HTML payload.
- **URL:** `https://sidebysideshows.com/` (homepage contains ~50 upcoming events)

### Implementation

Add `search_side_by_side(city="Austin")` to `tools.py`:

1. **Fetch** the homepage HTML with `requests.get()`.
2. **Extract** the `initialShows` JSON array from the `__next_f` script tags. Pattern: look for the serialized props containing the shows array.
3. **Parse** each event into the same shape as Showlist Austin results: `{name, venue, date, time, price, url}`.
4. **Rank** against artist profile using `match_artist_to_event()` (same as other tools).
5. **Cache** results with a 6h TTL in `data/sbs_cache.json` (same pattern as setlist/presale caches).

### Merge with Showlist Austin

Update `search_small_venue_calendar()` in `tools.py` to become a unified indie search:

```python
def search_small_venue_calendar(venue_name=None):
    """Search indie/small venue shows from Showlist Austin AND Side By Side Shows."""
    results = []
    # Fetch from both sources, deduplicate by (artist, venue, date)
    showlist_results = _scrape_showlist(venue_name)
    sbs_results = _scrape_side_by_side()
    results = deduplicate(showlist_results + sbs_results)
    return results
```

- Deduplicate by normalizing artist name + venue + date.
- If one source has more detail (e.g., SxS has price but Showlist doesn't), merge fields.
- Showlist Austin remains venue-specific (pass `venue_name`); SxS returns all Austin shows.

### MCP Server
Add `side_by_side_shows` as a new MCP tool in `server.py`, or merge into existing `small_venue_calendar` tool.

---

## 3. Editorial CSS Overhaul (Streamlit + Custom CSS)

### Design Direction
Light, editorial aesthetic. Think Pitchfork/Bandcamp: strong typography, generous whitespace, card-based concert listings.

### Approach
Inject custom CSS via `st.markdown(unsafe_allow_html=True)` at the top of `app.py`. Override Streamlit's default dark theme.

### Key Style Changes

1. **Global theme:**
   - Light background (`#FAFAF8` warm white)
   - Dark text (`#1A1A1A`)
   - Accent color for interactive elements (muted coral or deep blue)
   - System font stack: `-apple-system, 'Inter', sans-serif`

2. **Concert cards** (replace current `st.expander` or inline rendering):
   - White card with subtle border (`1px solid #E5E5E3`)
   - Artist name in bold, larger type
   - Venue/date/price in smaller, muted text
   - Tier tag as a small colored pill (superfan = coral, fan = blue, casual = gray)
   - Ticket link and calendar link as minimal text buttons
   - Hover: subtle lift shadow

3. **Tab navigation:**
   - Clean horizontal tabs with bottom border indicator
   - No Streamlit default tab styling

4. **Chat interface:**
   - User messages right-aligned, light background
   - Agent messages left-aligned, white with left border accent
   - Clean message bubbles, no Streamlit avatar icons

5. **Sidebar:**
   - Clean profile section at top
   - Spotify connect button styled as a green pill
   - Settings grouped with subtle section dividers

### Implementation Plan

1. Create `styles.css` with all custom styles.
2. Load it in `app.py` via:
   ```python
   with open("styles.css") as f:
       st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
   ```
3. Update concert card rendering (`render_concert_card` function around line 210) to use custom HTML with CSS classes instead of native Streamlit components.
4. Update tab rendering to use styled HTML where Streamlit components look too default.
5. Keep functional Streamlit components (buttons, inputs, chat) but override their CSS.

### What Stays Streamlit-Native
- `st.chat_input` / `st.chat_message` (override CSS only)
- `st.sidebar` (override CSS only)
- `st.tabs` (override CSS only)
- `st.button`, `st.text_input` (override CSS only)

---

## Execution Order

1. **Rate limits first** (unblocks development, ~30 min)
2. **SxS scraper second** (new feature, ~1-2 hours)
3. **CSS overhaul third** (cosmetic, can iterate, ~2-3 hours)

---

## Verification

- Rate limits: Run app, send 10+ queries in quick succession, confirm no 429 errors
- SxS scraper: Run `search_side_by_side()` standalone, verify JSON extraction and artist matching
- CSS: Visual inspection in browser, check all 4 tabs render correctly
