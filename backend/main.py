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
    # lang brauchen wir im Request nicht mehr zwingend für die Logik, 
    # da wir jetzt IMMER beide Sprachen liefern.

def clean_text(text):
    if not text: return ""
    # Wir behalten Newlines bei, damit Listen erkennbar bleiben!
    text = re.sub(r'\n+', ' | ', text) 
    return re.sub(r'\s+', ' ', text).strip()

def extract_structured_data(soup):
    """ JSON-LD Extraction """
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
    print(f"🔎 Analysiere: {request.url}")
    
    # 1. SCRAPING
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
        
        # A) JSON-LD (Preis & Basis)
        structured = extract_structured_data(soup)
        
        # B) BESCHREIBUNG & AUSSTATTUNG (Das Wichtigste!)
        # Wir suchen gezielt nach der Description-Box von AutoScout/Mobile
        # Oft in div data-testid="description" oder class="description"
        full_description = ""
        
        # Versuche verschiedene gängige Container für Beschreibungen
        desc_candidates = soup.find_all(['div', 'p'], attrs={"data-testid": re.compile(r"description", re.I)})
        if not desc_candidates:
            desc_candidates = soup.find_all(['div'], class_=re.compile(r"description|sc-grid-col-12", re.I))

        for desc in desc_candidates:
            # Hier holen wir den rohen Text mit Trennern, damit die Liste erhalten bleibt
            full_description += clean_text(desc.get_text(" | ")) + " | "

        # Zusätzlich spezifische Ausstattungslisten (Data Grid)
        tech_data_text = ""
        dls = soup.find_all('dl')
        for dl in dls:
            tech_data_text += clean_text(dl.get_text(" : ")) + " | "
            
        # Fallback: Wenn wir kaum Text haben, nimm den Body (aber sauberer)
        if len(full_description) < 100:
             full_description = clean_text(soup.body.get_text())[:15000]

        # Kombinierter Text für die KI
        raw_text_for_ai = f"""
        DESCRIPTION TEXT:
        {full_description[:10000]}
        
        TECH SPECS:
        {tech_data_text[:3000]}
        """

        # Daten Mapping
        title = structured.get('title') or "Fahrzeug"
        image_url = structured.get('image')
        
        price = 0
        if structured.get('price'): price = int(float(structured['price']))
        
        km = 0
        if structured.get('km'): km = int(float(structured['km']))
        elif "km" in tech_data_text:
             # Einfacher Regex Fallback für KM im Text
             km_match = re.search(r'(\d{1,3}(?:\.?\d{3})*)\s*km', tech_data_text, re.IGNORECASE)
             if km_match: km = int(km_match.group(1).replace('.', ''))

        # 2. KI ANALYSE (JETZT MULTILINGUAL)
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        system_instruction = """
        You are an expert car negotiator.
        Your task is to analyze the car data and the raw description text to identify negotiation points.
        
        CRITICAL: The description often contains a raw list of features (e.g., "SITZHEIZUNG", "ACC", "LED"). 
        SCAN THE TEXT CAREFULLY. Do NOT say equipment is missing if it is listed in the text.
        
        OUTPUT FORMAT:
        You must return a JSON object with TWO main keys: "de" (German) and "en" (English).
        Each key must contain:
        - rating (string: "expensive", "fair", "good_deal" / "teuer", "fair", "guter_deal")
        - arguments (array of 3 strings)
        - script (string, 1-2 sentences)
        - market_price_estimate (number, same for both usually)
        """

        user_prompt = f"""
        Car: {title}
        Price: {price} EUR
        KM: {km}
        
        RAW TEXT FROM WEBSITE (Search here for equipment like 'Navi', 'Leder', 'ACC'!):
        "{raw_text_for_ai}"

        Generate the dual-language JSON analysis now.
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
            ai_result = {"de": {}, "en": {}} # Fallback

        # DB Save (Wir nehmen die deutschen Werte für die DB Statistik)
        try:
            de_data = ai_result.get("de", {})
            est = de_data.get("market_price_estimate", price)
            supabase.table("scans").insert({
                "url": str(request.url),
                "title": str(title),
                "price": int(price),
                "ai_market_estimate": int(est),
                "rating": str(de_data.get("rating", "fair"))
            }).execute()
        except: pass

        return {
            "meta": { "title": title, "url": request.url, "image": image_url },
            "data": { "price": price, "km": km },
            "analysis": ai_result, # Gibt jetzt { "de": {...}, "en": {...} } zurück
            "debug_snippet": raw_text_for_ai[:500]
        }

    except Exception as e:
        print(f"ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))