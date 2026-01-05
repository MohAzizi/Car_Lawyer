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
    """Extrahiert Zahl aus Strings wie '24.990 €', 'VB 10.000'"""
    if not price_str: return 0
    # Entferne alles außer Ziffern
    clean = re.sub(r'[^\d]', '', str(price_str))
    try:
        return int(clean)
    except:
        return 0

def extract_kleinanzeigen_specifics(soup):
    """Spezial-Logik für Kleinanzeigen.de"""
    data = {}
    # Preis steht oft in ID 'viewad-price' oder Class 'ad-price'
    price_el = soup.find(id="viewad-price")
    if not price_el:
        price_el = soup.find(class_="ad-price")
    
    if price_el:
        data['price'] = parse_price_string(price_el.get_text())
        
    # Titel
    title_el = soup.find(id="viewad-title")
    if title_el: data['title'] = clean_text(title_el.get_text())
    
    # Details (KM etc) stehen oft in einer Liste
    details = soup.find_all("li", class_="addetailslist--detail")
    for detail in details:
        txt = detail.get_text()
        if "km" in txt.lower() and "kilometer" in txt.lower():
            data['km'] = parse_price_string(txt)
            
    return data

def extract_mobile_de_fallback(soup):
    """Spezial-Logik für Mobile.de"""
    data = {}
    # Preis
    price_meta = soup.find("meta", property="product:price:amount")
    if not price_meta: price_meta = soup.find("meta", property="og:price:amount")
    
    if price_meta: 
        data['price'] = parse_price_string(price_meta.get("content"))
    else:
        # Fallback im HTML
        price_el = soup.find(attrs={"data-testid": "prime-price"})
        if price_el: data['price'] = parse_price_string(price_el.get_text())

    # Titel
    title_meta = soup.find("meta", property="og:title")
    if title_meta: data['title'] = title_meta["content"]
    
    # Image
    img_meta = soup.find("meta", property="og:image")
    if img_meta: data['image'] = img_meta["content"]
    
    return data

def regex_price_search(text):
    """Der letzte Rettungsanker: Sucht nach Preis-Mustern im gesamten Text"""
    # Muster: "€ 10.000", "10.000 €", "10.000,-", "10000 Euro"
    # Wir suchen nach Zahlen zwischen 500 und 5.000.000 um Jahreszahlen (2024) auszuschließen
    matches = re.findall(r'(?:€|EUR)\s*(\d{1,3}(?:\.?\d{3})*)', text) # € 20.000
    if not matches:
        matches = re.findall(r'(\d{1,3}(?:\.?\d{3})*)\s*(?:€|EUR)', text) # 20.000 €
    
    for m in matches:
        val = parse_price_string(m)
        if 500 < val < 5000000: # Plausibilitäts-Check
            return val
    return 0

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
    
    # 1. SCRAPING
    params = {
        'api_key': SCRAPINGBEE_API_KEY,
        'url': url,
        'render_js': 'True', 
        'premium_proxy': 'True', 
        'country_code': 'de',
        'stealth_proxy': 'True', # Wichtig für Mobile.de
        'wait_browser': 'domcontentloaded', 
        'block_resources': 'False', 
        'block_ads': 'True'
    }

    try:
        response = requests.get('https://app.scrapingbee.com/api/v1/', params=params)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # --- EXTRAKTION START ---
        
        # 1. Structured Data (JSON-LD)
        data_struct = extract_structured_data(soup)
        
        # 2. Platform Specifics (Mobile / Kleinanzeigen)
        data_mobile = extract_mobile_de_fallback(soup)
        data_klein = extract_kleinanzeigen_specifics(soup)
        
        # 3. Zusammenführen
        price = int(float(data_struct.get('price') or data_mobile.get('price') or data_klein.get('price') or 0))
        km = int(float(data_struct.get('km') or data_mobile.get('km') or data_klein.get('km') or 0))
        title = data_struct.get('title') or data_mobile.get('title') or data_klein.get('title') or "Fahrzeug"
        image_url = data_struct.get('image') or data_mobile.get('image')

        # 4. NOTFALL-PLAN: Regex Suche im gesamten HTML (Wenn Preis immer noch 0)
        if price == 0:
            full_html_text = soup.get_text()
            price = regex_price_search(full_html_text)
            print(f"⚠️ Preis per Regex gefunden: {price}")

        # Text für KI vorbereiten
        soup = remove_noise(soup)
        body_text = clean_text(soup.body.get_text())[:12000]
        meta_desc = soup.find("meta", property="og:description")
        meta_text = meta_desc["content"] if meta_desc else ""
        
        full_text = f"TITLE: {title}\nMETA: {meta_text}\nCONTENT: {body_text}"
        
    except Exception as e:
        print(f"Scraping Error: {e}")
        title = "Analyse Fehler"
        price = 0
        km = 0
        image_url = None
        full_text = "Fehler beim Laden."

    # 2. KI ANALYSE
    valid_image_url = image_url if image_url and "http" in image_url else None
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    system_instruction = f"""
    You are a professional buyer's agent. Output ONLY in {lang.upper()}.

    YOUR MISSION:
    Determine a realistic NEGOTIATION TARGET PRICE.
    
    RULES:
    1. If input price is 0: TRY TO FIND THE PRICE IN THE TEXT. If found, use it as base. If not, estimate based on car model.
    2. Rating Logic:
       - "EXPENSIVE": Target 10-15% below asking.
       - "FAIR": Target 4-8% below asking.
       - "GOOD_DEAL": Target equal asking.
    
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
        
        # Wenn wir per Regex einen Preis gefunden haben, aber die KI 0 hatte, korrigieren wir hier NICHT mehr,
        # da wir den Regex-Preis schon in den Prompt geschickt haben.
        
    except Exception as e:
        print(f"KI Error: {e}")
        ai_result = {
            "rating": "FAIR",
            "arguments": ["Konnte Preis nicht validieren", "Daten unvollständig", "Manuelle Prüfung nötig"],
            "script": "Bitte prüfen Sie das Angebot manuell.",
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
def read_root(): return {"status": "Deal Anwalt Online v3.3 (Kleinanzeigen Fix)"}

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
            
            # Fallback wenn Preis 0 ist (damit nicht 0€ da steht)
            display_curr_price = f"{curr_price:,.0f} €".replace(",", ".") if curr_price > 0 else "❓ Unbekannt"
            
            if user_lang == "de":
                msg = f"🚗 *{result['meta']['title']}*\n\n"
                msg += f"💶 Aktuell: `{display_curr_price}`\n"
                msg += f"🎯 Zielpreis: `{est_price:,.0f} €`\n".replace(",", ".")
                msg += f"📊 Rating: *{rating}*\n\n"
                if diff > 0 and curr_price > 0: msg += f"📉 *Verhandlungsziel: -{diff:,.0f} €*\n\n".replace(",", ".")
                elif curr_price == 0: msg += f"⚠️ Preis im Inserat nicht gefunden. Schätzung basiert auf KI.\n\n"
                else: msg += f"✅ *Guter Preis!*\n\n"
                msg += "🔥 *Argumente:*\n"
                for arg in ai_data.get("arguments", []): 
                     clean_arg = arg.replace("Depreciation:", "📉").replace("Equipment:", "🛠").replace("Market:", "📊")
                     msg += f"{clean_arg}\n"
                msg += f"\n💬 *Script:*\n_{ai_data.get('script')}_"
            else:
                msg = f"🚗 *{result['meta']['title']}*\n\n"
                msg += f"💶 Current: `{display_curr_price}`\n"
                msg += f"🎯 Target: `{est_price:,} €`\n"
                msg += f"📊 Rating: *{rating}*\n\n"
                if diff > 0 and curr_price > 0: msg += f"📉 *Target Discount: -{diff:,} €*\n\n"
                elif curr_price == 0: msg += f"⚠️ Price missing in ad. Estimate is AI based.\n\n"
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