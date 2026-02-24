import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

class MatchmakerDiscovery:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
            self.model_id = "gemini-1.5-flash"

    def suggest_similar_artists(self, approved_artists):
        """
        Takes a list of approved artists and returns a list of similar artists
        using an LLM.
        """
        if not self.api_key:
            print("Gemini API Key not found for Matchmaker.")
            return []

        if not approved_artists:
            return []

        prompt = f"""
        Based on the following list of musical artists that a user likes, 
        suggest 5-10 other similar artists who might be touring.
        Return ONLY a comma-separated list of artist names.
        
        Liked Artists: {', '.join(approved_artists)}
        """

        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            suggested = [a.strip() for a in response.text.split(',')]
            return suggested
        except Exception as e:
            print(f"Error in Matchmaker discovery: {e}")
            return []

if __name__ == "__main__":
    matchmaker = MatchmakerDiscovery()
    # print(matchmaker.suggest_similar_artists(["Kacey Musgraves", "Brandi Carlile"]))
