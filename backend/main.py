
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware # <--- NEU
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import re
import os
from dotenv import load_dotenv
from openai import OpenAI
import json 
from datetime import datetime
from supabase import create_client, Client # <--- NEU


load_dotenv()

app = FastAPI()

SCRAPINGBEE_API_KEY = os.getenv("SCRAPINGBEE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
# --- SUPABASE CONFIG ---
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase Client initialisiert")
except Exception as e:
    print(f"❌ Supabase konnte nicht starten: {e}")
# --- NEU: CORS ERLAUBEN ---
# Das erlaubt deinem Frontend (localhost:3000) mit dem Backend (localhost:8000) zu reden.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Für MVP erlauben wir alle. Später nur deine Domain.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ---------------------------

# ... (Hier kommt dein alter Code: SCRAPINGBEE_API_KEY = ... class CarRequest ... etc.)

# --- KONFIGURATION ---
# Fürs erste hardcoden wir den Key hier, später kommt er in eine .env Datei


class CarRequest(BaseModel):
    url: str

@app.get("/")
def read_root():
    return {"status": "Deal Anwalt Backend is running 🚀"}

@app.post("/analyze")
def analyze_car(request: CarRequest):
    print(f"🔎 Analysiere URL: {request.url}")
    
    # --- 1. SCRAPING (Bleibt gleich) ---
    params = {
        'api_key': SCRAPINGBEE_API_KEY,
        'url': request.url,
        'render_js': 'True',
        'premium_proxy': 'True',
        'stealth_proxy': 'True',
        'country_code': 'de',
        'wait_browser': 'networkidle2' 
    }

    try:
        response = requests.get('https://app.scrapingbee.com/api/v1/', params=params)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Meta Daten holen
        og_title = soup.find('meta', property='og:title')
        og_desc = soup.find('meta', property='og:description')
        og_image = soup.find('meta', property='og:image')

        title = og_title['content'] if og_title else "Unbekanntes Fahrzeug"
        desc_text = og_desc['content'] if og_desc else ""
        image_url = og_image['content'] if og_image else None

        # Preis
        # 2. Preis extrahieren (Verbesserte Logik)
        price = 0
        # Wir suchen nach Zahlen, die direkt vor '€', 'EUR' oder 'Euro' stehen
        # Erklärung Regex: (\d{1,3}(?:\.\d{3})*) sucht nach Zahlen wie 10.000
        # \s* sucht nach optionalen Leerzeichen
        # (?:€|EUR|Euro) sucht nach dem Währungssymbol
        price_match = re.search(r'(\d{1,3}(?:\.\d{3})*)\s*(?:€|EUR|Euro)', title)
        
        if price_match:
            # Treffer gefunden (z.B. "42.470")
            price = int(price_match.group(1).replace('.', ''))
        else:
            # Fallback: Falls kein Euro-Zeichen im Titel ist, nehmen wir die größte Zahl im Titel (oft der Preis)
            # Das verhindert, dass wir "430" aus "BMW 430" nehmen, wenn "42.470" auch da steht.
            all_numbers = re.findall(r'(\d{1,3}(?:\.\d{3})*)', title)
            if all_numbers:
                # Wir filtern kleine Zahlen (unter 500) raus, da Autos selten 430€ kosten
                # und nehmen dann die letzte gefundene Zahl (oft steht Preis am Ende)
                valid_numbers = [int(n.replace('.', '')) for n in all_numbers if int(n.replace('.', '')) > 500]
                if valid_numbers:
                    price = max(valid_numbers) # Nimm die höchste Zahl im Titel

        # Details
        km = 0
        ez_string = "N/A"
        power = "N/A"
        
        if desc_text:
            parts = desc_text.split('•')
            for part in parts:
                part = part.strip()
                if 'km' in part:
                    km_clean = re.sub(r'[^\d]', '', part)
                    if km_clean: km = int(km_clean)
                elif '/' in part and len(part) == 7:
                    ez_string = part
                elif 'PS' in part or 'kW' in part:
                    power = part

        # --- 2. MATHE & LOGIK (Das Gehirn V2) ---
        
        # Alter berechnen
        current_year = datetime.now().year
        car_year = current_year
        if ez_string != "N/A":
            try:
                car_year = int(ez_string.split('/')[1])
            except:
                pass
        
        age = current_year - car_year
        if age == 0: age = 1 # Vermeide Division durch Null
        
        # KM pro Jahr berechnen
        km_per_year = int(km / age)
        
        # Logik-Flags für die KI vorbereiten
        fuel_type = "Unbekannt"
        if "diesel" in desc_text.lower(): fuel_type = "Diesel"
        elif "benzin" in desc_text.lower(): fuel_type = "Benzin"
        elif "hybrid" in desc_text.lower(): fuel_type = "Hybrid"
        elif "elektro" in desc_text.lower(): fuel_type = "Elektro"

        # --- 3. KI PROMPT (Der aggressive Anwalt) ---
        client = OpenAI(api_key=OPENAI_API_KEY) # <--- KEY PRÜFEN!

        system_instruction = """
        Du bist ein professioneller KFZ-Einkäufer. Dein Ziel: Den Preis drücken.
        Analysiere die harten Fakten knallhart und logisch.
        Vermeide Floskeln wie 'Gutes Auto'. Suche das Haar in der Suppe.
        """

        user_prompt = f"""
        Fahrzeugdaten:
        - Modell: {title}
        - Preis: {price} EUR
        - Laufleistung: {km} km
        - Erstzulassung: {ez_string} (Alter: {age} Jahre)
        - Durchschnitt pro Jahr: {km_per_year} km/Jahr
        - Antrieb: {fuel_type}
        - Beschreibungstext: {desc_text}

        Wende diese Logik an, um Argumente zu finden:
        1. WENN km_per_year < 5000: Argumentiere mit "Standuhr", Standschäden, verhärtete Reifen/Gummis.
        2. WENN km_per_year > 25000: Argumentiere mit "Langstreckenbomber", Steinschläge prüfen, Fahrwerk verschlissen.
        3. WENN Diesel UND km_per_year < 10000: Argumentiere mit "Verkokungsgefahr", AGR-Ventil Risiko, Partikelfilter zu.
        4. WENN Elektro/Hybrid UND Alter > 5 Jahre: Argumentiere mit "Batterie-Degradation" und Garantieverlust.
        5. WENN Beschreibung sehr kurz: Argumentiere mit "Katze im Sack", fehlende Historie.

        Aufgabe:
        Erstelle ein JSON mit 3 harten, spezifischen Argumenten basierend auf diesen Daten.
        Schätze einen aggressiven aber nicht unverschämten Zielpreis (ca. 8-12% Rabatt).
        
        Antworte NUR JSON:
        {{
            "market_price_estimate": 12345,
            "rating": "teuer/fair/gut",
            "arguments": ["Argument 1", "Argument 2", "Argument 3"],
            "script": "Ein direkter Satz an den Verkäufer..."
        }}
        """

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt}
            ],
            response_format={ "type": "json_object" }
        )
        
        ai_result = json.loads(completion.choices[0].message.content)
        try:
            # 1. Daten sicherstellen (keine None-Werte wo Zahlen erwartet werden)
            safe_market_est = int(ai_result.get("market_price_estimate", price))
            safe_potential = int(price - safe_market_est)
            
            # 2. Das Paket schnüren
            data_to_save = {
                "url": str(request.url),
                "title": str(title),
                "image_url": str(image_url) if image_url else None,
                "price": int(price),
                "km": int(km),
                "ez": str(ez_string),
                "rating": str(ai_result.get("rating", "fair")),
                "ai_market_estimate": safe_market_est,
                "ai_potential": safe_potential
            }
            
            print(f"💾 Versuche zu speichern: {data_to_save['title']}...")
            
            # 3. Ab in die Datenbank
            db_response = supabase.table("scans").insert(data_to_save).execute()
            print("✅ Erfolgreich in DB gespeichert!")
            
        except Exception as db_error:
            # Wenn es knallt, sehen wir hier warum
            print(f"⚠️ DATENBANK FEHLER: {db_error}")
        # Zusammenbauen
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
                "power": power
            },
            "analysis": {
                "market_price_estimate": ai_result.get("market_price_estimate", price),
                "rating": ai_result.get("rating", "fair"),
                "negotiation_potential": price - ai_result.get("market_price_estimate", price),
                "arguments": ai_result.get("arguments", ["Preis vergleichen"]),
                "script": ai_result.get("script", "Was ist letzte Preis?")
            }
        }

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))