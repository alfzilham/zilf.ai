import asyncio
import os
import httpx
from dotenv import load_dotenv

# Paksa muat .env dari jalur absolut
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path, override=True)

async def debug_call():
    url = "https://zilf-max-api-production.up.railway.app/v1/chat"
    headers = {
        "Authorization": f"Bearer {os.getenv('ZILF_MAX_API_KEY')}",
        "Content-Type": "application/json"
    }
    payload = {
        "message": "Halo",
        "model": "llama-3.3-70b-versatile",
        "provider": "groq"
    }
    
    print(f"DEBUG: Memanggil {url} via httpx...")
    print(f"DEBUG: API KEY used: {headers['Authorization'][:15]}...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
            print(f"DEBUG: HTTP STATUS: {resp.status_code}")
            print(f"DEBUG: RESPONSE BODY: {resp.text}")
        except Exception as e:
            print(f"DEBUG: REQUEST FAILED: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(debug_call())
