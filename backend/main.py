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

def clean_text(text):
    if not text: return ""
    text = re.sub(r'\n+', ' | ', text) 
    return re.sub(r'\s+', ' ', text).strip()

def remove_noise(soup):
    """
    Entfernt Navigation, Footer, Scripts und Werbung aus dem HTML.
    Das ist entscheidend, damit die KI nicht das Menü liest!
    """
    # Liste der Tags, die wir sicher löschen können
    noise_tags = ['header', 'footer', 'nav', 'script', 'style', 'noscript', 'iframe', 'svg']
    for tag in soup(noise_tags):
        tag.decompose()
        
    # Entferne Elemente anhand von Klassen (Cookie Banner, Menüs)
    noise_classes = re.compile(r'cookie|banner|menu|navigation|footer|header|legal|social', re.I)
    for tag in soup.find_all(class_=noise_classes):
        tag.decompose()
        
    return soup

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
        # 'networkidle2' wartet bis fast alles geladen ist -> Langsam
        # 'domcontentloaded' feuert früher (sobald HTML da ist) -> Schneller
        'wait_browser': 'domcontentloaded', 
        'block_resources': 'True',  # Blockiert Bilder & CSS (Riesiger Speed-Boost!)
        'block_ads': 'True'         # Blockiert Werbung
    }

    try:
        response = requests.get('https://app.scrapingbee.com/api/v1/', params=params)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # A) JSON-LD (Preis & Basis) - VOR dem Noise Removal holen!
        structured = extract_structured_data(soup)
        
        # B) CLEANUP (Müll entfernen)
        soup = remove_noise(soup)
        
        # C) BESCHREIBUNG & AUSSTATTUNG
        # Jetzt suchen wir im gesäuberten HTML
        
        full_description = ""
        
        # Strategie 1: Spezifische Container (Mobile/AutoScout)
        desc_candidates = soup.find_all(['div', 'p'], attrs={"data-testid": re.compile(r"description", re.I)})
        
        # Strategie 2: Wenn das fehlschlägt, suchen wir Textblöcke mit Keywords
        if not desc_candidates:
            # Suche nach Bereichen, die "Ausstattung" oder "Beschreibung" enthalten
            for tag in soup.find_all(['div', 'section']):
                txt = tag.get_text().lower()
                if ("ausstattung" in txt or "beschreibung" in txt) and len(txt) > 200:
                    desc_candidates.append(tag)

        # Text extrahieren
        for desc in desc_candidates:
            full_description += clean_text(desc.get_text(" | ")) + " | "

        # Zusätzlich Tabellen-Daten (Tech Specs)
        tech_data_text = ""
        dls = soup.find_all('dl')
        for dl in dls:
            tech_data_text += clean_text(dl.get_text(" : ")) + " | "
            
        # Fallback: Wenn wir immer noch nix haben, nimm den Body (der jetzt sauber ist!)
        if len(full_description) < 100:
             full_description = clean_text(soup.body.get_text())[:15000]

        raw_text_for_ai = f"""
        DESCRIPTION:
        {full_description[:10000]}
        
        SPECS:
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
             km_match = re.search(r'(\d{1,3}(?:\.?\d{3})*)\s*km', tech_data_text, re.IGNORECASE)
             if km_match: km = int(km_match.group(1).replace('.', ''))

        # 2. KI ANALYSE
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        system_instruction = """
        You are an expert car negotiator.
        Analyze the raw description text to identify negotiation points.
        
        CRITICAL: 
        1. The text might be messy. Look for keywords: "Sitzheizung", "Leder", "Navi", "ACC", "LED", "Scheckheft".
        2. If you find these features, ACKNOWLEDGE them. Do NOT say they are missing.
        3. Only argue "missing features" if the text really looks like a base model.
        
        OUTPUT FORMAT (JSON):
        Returns keys "de" and "en".
        Each contains: rating, arguments (array), script, market_price_estimate.
        """

        user_prompt = f"""
        Car: {title}
        Price: {price} EUR
        KM: {km}
        
        RAW TEXT (Scraped & Cleaned):
        "{raw_text_for_ai}"

        Generate dual-language JSON analysis.
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
            ai_result = {"de": {}, "en": {}}

        # DB Save
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
            "analysis": ai_result
        }

    except Exception as e:
        print(f"ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))