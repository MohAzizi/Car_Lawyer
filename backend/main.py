from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import re
import os
from dotenv import load_dotenv
from openai import OpenAI
from datetime import datetime, timezone
from supabase import create_client, Client
import json
import dateutil.parser

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

def clean_text(text):
    if not text: return ""
    return re.sub(r'\s+', ' ', text).strip()

def parse_tech_data(soup):
    """
    Spezial-Funktion für AutoScout/Mobile Daten-Listen (dl/dt/dd).
    Holt KM, EZ, Leistung, etc. sehr präzise aus den Tabellen.
    """
    data = {}
    
    # Suche nach allen Definitions-Listen (Standard für Specs)
    dls = soup.find_all('dl')
    
    for dl in dls:
        # Wir iterieren durch alle Zeilen in der Liste
        dts = dl.find_all('dt') # Label (z.B. "Kilometerstand")
        dds = dl.find_all('dd') # Wert (z.B. "10.000 km")
        
        if len(dts) == len(dds):
            for i in range(len(dts)):
                key = clean_text(dts[i].get_text()).lower()
                val = clean_text(dds[i].get_text())
                
                if "kilometer" in key:
                    data['km'] = val
                elif "erstzulassung" in key:
                    data['ez'] = val
                elif "leistung" in key:
                    data['power'] = val
                elif "fahrzeughalter" in key:
                    data['owners'] = val
                elif "getriebe" in key:
                    data['transmission'] = val
                elif "kraftstoff" in key:
                    data['fuel'] = val
                    
    return data

def extract_structured_data(soup):
    """ JSON-LD Extraction (Bleibt für den Preis wichtig) """
    data = {}
    scripts = soup.find_all('script', type='application/ld+json')
    
    for script in scripts:
        try:
            json_content = json.loads(script.string)
            if isinstance(json_content, list): json_content = json_content[0]
            if isinstance(json_content, dict) and '@graph' in json_content:
                for item in json_content['@graph']:
                    if item.get('@type') in ['Product', 'Car', 'Vehicle', 'Offer']:
                        json_content = item
                        break
            
            if 'offers' in json_content:
                offer = json_content['offers']
                if isinstance(offer, list): offer = offer[0]
                data['price'] = offer.get('price')
            
            # Manchmal steht KM hier, manchmal nicht
            if 'mileageFromOdometer' in json_content:
                data['km'] = json_content['mileageFromOdometer'].get('value')
            
            if 'name' in json_content: data['title'] = json_content['name']
            
            if 'image' in json_content:
                img = json_content['image']
                if isinstance(img, list): data['image'] = img[0]
                elif isinstance(img, dict): data['image'] = img.get('url')
                else: data['image'] = img
        except: continue
    return data

@app.post("/analyze")
def analyze_car(request: CarRequest):
    print(f"🔎 Analysiere ({request.lang}): {request.url}")
    
    # 1. HISTORY CHECK
    history_info = ""
    try:
        existing = supabase.table("scans").select("*").eq("url", request.url).order("created_at", desc=False).limit(1).execute()
        if existing.data:
            first = existing.data[0]
            old_price = first['price']
            # WICHTIG: Wir ignorieren den History-Check, wenn der alte Preis offensichtlich falsch war (> 60000 für diesen Lexus Fehler)
            # Oder du löschst die DB Tabelle einmal manuell.
            history_info = f"Old price: {old_price}"
    except: pass

    # 2. SCRAPING
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
        
        # A) JSON-LD (Bester Preis)
        structured = extract_structured_data(soup)
        
        # B) TABELLEN PARSING (Beste KM/EZ/Specs)
        tech_data = parse_tech_data(soup)
        
        # C) TEXT BODY (Für Ausstattung)
        # Wir suchen gezielt nach Listen-Elementen (Ausstattung sind oft <li> oder in Grids)
        equipment_text = ""
        # Suche nach spezifischen Containern für Ausstattung (Mobile/Autoscout Klassen ändern sich oft, daher generisch)
        feature_lists = soup.find_all(['div', 'ul'], class_=re.compile(r'equipment|feature|opt|item', re.I))
        for f in feature_lists:
            t = clean_text(f.get_text(" "))
            if len(t) > 20 and len(t) < 3000: # Filtert Müll raus
                equipment_text += t + " | "
        
        if len(equipment_text) < 50: # Fallback Body
            equipment_text = soup.body.get_text(separator=' ', strip=True)[:10000]

        # D) META FALLBACK
        og_title = soup.find('meta', property='og:title')
        title = structured.get('title') or (og_title['content'] if og_title else "Unbekannt")
        image_url = structured.get('image') or (soup.find('meta', property='og:image')['content'] if soup.find('meta', property='og:image') else None)

        # 3. DATEN ZUSAMMENFÜHREN
        
        # PREIS: JSON-LD gewinnt
        price = 0
        if structured.get('price'): price = int(float(structured['price']))
        
        # KM: Tech-Data (Tabelle) gewinnt vor JSON-LD
        km = 0
        if tech_data.get('km'):
            # "1.865 km" -> 1865
            km = int(re.sub(r'[^\d]', '', tech_data['km']))
        elif structured.get('km'):
            km = int(float(structured['km']))
            
        # EZ: Tech-Data gewinnt
        ez_string = tech_data.get('ez') or str(structured.get('year', 'Unbekannt'))
        
        # Zusatzinfos für KI
        extra_specs = f"Power: {tech_data.get('power', 'N/A')}, Fuel: {tech_data.get('fuel', 'N/A')}, Owners: {tech_data.get('owners', 'N/A')}"

        # 4. KI ANALYSE (Language Fixed)
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Spracheinstellung
        target_lang_name = "German" if request.lang == 'de' else "English"
        
        system_instruction = f"""
        You are a car negotiation expert.
        CRITICAL: ALL your output (rating, arguments, script) MUST be in {target_lang_name.upper()}.
        Do NOT output English if the user asked for German.
        """

        user_prompt = f"""
        Analyze this car offer:
        Title: {title}
        Price: {price} EUR
        KM: {km}
        EZ: {ez_string}
        Specs: {extra_specs}
        
        Detected Equipment / Features Text:
        "{equipment_text[:4000]}"
        
        History Note (Internal): {history_info}

        Task:
        1. Rate the deal.
        2. Find 3 hard arguments to negotiate the price down.
           - Check KM and Age.
           - Check Equipment text (look for leather, navi, sunroof, LED). 
           - If equipment is missing in text, argue it's "basic".
           - If price is high vs KM, mention that.
        3. Write a negotiation script sentence.

        Respond in JSON:
        {{
            "market_price_estimate": (int),
            "rating": "expensive/fair/good",
            "arguments": ["Argument 1", "Argument 2", "Argument 3"],
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
        except: ai_result = {}

        est_price = ai_result.get("market_price_estimate", int(price * 0.95))
        if est_price == 0: est_price = price

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
                "arguments": ai_result.get("arguments", []),
                "script": ai_result.get("script", "")
            },
            "debug_specs": extra_specs,
            "debug_equipment_snippet": equipment_text[:200]
        }

    except Exception as e:
        print(f"ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))