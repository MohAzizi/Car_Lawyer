from fastapi import FastAPI, HTTPException, Request
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase Client initialisiert")
except Exception as e:
    print(f"❌ Supabase Error: {e}")

class CarRequest(BaseModel):
    url: str

# --- HILFSFUNKTIONEN ---

def clean_text(text):
    if not text: return ""
    text = re.sub(r'\n+', ' | ', text) 
    return re.sub(r'\s+', ' ', text).strip()

def remove_noise(soup):
    noise_tags = ['header', 'footer', 'nav', 'script', 'style', 'noscript', 'iframe', 'svg']
    for tag in soup(noise_tags): tag.decompose()
    noise_classes = re.compile(r'cookie|banner|menu|navigation|footer|header|legal|social', re.I)
    for tag in soup.find_all(class_=noise_classes): tag.decompose()
    return soup

def extract_structured_data(soup):
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

def send_telegram_message(chat_id, text):
    """Sendet eine Nachricht an den Nutzer via Telegram"""
    if not TELEGRAM_BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

# --- KERN-LOGIK (Ausgelagert, damit Bot UND API sie nutzen können) ---
def run_analysis_logic(url: str, lang: str = "de"):
    print(f"⚙️ Core Logic läuft für: {url}")
    
    # 1. SCRAPING
    params = {
        'api_key': SCRAPINGBEE_API_KEY,
        'url': url,
        'render_js': 'True', 
        'premium_proxy': 'True', 
        'country_code': 'de',
        'wait_browser': 'domcontentloaded', 
        'block_resources': 'True', 
        'block_ads': 'True'
    }

    response = requests.get('https://app.scrapingbee.com/api/v1/', params=params)
    if response.status_code != 200:
        raise Exception("Konnte Webseite nicht laden.")

    soup = BeautifulSoup(response.content, 'html.parser')
    structured = extract_structured_data(soup)
    soup = remove_noise(soup)
    
    full_description = ""
    desc_candidates = soup.find_all(['div', 'p'], attrs={"data-testid": re.compile(r"description", re.I)})
    if not desc_candidates:
        for tag in soup.find_all(['div', 'section']):
            txt = tag.get_text().lower()
            if ("ausstattung" in txt or "beschreibung" in txt) and len(txt) > 200:
                desc_candidates.append(tag)
    for desc in desc_candidates: full_description += clean_text(desc.get_text(" | ")) + " | "

    tech_data_text = ""
    for dl in soup.find_all('dl'): tech_data_text += clean_text(dl.get_text(" : ")) + " | "
        
    if len(full_description) < 100: full_description = clean_text(soup.body.get_text())[:15000]

    raw_text_for_ai = f"DESCRIPTION: {full_description[:10000]}\nSPECS: {tech_data_text[:3000]}"

    title = structured.get('title') or "Fahrzeug"
    image_url = structured.get('image')
    price = 0
    if structured.get('price'): price = int(float(structured['price']))
    km = 0
    if structured.get('km'): km = int(float(structured['km']))
    elif "km" in tech_data_text:
            km_match = re.search(r'(\d{1,3}(?:\.?\d{3})*)\s*km', tech_data_text, re.IGNORECASE)
            if km_match: km = int(km_match.group(1).replace('.', ''))

    # 2. KI (Vision & Text)
    valid_image_url = image_url if image_url and "http" in image_url else None
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    system_instruction = """
    You are a ruthless car dealer purchasing expert. 
    Goal: Devalue the car.
    
    Output JSON keys "de" and "en".
    Each contains: rating (expensive/fair/good), arguments (array of 3 points), script (1 sentence), market_price_estimate (number).
    """

    user_message_content = [{
        "type": "text", 
        "text": f"ANALYZE: Title: {title}, Price: {price}, KM: {km}\nTEXT: {raw_text_for_ai}"
    }]
    
    if valid_image_url:
        user_message_content.append({
            "type": "image_url",
            "image_url": {"url": valid_image_url, "detail": "low"}
        })

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_instruction}, {"role": "user", "content": user_message_content}],
            response_format={ "type": "json_object" }
        )
        ai_result = json.loads(completion.choices[0].message.content)
    except:
        ai_result = {"de": {}, "en": {}}

    # DB Save
    try:
        de_data = ai_result.get("de", {})
        est = de_data.get("market_price_estimate", price)
        supabase.table("scans").insert({
            "url": str(url), "title": str(title), "price": int(price),
            "ai_market_estimate": int(est), "rating": str(de_data.get("rating", "fair"))
        }).execute()
    except: pass

    return {
        "meta": { "title": title, "url": url, "image": image_url },
        "data": { "price": price, "km": km },
        "analysis": ai_result
    }

# --- ENDPOINTS ---

@app.get("/")
def read_root():
    return {"status": "Deal Anwalt is Online 🚀"}

@app.post("/analyze")
def analyze_endpoint(request: CarRequest):
    # Der normale Web-Endpoint ruft jetzt die Logik-Funktion auf
    try:
        return run_analysis_logic(request.url)
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- TELEGRAM WEBHOOK ---
@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        
        # Prüfung: Ist es eine Nachricht?
        if "message" not in data: return {"status": "ok"}
        
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        print(f"📩 Telegram Nachricht von {chat_id}: {text}")

        # Prüfung: Ist es ein Link?
        if "http" not in text:
            send_telegram_message(chat_id, "👋 Hallo! Schick mir einen Link von Mobile.de oder AutoScout24 und ich checke den Deal für dich.")
            return {"status": "ok"}

        # Link extrahieren (falls Text dabei steht)
        url_match = re.search(r'(https?://[^\s]+)', text)
        if not url_match:
            send_telegram_message(chat_id, "❌ Ich finde keinen gültigen Link.")
            return {"status": "ok"}
            
        url = url_match.group(1)
        
        # Start-Nachricht
        send_telegram_message(chat_id, "🕵️‍♂️ Ich analysiere den Deal... Gib mir 10-15 Sekunden.")

        # Analyse ausführen
        try:
            result = run_analysis_logic(url)
            
            # Ergebnis formatieren (für Telegram)
            ai_data = result["analysis"]["de"]
            est_price = ai_data.get("market_price_estimate", result["data"]["price"])
            curr_price = result["data"]["price"]
            diff = curr_price - est_price
            rating = ai_data.get("rating", "Unbekannt")
            
            msg = f"🚗 *{result['meta']['title']}*\n"
            msg += f"Aktuell: {curr_price} €\n"
            msg += f"Schätzung: {est_price} €\n"
            msg += f"Bewertung: *{rating.upper()}*\n\n"
            
            if diff > 0:
                msg += f"📉 *Verhandle um: -{diff} €*\n\n"
            else:
                msg += f"✅ *Guter Preis!*\n\n"
                
            msg += "🔥 *Deine Argumente:*\n"
            for arg in ai_data.get("arguments", []):
                msg += f"- {arg}\n"
                
            msg += f"\n🗣 *Sag dem Händler:*\n_{ai_data.get('script')}_"
            
            send_telegram_message(chat_id, msg)
            
        except Exception as e:
            print(f"Analysis Error: {e}")
            send_telegram_message(chat_id, "⚠️ Fehler bei der Analyse. Ist der Link noch gültig?")

    except Exception as e:
        print(f"Webhook Error: {e}")
    
    return {"status": "ok"}