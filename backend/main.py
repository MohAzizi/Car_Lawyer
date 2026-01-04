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

USER_LANGUAGES = {} 

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
    return soup

def parse_price_string(price_str):
    """Extrahiert Zahl aus Strings wie '24.990 €'"""
    if not price_str: return 0
    clean = re.sub(r'[^\d]', '', str(price_str))
    return int(clean) if clean else 0

def extract_mobile_de_fallback(soup):
    """Spezial-Extraktion für Mobile.de über Meta-Tags (Robuster gegen Blocking)"""
    data = {}
    
    # 1. Preis aus Meta-Tags oder spezifischen Attributen
    # Mobile nutzt oft 'og:price:amount' oder spezifische data-testids
    price_meta = soup.find("meta", property="product:price:amount")
    if not price_meta:
        price_meta = soup.find("meta", property="og:price:amount")
    
    if price_meta and price_meta.get("content"):
        data['price'] = parse_price_string(price_meta["content"])
    else:
        # Versuche HTML Fallback
        price_el = soup.find(attrs={"data-testid": "prime-price"})
        if price_el: data['price'] = parse_price_string(price_el.get_text())

    # 2. Titel
    title_meta = soup.find("meta", property="og:title")
    if title_meta: data['title'] = title_meta["content"]

    # 3. Bild
    img_meta = soup.find("meta", property="og:image")
    if img_meta: data['image'] = img_meta["content"]

    # 4. KM und Daten aus Description Meta (Oft steht da: "BMW 320d, 150.000 km...")
    desc_meta = soup.find("meta", property="og:description")
    if desc_meta:
        desc_text = desc_meta["content"]
        data['description'] = desc_text
        # KM suchen
        km_match = re.search(r'(\d{1,3}(?:\.?\d{3})*)\s*km', desc_text, re.IGNORECASE)
        if km_match:
            data['km'] = int(km_match.group(1).replace('.', ''))
    
    return data

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

def send_telegram_message(chat_id, text, reply_markup=None):
    if not TELEGRAM_BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup: payload["reply_markup"] = reply_markup
    requests.post(url, json=payload)

# --- CORE LOGIC ---
def run_analysis_logic(url: str, lang: str = "de"):
    print(f"⚙️ Core Logic läuft für: {url} (Sprache: {lang})")
    
    # 1. SCRAPING (Optimiert für Mobile.de)
    params = {
        'api_key': SCRAPINGBEE_API_KEY,
        'url': url,
        'render_js': 'True', 
        'premium_proxy': 'True', 
        'country_code': 'de',
        # WICHTIG: Stealth Proxy aktiviert spezielle Anti-Bot Umgehung
        'stealth_proxy': 'True', 
        'wait_browser': 'domcontentloaded', # Schneller, oft reicht das
        'block_resources': 'False', 
        'block_ads': 'True'
    }

    try:
        response = requests.get('https://app.scrapingbee.com/api/v1/', params=params)
        
        # Fehlerbehandlung wenn ScrapingBee blockiert wird
        if response.status_code != 200:
            print(f"⚠️ ScrapingBee Error: {response.status_code} - {response.text}")
            # Wir werfen keinen harten Fehler, sondern versuchen es mit Dummy-Daten, 
            # damit das Frontend nicht 'Application Error' wirft, sondern eine Nachricht.
            raise Exception("Seite konnte nicht geladen werden (Bot-Schutz).")

        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extraktion
        structured = extract_structured_data(soup)
        mobile_fallback = extract_mobile_de_fallback(soup) # Neuer Fallback
        
        # Zusammenführen (Structured > Mobile Fallback > Default)
        title = structured.get('title') or mobile_fallback.get('title') or "Fahrzeug"
        price = int(float(structured.get('price') or mobile_fallback.get('price') or 0))
        km = int(float(structured.get('km') or mobile_fallback.get('km') or 0))
        image_url = structured.get('image') or mobile_fallback.get('image')

        # Text für KI
        # Wenn wir keinen Body-Text haben (weil Blocked), nehmen wir die Meta-Description
        soup = remove_noise(soup)
        body_text = clean_text(soup.body.get_text())[:12000]
        meta_desc = mobile_fallback.get('description', '')
        
        full_text = f"META INFO: {meta_desc}\n\nPAGE CONTENT: {body_text}"
        
    except Exception as e:
        print(f"Scraping Critical Error: {e}")
        # Notfall-Fallback, damit Frontend nicht abstürzt
        title = "Analyse fehlgeschlagen"
        price = 0
        km = 0
        image_url = None
        full_text = "Fehler beim Laden der Seite."

    # 2. KI ANALYSE
    valid_image_url = image_url if image_url and "http" in image_url else None
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    system_instruction = f"""
    You are a professional buyer's agent. Output ONLY in {lang.upper()}.

    YOUR MISSION:
    Determine a realistic NEGOTIATION TARGET PRICE.
    
    PRICE RULES:
    1. If rating "EXPENSIVE": Target MUST be 10-15% below asking.
    2. If rating "FAIR": Target MUST be 4-8% below asking.
    3. If rating "GOOD_DEAL": Target can be equal asking.
    4. If price is 0 (scrape error), estimate purely based on car title if possible, or set to 0.
    
    Output JSON strict format:
    {{
        "rating": "EXPENSIVE" | "FAIR" | "GOOD_DEAL",
        "arguments": ["Arg1", "Arg2", "Arg3"],
        "script": "Negotiation sentence",
        "market_price_estimate": Integer
    }}
    """

    user_message_content = [{
        "type": "text", 
        "text": f"ANALYZE: Title: {title}, Asking Price: {price} EUR, KM: {km}\nRAW DATA: {full_text}"
    }]
    
    if valid_image_url:
        user_message_content.append({
            "type": "image_url",
            "image_url": {"url": valid_image_url, "detail": "high"}
        })

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_instruction}, {"role": "user", "content": user_message_content}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "car_analysis",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "rating": {"type": "string", "enum": ["EXPENSIVE", "FAIR", "GOOD_DEAL"]},
                            "arguments": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
                            "script": {"type": "string"},
                            "market_price_estimate": {"type": "integer"}
                        },
                        "required": ["rating", "arguments", "script", "market_price_estimate"],
                        "additionalProperties": False
                    }
                }
            }
        )
        ai_result = json.loads(completion.choices[0].message.content)
    except Exception as e:
        print(f"KI Error: {e}")
        ai_result = {
            "rating": "FAIR",
            "arguments": ["Link konnte nicht vollständig gelesen werden.", "Bitte Daten manuell prüfen.", "Mobile.de Blockade möglich."],
            "script": "Konnte das Fahrzeug nicht analysieren.",
            "market_price_estimate": price
        }

    final_output = {
        "meta": { "title": title, "url": url, "image": image_url },
        "data": { "price": price, "km": km },
        "analysis": { "de": ai_result, "en": ai_result }
    }
    
    # DB Save
    try:
        supabase.table("scans").insert({
            "url": str(url), "title": str(title), "price": int(price),
            "ai_market_estimate": int(ai_result['market_price_estimate']), 
            "rating": str(ai_result['rating'])
        }).execute()
    except: pass

    return final_output

# --- ENDPOINTS ---
@app.get("/")
def read_root(): return {"status": "Deal Anwalt Online v3.2 (Stealth Mode)"}

@app.post("/analyze")
def analyze_endpoint(request: CarRequest):
    return run_analysis_logic(request.url, "de")

# --- TELEGRAM BOT ---
@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        if "callback_query" in data:
            cb = data["callback_query"]
            chat_id = cb["message"]["chat"]["id"]
            action = cb["data"]
            if action in ["lang_de", "lang_en"]:
                lang = "de" if action == "lang_de" else "en"
                USER_LANGUAGES[chat_id] = lang
                msg = "🇩🇪 Sprache: Deutsch" if lang == "de" else "🇺🇸 Language: English"
                send_telegram_message(chat_id, msg)
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"]})
            return {"status": "ok"}

        if "message" not in data: return {"status": "ok"}
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        
        if text == "/start":
            keyboard = {"inline_keyboard": [[{"text": "🇩🇪 Deutsch", "callback_data": "lang_de"}, {"text": "🇺🇸 English", "callback_data": "lang_en"}]]}
            send_telegram_message(chat_id, "Wähle deine Sprache / Choose language:", reply_markup=keyboard)
            return {"status": "ok"}

        url_match = re.search(r'(https?://[^\s]+)', text)
        if not url_match:
            send_telegram_message(chat_id, "Bitte Link senden.")
            return {"status": "ok"}
            
        url = url_match.group(1)
        user_lang = USER_LANGUAGES.get(chat_id, "de")
        send_telegram_message(chat_id, "🕵️‍♂️..." if user_lang == "de" else "🕵️‍♂️ Analyzing...")

        try:
            result = run_analysis_logic(url, user_lang)
            ai_data = result["analysis"]["de"]
            est_price = ai_data.get("market_price_estimate", 0)
            curr_price = result["data"]["price"]
            rating = ai_data.get("rating", "FAIR")
            diff = curr_price - est_price
            
            if user_lang == "de":
                msg = f"🚗 *{result['meta']['title']}*\n\n"
                msg += f"💶 Aktuell: `{curr_price:,.0f} €`\n".replace(",", ".")
                msg += f"🎯 Zielpreis: `{est_price:,.0f} €`\n".replace(",", ".")
                msg += f"📊 Rating: *{rating}*\n\n"
                if diff > 0: msg += f"📉 *Verhandlungsziel: -{diff:,.0f} €*\n\n".replace(",", ".")
                else: msg += f"✅ *Guter Preis!*\n\n"
                msg += "🔥 *Argumente:*\n"
                for arg in ai_data.get("arguments", []): 
                     clean_arg = arg.replace("Depreciation:", "📉").replace("Equipment:", "🛠").replace("Market:", "📊")
                     msg += f"{clean_arg}\n"
                msg += f"\n💬 *Script:*\n_{ai_data.get('script')}_"
            else:
                msg = f"🚗 *{result['meta']['title']}*\n\n"
                msg += f"💶 Current: `{curr_price:,} €`\n"
                msg += f"🎯 Target: `{est_price:,} €`\n"
                msg += f"📊 Rating: *{rating}*\n\n"
                if diff > 0: msg += f"📉 *Target Discount: -{diff:,} €*\n\n"
                else: msg += f"✅ *Good Deal!*\n\n"
                msg += "🔥 *Arguments:*\n"
                for arg in ai_data.get("arguments", []): 
                     clean_arg = arg.replace("Depreciation:", "📉").replace("Equipment:", "🛠").replace("Market:", "📊")
                     msg += f"{clean_arg}\n"
                msg += f"\n💬 *Script:*\n_{ai_data.get('script')}_"

            send_telegram_message(chat_id, msg)
        except Exception as e:
            print(f"Error: {e}")
            send_telegram_message(chat_id, "⚠️ Fehler beim Abruf.")
    except: pass
    return {"status": "ok"}