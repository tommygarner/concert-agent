import argparse
from tools.spotify_parser import SpotifyAnalystAPI
from tools.db_manager import DatabaseManager
from tools.ticketmaster_scout import TicketmasterScout
from tools.matchmaker import MatchmakerDiscovery
from tools.secretary_notifier import SecretaryNotifier

class ConcertDiscoveryAgent:
    def __init__(self):
        self.db = DatabaseManager()
        self.scout = TicketmasterScout()
        self.matchmaker = MatchmakerDiscovery()
        self.secretary = SecretaryNotifier()

    def run_historian(self):
        """Fetch recent Spotify plays via API and sync to DB."""
        print("--- Historian: Fetching Recent Spotify Plays ---")
        analyst = SpotifyAnalystAPI()
        top_artists = analyst.get_top_artists()

        for _, row in top_artists.iterrows():
            self.db.update_artist_score(row['artist_name'], row['total_score'])
        print(f"Synced {len(top_artists)} artists to database.")

    def run_scout(self):
        """Find concerts for approved artists."""
        print("--- Scout: Searching for Concerts in Austin ---")
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT artist_name FROM artist_preferences WHERE status = 'APPROVED'")
            approved_artists = [row[0] for row in cursor.fetchall()]

        all_events = []
        for artist in approved_artists:
            events = self.scout.search_concerts(artist)
            for event in events:
                self.db.add_concert_alert(
                    event['event_id'], 
                    event['artist_name'], 
                    event['venue'], 
                    event['date'], 
                    event['url']
                )
                all_events.append(event)
        
        print(f"Found and saved {len(all_events)} new concert alerts.")

    def run_matchmaker(self):
        """Suggest similar artists based on approvals."""
        print("--- Matchmaker: Discovering Similar Artists ---")
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT artist_name FROM artist_preferences WHERE status = 'APPROVED'")
            approved_artists = [row[0] for row in cursor.fetchall()]

        if not approved_artists:
            print("No approved artists found. Approve some in the Gatekeeper dashboard first.")
            return

        suggestions = self.matchmaker.suggest_similar_artists(approved_artists)
        print(f"Gemini suggested: {', '.join(suggestions)}")
        
        # We add suggestions with a base score to be reviewed in Gatekeeper
        for artist in suggestions:
            # Check if already exists to avoid overwriting scores
            cursor.execute("SELECT 1 FROM artist_preferences WHERE artist_name = ?", (artist,))
            if not cursor.fetchone():
                self.db.update_artist_score(artist, 5.0) # Base score for suggestions

    def run_secretary(self):
        """Notify the user of new findings."""
        print("--- Secretary: Preparing Notifications ---")
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT artist_name, venue, date, url FROM concert_alerts WHERE notified_status = 'NEW'")
            new_alerts = cursor.fetchall()

        if not new_alerts:
            print("No new alerts to notify.")
            return

        body = "<h1>New Concert Alerts in Austin!</h1><ul>"
        for alert in new_alerts:
            body += f"<li><b>{alert[0]}</b> at {alert[1]} on {alert[2]} - <a href='{alert[3]}'>Tickets</a></li>"
        body += "</ul>"

        if self.secretary.send_notification("Bi-weekly Concert Digest", body):
            # Update status to NOTIFIED
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE concert_alerts SET notified_status = 'NOTIFIED' WHERE notified_status = 'NEW'")
                conn.commit()

def main():
    parser = argparse.ArgumentParser(description="Austin Concert Discovery Agent")
    parser.add_argument("--historian", action="store_true", help="Run the Spotify historian sync")
    parser.add_argument("--scout", action="store_true", help="Run the concert scout")
    parser.add_argument("--matchmaker", action="store_true", help="Run the discovery matchmaker")
    parser.add_argument("--secretary", action="store_true", help="Run the notifier")
    args = parser.parse_args()
    agent = ConcertDiscoveryAgent()

    if args.historian:
        agent.run_historian()
    if args.scout:
        agent.run_scout()
    if args.matchmaker:
        agent.run_matchmaker()
    if args.secretary:
        agent.run_secretary()

if __name__ == "__main__":
    main()
