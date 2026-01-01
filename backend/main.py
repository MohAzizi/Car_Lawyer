from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import re
import os
from dotenv import load_dotenv
from openai import OpenAI
from datetime import datetime
from supabase import create_client, Client
import json

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIG ---
SCRAPINGBEE_API_KEY = os.getenv("SCRAPINGBEE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase Client initialisiert")
except Exception as e:
    print(f"❌ Supabase Error: {e}")

class CarRequest(BaseModel):
    url: str
    lang: str = "de" 

def extract_metadata(soup):
    og_title = soup.find('meta', property='og:title')
    og_desc = soup.find('meta', property='og:description')
    og_image = soup.find('meta', property='og:image')
    
    return {
        "title": og_title['content'] if og_title else "Unbekanntes Fahrzeug",
        "desc": og_desc['content'] if og_desc else "",
        "image": og_image['content'] if og_image else None
    }

@app.post("/analyze")
def analyze_car(request: CarRequest):
    print(f"🔎 Analysiere ({request.lang}): {request.url}")
    
    params = {
        'api_key': SCRAPINGBEE_API_KEY,
        'url': request.url,
        'render_js': 'True', 
        'premium_proxy': 'True',
        'country_code': 'de',
        'wait_browser': 'networkidle2' 
    }

    try:
        response = requests.get('https://app.scrapingbee.com/api/v1/', params=params)
        soup = BeautifulSoup(response.content, 'html.parser')
        meta = extract_metadata(soup)
        
        title = meta['title']
        desc_text = meta['desc']
        image_url = meta['image']
        
        # NEU: Wir holen viel mehr Text (15.000 Zeichen), um auch Ausstattung weit unten zu finden
        # Wir priorisieren den "Body", um Header/Footer Müll zu vermeiden
        raw_body = soup.body.get_text(separator=' ', strip=True) if soup.body else soup.get_text(separator=' ', strip=True)
        
        # Trick: Wir suchen nach "Ausstattung" im Text und schneiden den Teil aus
        relevant_text = raw_body[:15000] 
        
        full_text = title + " " + desc_text + " " + relevant_text

        # --- PARSING (Preise & KM) ---
        price = 0
        match_1 = re.search(r'(\d{1,3}(?:\.\d{3})*)\s*(?:€|EUR)', full_text)
        match_2 = re.search(r'(?:€|EUR)\s*(\d{1,3}(?:\.\d{3})*)', full_text)
        
        if match_1: price = int(match_1.group(1).replace('.', ''))
        elif match_2: price = int(match_2.group(1).replace('.', ''))
        
        km = 0
        km_match = re.search(r'(\d{1,3}(?:\.?\d{3})*)\s*(?:km)', full_text, re.IGNORECASE)
        if km_match: km = int(km_match.group(1).replace('.', ''))
            
        ez_string = "Unbekannt"
        ez_match = re.search(r'(\d{2}/\d{4})', full_text)
        if ez_match: ez_string = ez_match.group(1)

        # --- KI ANALYSE ---
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # NEU: Sprach-Befehl ist jetzt "System Law"
        lang_instruction = "You MUST answer in GERMAN." if request.lang == 'de' else "You MUST answer in ENGLISH."
        
        system_instruction = f"""
        {lang_instruction}
        You are a professional car dealer negotiation assistant.
        Your goal: Help the buyer negotiate a lower price.
        
        CRITICAL RULE:
        - Analyze the text below for "Equipment" (Ausstattung) keywords (e.g., Leather, Navi, LED, Pano, ACC).
        - If the text contains these keywords, DO NOT claim the car is "basic" or "naked".
        - If the text is messy or cut off, assume "Equipment details unclear" instead of "No equipment".
        """

        user_prompt = f"""
        Car: {title}
        Price: {price} EUR
        KM: {km}
        First Reg: {ez_string}
        
        RAW CAR DATA FROM WEBSITE (Read this carefully for equipment!): 
        "{relevant_text}..."

        Task:
        1. Estimate a fair dealer purchase price (market_price_estimate).
        2. Identify 3 strong negotiation arguments for the BUYER based on the data.
           - If it has high mileage -> Argument about wear/tear.
           - If it misses features -> Argument about base model.
           - If it HAS features -> Find other flaws (e.g. price too high for age).
        3. Create a short negotiation script sentence.
        
        Reply strictly in JSON:
        {{
            "market_price_estimate": (int),
            "rating": "expensive/fair/good",
            "arguments": ["Arg1", "Arg2", "Arg3"],
            "script": "..."
        }}
        """

        try:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={ "type": "json_object" }
            )
            ai_result = json.loads(completion.choices[0].message.content)
        except:
            ai_result = {}

        est_price = ai_result.get("market_price_estimate", int(price * 0.95))
        if est_price == 0: est_price = price 

        # DB Save (wie gehabt)
        try:
            supabase.table("scans").insert({
                "url": str(request.url),
                "title": str(title),
                "price": int(price),
                "ai_market_estimate": int(est_price),
                "rating": str(ai_result.get("rating", "fair"))
            }).execute()
        except: pass

        return {
            "meta": { "title": title, "url": request.url, "image": image_url },
            "data": { "price": price, "km": km, "ez": ez_string },
            "analysis": {
                "market_price_estimate": est_price,
                "rating": ai_result.get("rating", "fair"),
                "negotiation_potential": price - est_price,
                "arguments": ai_result.get("arguments", ["Data unclear", "Check price locally"]),
                "script": ai_result.get("script", "")
            },
            # NEU: Das Debug Feld!
            "debug_text_snippet": relevant_text[:500] + " ... [CHECK CONSOLE FOR MORE] ... " 
        }

    except Exception as e:
        print(f"ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))