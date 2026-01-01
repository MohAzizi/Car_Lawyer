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

# UPDATE: Wir akzeptieren jetzt auch die Sprache "lang"
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
        body_text = soup.get_text(separator=' ', strip=True)[:4000] 
        full_text = title + " " + desc_text

        # --- 1. INTELLIGENTERES PARSING (FIX FÜR AUTOSCOUT) ---
        
        # PREIS: Suche nach "€ 12.345" ODER "12.345 €"
        price = 0
        # Muster 1: 48.970 €
        match_1 = re.search(r'(\d{1,3}(?:\.\d{3})*)\s*(?:€|EUR)', full_text)
        # Muster 2: € 48.970 (AutoScout Style)
        match_2 = re.search(r'(?:€|EUR)\s*(\d{1,3}(?:\.\d{3})*)', full_text)
        
        if match_1:
            price = int(match_1.group(1).replace('.', ''))
        elif match_2:
            price = int(match_2.group(1).replace('.', ''))
        
        # KM: Suche nach "km"
        km = 0
        km_match = re.search(r'(\d{1,3}(?:\.?\d{3})*)\s*(?:km)', full_text, re.IGNORECASE)
        if km_match:
            km = int(km_match.group(1).replace('.', ''))
            
        # EZ: Suche Datum MM/YYYY
        ez_string = "Unbekannt"
        ez_match = re.search(r'(\d{2}/\d{4})', full_text)
        if ez_match:
            ez_string = ez_match.group(1)

        # Mathe
        current_year = datetime.now().year
        car_year = current_year
        if ez_string != "Unbekannt":
            try: car_year = int(ez_string.split('/')[1])
            except: pass
        age = current_year - car_year
        if age == 0: age = 1
        km_per_year = int(km / age) if km > 0 else 0

        # --- 2. KI ANALYSE (MIT SPRACHE & AUSSTATTUNG) ---
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Sprache setzen
        lang_instruction = "Antworte auf DEUTSCH." if request.lang == 'de' else "Answer in ENGLISH."
        
        system_instruction = f"""
        Du bist ein Auto-Experte. {lang_instruction}
        Dein Ziel: Preis drücken für den Käufer. Sei kritisch aber fair.
        """

        user_prompt = f"""
        Fahrzeug: {title}
        Preis: {price} EUR
        KM: {km}
        EZ: {ez_string}
        Beschreibung: "{desc_text}... {body_text[:600]}"

        Aufgabe:
        1. Analysiere Preis/Leistung.
        2. WICHTIG: Suche im Text nach Ausstattung (Leder, Navi, Schiebedach, LED, etc.).
           - Wenn viel fehlt -> Nutze das als Argument um den Preis zu drücken ("Nackte Basis").
           - Wenn viel da ist -> Erwähne es positiv, aber suche trotzdem Mängel (Verschleiß).
        3. Erstelle 3 knackige Verhandlungs-Argumente für den KÄUFER.
        
        Antworte JSON:
        {{
            "market_price_estimate": (int),
            "rating": "teuer/fair/good_deal",
            "arguments": ["Arg1", "Arg2", "Arg3"],
            "script": "Ein direkter Satz an den Verkäufer"
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

        # Fallback Werte
        est_price = ai_result.get("market_price_estimate", int(price * 0.95))
        if est_price == 0: est_price = price # Vermeide 0€ Schätzung

        # DB Save
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
                "arguments": ai_result.get("arguments", ["Daten unklar", "Preis prüfen"]),
                "script": ai_result.get("script", "")
            }
        }

    except Exception as e:
        print(f"ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))