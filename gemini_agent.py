import os
import google.generativeai as genai
from dotenv import load_dotenv
from server import search_concerts, load_artist_profile

load_dotenv()

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def run_concert_agent(user_query, use_history=True):
    # 1. Load context from your Spotify history
    profile_summary = ""
    if use_history:
        profile = load_artist_profile()
        # Get top 20 artists to give Gemini context
        top_artists = sorted(profile.items(), key=lambda x: x[1], reverse=True)[:20]
        profile_summary = ", ".join([f"{a} (score: {s:.1f})" for a, s in top_artists])

    # 2. System Instruction
    system_prompt = f"""
    You are the Austin Concert Agent. Your goal is to help the user find music events in Austin.
    
    USER PROFILE (Top Artists from 10-year history):
    {profile_summary}
    
    GUIDELINES:
    - Use the search_concerts tool to find live data.
    - If the user asks for "recommendations," prioritize artists in their profile.
    - Be concise and enthusiastic about the Austin music scene.
    - If you find a match from their profile, highlight it as a "Top Pick."
    """

    # 3. Initialize Model with Tool
    model = genai.GenerativeModel(
        model_name='gemini-2.0-flash-001',
        tools=[search_concerts],
        system_instruction=system_prompt
    )

    # 4. Start Chat
    chat = model.start_chat(enable_automatic_function_calling=True)
    response = chat.send_message(user_query)
    
    return response.text

if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What are some good shows this month?"
    print(f"\n--- Austin Concert Agent ---\n")
    print(run_concert_agent(query))
