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
import dateutil.parser # Muss evtl. installiert werden: pip install python-dateutil

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

def extract_structured_data(soup):
    """
    Sucht nach JSON-LD und extrahiert zusätzlich das Datum (datePosted).
    """
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
            
            # Standard Felder
            if 'offers' in json_content:
                offer = json_content['offers']
                if isinstance(offer, list): offer = offer[0]
                data['price'] = offer.get('price')
            
            if 'mileageFromOdometer' in json_content:
                data['km'] = json_content['mileageFromOdometer'].get('value')
            
            if 'productionDate' in json_content:
                data['year'] = json_content.get('productionDate')

            if 'name' in json_content: data['title'] = json_content['name']
            
            # --- NEU: DATUMS-CHECK ---
            # Mobile/Autoscout nutzen oft 'datePosted', 'releaseDate' oder 'availabilityStarts'
            date_candidates = [
                json_content.get('datePosted'),
                json_content.get('releaseDate'),
                json_content.get('offers', {}).get('availabilityStarts') if isinstance(json_content.get('offers'), dict) else None
            ]
            
            for d in date_candidates:
                if d:
                    data['ad_date'] = d
                    break

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
    
    # 1. HISTORIEN-CHECK (Supabase Gedächtnis)
    history_info = ""
    price_drop = 0
    days_known = 0
    
    try:
        # Wir suchen nach dem letzten Eintrag für diese URL
        existing = supabase.table("scans")\
            .select("*")\
            .eq("url", request.url)\
            .order("created_at", desc=False)\
            .limit(1)\
            .execute()
            
        if existing.data and len(existing.data) > 0:
            first_scan = existing.data[0]
            old_price = first_scan['price']
            first_seen_date = dateutil.parser.isoparse(first_scan['created_at'])
            now = datetime.now(timezone.utc)
            
            days_known = (now - first_seen_date).days
            
            # Wir wissen den Preis erst später, aber wir merken uns, dass wir Historie haben
            history_info = f"Old scan found from {first_scan['created_at']} with price {old_price}."
            # Die eigentliche Diff-Berechnung machen wir, wenn wir den neuen Preis haben
            
    except Exception as e:
        print(f"History Check Error: {e}")

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
        
        structured = extract_structured_data(soup)
        
        # Meta & Title Parsing (wie vorher...)
        og_title = soup.find('meta', property='og:title')
        og_desc = soup.find('meta', property='og:description')
        og_image = soup.find('meta', property='og:image')
        
        title = structured.get('title') or (og_title['content'] if og_title else "Unbekannt")
        image_url = structured.get('image') or (og_image['content'] if og_image else None)
        
        # Text Extraction (Smart Selectors wie im letzten Schritt)
        relevant_text_parts = []
        descriptions = soup.find_all(attrs={"data-testid": re.compile(r"description|features|equipment", re.I)})
        for d in descriptions: relevant_text_parts.append(clean_text(d.get_text(" ")))
        
        lists = soup.find_all(['ul', 'dl', 'div'], class_=re.compile(r"equipment|feature|data|details", re.I))
        for l in lists:
            text = clean_text(l.get_text(" "))
            if len(text) > 50 and len(text) < 5000: relevant_text_parts.append(text)

        if not relevant_text_parts:
            body_text = soup.body.get_text(separator=' ', strip=True) if soup.body else ""
            relevant_text_parts.append(body_text[:10000])

        combined_features_text = " ".join(relevant_text_parts)

        # DATEN MAPPING
        price = 0
        if structured.get('price'): 
            try: price = int(float(structured['price']))
            except: pass
        if price == 0:
            price_match = re.search(r'(?:€|EUR)\s*(\d{1,3}(?:\.\d{3})*)', title)
            if price_match: price = int(price_match.group(1).replace('.', ''))

        km = 0
        if structured.get('km'): 
            try: km = int(float(structured['km']))
            except: pass
        
        ez_string = str(structured.get('year', 'Unbekannt'))
        
        # Ad-Datum aus JSON-LD?
        ad_date = structured.get('ad_date', 'Unbekannt')

        # 3. HISTORIEN LOGIK ABSCHLIESSEN
        history_message_for_ai = ""
        if history_info and price > 0:
            # Wir vergleichen mit dem alten Scan (old_price Variable muss hier verfügbar gemacht werden)
            try:
                old_price = existing.data[0]['price'] # Erneut holen sicherheitshalber
                diff = old_price - price
                
                if diff > 0:
                    history_message_for_ai = f"CRITICAL INTEL: We tracked this car. It was listed for {old_price} EUR {days_known} days ago. Price dropped by {diff} EUR! Use this to argue they are desperate."
                elif diff < 0:
                    history_message_for_ai = f"WARNING: Price INCREASED by {abs(diff)} EUR compared to {days_known} days ago."
                elif days_known > 14:
                    history_message_for_ai = f"CRITICAL INTEL: This car is in our database for {days_known} days. It's a 'shelf warmer' (Standuhr)."
            except: pass

        # Wenn wir ein Datum im JSON gefunden haben:
        if ad_date != 'Unbekannt':
             history_message_for_ai += f" The ad metadata indicates it was posted on {ad_date}."

        # 4. KI ANALYSE
        client = OpenAI(api_key=OPENAI_API_KEY)
        target_lang = "GERMAN" if request.lang == 'de' else "ENGLISH"
        
        system_instruction = f"""
        You are an expert car negotiator. Output in {target_lang}.
        
        SPECIAL MISSION: Use the "HISTORY INTEL" provided to pressure the seller.
        - If the car is old (days on market > 30), call it a "slow seller".
        - If the price dropped, mention that the market is rejecting the car.
        """

        user_prompt = f"""
        CAR DATA:
        - Title: {title}
        - Price: {price} EUR
        - KM: {km}
        - EZ: {ez_string}
        
        HISTORY INTEL (Only known by our system):
        "{history_message_for_ai}"
        
        EQUIPMENT TEXT: 
        "{combined_features_text[:6000]}"

        Generate JSON response:
        {{
            "market_price_estimate": (int),
            "rating": "expensive/fair/good",
            "arguments": ["Arg1 ({target_lang})", "Arg2 ({target_lang})", "Arg3 ({target_lang})"],
            "script": "Negotiation sentence in {target_lang} using history intel if available."
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

        # DB Save (Jeder Scan wird gespeichert -> Historie wächst)
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
            # Debugging: Sieh dir an, was die History Logik gefunden hat
            "debug_history": history_message_for_ai
        }

    except Exception as e:
        print(f"ERROR: {e}")
        raise HTTPException(status_code=500, detail=str(e))