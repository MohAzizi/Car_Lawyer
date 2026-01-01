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

# CORS Konfiguration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ENVIRONMENT VARIABLES ---
SCRAPINGBEE_API_KEY = os.getenv("SCRAPINGBEE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Supabase Client
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase Client initialisiert")
except Exception as e:
    print(f"❌ Supabase Error: {e}")

class CarRequest(BaseModel):
    url: str

# --- HILFSFUNKTIONEN ---

def clean_number(text):
    """Extrahiert Zahlen aus Text (z.B. '29.999 €' -> 29999)"""
    if not text: return 0
    # Entferne alles außer Zahlen
    num = re.sub(r'[^\d]', '', text)
    return int(num) if num else 0

def extract_metadata(soup):
    """Holt Titel, Beschreibung und Bild aus den Meta-Tags (Universal für alle Seiten)"""
    og_title = soup.find('meta', property='og:title')
    og_desc = soup.find('meta', property='og:description')
    og_image = soup.find('meta', property='og:image')
    
    return {
        "title": og_title['content'] if og_title else "Unbekanntes Fahrzeug",
        "desc": og_desc['content'] if og_desc else "",
        "image": og_image['content'] if og_image else None
    }

@app.get("/")
def read_root():
    return {"status": "Deal Anwalt Backend v2 (Multi-Platform) is running 🚀"}

@app.post("/analyze")
def analyze_car(request: CarRequest):
    print(f"🔎 Analysiere: {request.url}")
    
    # 1. SCRAPING (Universal via ScrapingBee)
    params = {
        'api_key': SCRAPINGBEE_API_KEY,
        'url': request.url,
        'render_js': 'True', # Wichtig für AutoScout & Mobile
        'premium_proxy': 'True', # Hilft gegen Blockaden
        'country_code': 'de',
        'wait_browser': 'networkidle2' 
    }

    try:
        response = requests.get('https://app.scrapingbee.com/api/v1/', params=params)
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Scraping fehlgeschlagen (Seite blockiert oder nicht erreichbar)")
            
        soup = BeautifulSoup(response.content, 'html.parser')
        meta = extract_metadata(soup)
        
        title = meta['title']
        desc_text = meta['desc']
        image_url = meta['image']
        
        # Zusätzlich den ganzen sichtbaren Text holen für die Ausstattungs-Analyse
        # Wir nehmen nur die wichtigsten Textblöcke, um Token zu sparen
        body_text = soup.get_text(separator=' ', strip=True)[:4000] 

        # 2. DATEN EXTRAKTION (Intelligenter Regex für alle Plattformen)
        
        # PREIS FINDEN
        price = 0
        # Sucht nach Preis im Titel oder Description (Format: 12.345 €)
        price_match = re.search(r'(\d{1,3}(?:\.\d{3})*)\s*(?:€|EUR|Euro)', title + " " + desc_text)
        if price_match:
            price = int(price_match.group(1).replace('.', ''))
        
        # KM FINDEN
        km = 0
        # Sucht nach "km" oder "Laufleistung"
        km_match = re.search(r'(\d{1,3}(?:\.?\d{3})*)\s*(?:km)', title + " " + desc_text, re.IGNORECASE)
        if km_match:
            km = int(km_match.group(1).replace('.', ''))
            
        # BAUJAHR / EZ FINDEN
        ez_string = "Unbekannt"
        # Sucht nach Datum Format MM/YYYY
        ez_match = re.search(r'(\d{2}/\d{4})', title + " " + desc_text)
        if ez_match:
            ez_string = ez_match.group(1)

        # 3. MATHE & LOGIK
        current_year = datetime.now().year
        car_year = current_year
        if ez_string != "Unbekannt":
            try: car_year = int(ez_string.split('/')[1])
            except: pass
        
        age = current_year - car_year
        if age == 0: age = 1
        km_per_year = int(km / age) if km > 0 else 0

        # Antriebsart raten
        fuel_type = "Unbekannt"
        full_text_lower = (title + desc_text).lower()
        if "diesel" in full_text_lower: fuel_type = "Diesel"
        elif "hybrid" in full_text_lower: fuel_type = "Hybrid"
        elif "elektro" in full_text_lower: fuel_type = "Elektro"
        elif "benzin" in full_text_lower: fuel_type = "Benzin"

        # 4. KI ANALYSE (Das Gehirn v3 - Jetzt mit Ausstattung!)
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        system_instruction = """
        Du bist ein knallharter Profi-Autohändler. Dein Ziel: Den Preis verhandeln.
        Bewerte das Auto basierend auf:
        1. Harten Fakten (KM, Alter, Preis)
        2. Ausstattung (Fehlt wichtiges? Ist es "Volle Hütte"?).
        
        Sei kritisch. Ein "nackter" 5er BMW ohne Leder/Navi ist schwer verkäuflich -> Preis drücken.
        Ein Auto mit seltener Top-Ausstattung (z.B. Standheizung, Pano, M-Paket) rechtfertigt höheren Preis.
        """

        user_prompt = f"""
        Fahrzeugdaten:
        - Titel: {title}
        - Preis: {price} EUR
        - KM: {km} (Ø {km_per_year} km/Jahr)
        - EZ: {ez_string}
        - Typ: {fuel_type}
        
        Auszug aus Beschreibung (suche hier nach Ausstattung!): 
        "{desc_text} ... {body_text[:500]}"

        Aufgaben:
        1. Analysiere die KM-Leistung (Standuhr vs. Langstrecke).
        2. CHECK DIE AUSSTATTUNG: Erwähne explizit fehlende oder besonders gute Ausstattung in den Argumenten.
        3. Schätze einen realistischen Händler-Einkaufspreis.
        
        Antworte NUR JSON:
        {{
            "market_price_estimate": 12345,
            "rating": "teuer/fair/gut",
            "arguments": ["Argument 1 (Technik/KM)", "Argument 2 (Ausstattung/Zustand)", "Argument 3 (Marktlage)"],
            "script": "Direkter Verhandlungssatz..."
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
        except Exception as e:
            print(f"KI Error: {e}")
            # Fallback falls KI versagt
            ai_result = {
                "market_price_estimate": int(price * 0.9),
                "rating": "fair",
                "arguments": ["Konnte Details nicht lesen, aber Preis prüfen.", "Allgemeiner Marktvergleich."],
                "script": "Was ist der letzte Preis?"
            }

        # 5. DB SPEICHERUNG
        try:
            safe_market = int(ai_result.get("market_price_estimate", price))
            data_to_save = {
                "url": str(request.url),
                "title": str(title),
                "image_url": str(image_url) if image_url else None,
                "price": int(price),
                "km": int(km),
                "ez": str(ez_string),
                "rating": str(ai_result.get("rating", "fair")),
                "ai_market_estimate": safe_market,
                "ai_potential": int(price - safe_market)
            }
            supabase.table("scans").insert(data_to_save).execute()
        except Exception as e:
            print(f"DB Save Error: {e}")

        return {
            "meta": {
                "title": title,
                "url": request.url,
                "image": image_url
            },
            "data": {
                "price": price,
                "km": km,
                "ez": ez_string,
                "power": "N/A" # Schwer generisch zu parsen, egal für MVP
            },
            "analysis": {
                "market_price_estimate": ai_result.get("market_price_estimate", price),
                "rating": ai_result.get("rating", "fair"),
                "negotiation_potential": price - ai_result.get("market_price_estimate", price),
                "arguments": ai_result.get("arguments", []),
                "script": ai_result.get("script", "")
            }
        }

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))