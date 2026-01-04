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
        'wait_browser': 'domcontentloaded', 
        'block_resources': 'True', 
        'block_ads': 'True'
    }

    response = requests.get('https://app.scrapingbee.com/api/v1/', params=params)
    if response.status_code != 200: raise Exception("Webseite nicht erreichbar")

    soup = BeautifulSoup(response.content, 'html.parser')
    structured = extract_structured_data(soup)
    soup = remove_noise(soup)
    
    # Text Extraction
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
    
    # Preis & KM Parsing
    price = 0
    if structured.get('price'): price = int(float(structured['price']))
    
    km = 0
    if structured.get('km'): km = int(float(structured['km']))
    elif "km" in tech_data_text:
        km_match = re.search(r'(\d{1,3}(?:\.?\d{3})*)\s*km', tech_data_text, re.IGNORECASE)
        if km_match: km = int(km_match.group(1).replace('.', ''))

    # 2. KI (Vision & Text) mit STRUCTURED OUTPUTS
    valid_image_url = image_url if image_url and "http" in image_url else None
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # --- NEUER PROMPT NACH FEEDBACK ---
    system_instruction = f"""
    You are a professional automotive valuation expert.
    Language: Output ONLY in {lang.upper()}.

    FACT-BASED RULES (CRITICAL):
    1. Use ONLY provided data. Do NOT invent service records or accidents.
    2. If equipment is missing in text, assume "Standard Equipment" - do NOT assume it's missing unless it's unusual for the price.
    3. MARKET INDICATORS:
       - "Shelf warmer" (Standuhr) ONLY if evidence exists (e.g. old ad date).
       - Rating definitions: 
         * EXPENSIVE: >10% above market.
         * FAIR: +/- 10% market average.
         * GOOD_DEAL: >10% below market.

    OUTPUT STRUCTURE:
    - 'arguments' MUST be EXACTLY 3 strings.
    - Start arguments with prefixes: "Depreciation:", "Equipment:", "Market:".
    - 'market_price_estimate' MUST be a clean Integer.
    """

    user_message_content = [{
        "type": "text", 
        "text": f"ANALYZE: Title: {title}, Price: {price} EUR, KM: {km}\nDATA: {raw_text_for_ai}"
    }]
    
    if valid_image_url:
        user_message_content.append({
            "type": "image_url",
            "image_url": {"url": valid_image_url, "detail": "high"} # Upgrade auf HIGH für bessere Erkennung
        })

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_instruction}, {"role": "user", "content": user_message_content}],
            # --- NEU: STRICT JSON SCHEMA ---
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "car_analysis",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "rating": {"type": "string", "enum": ["EXPENSIVE", "FAIR", "GOOD_DEAL"]},
                            "arguments": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 3, # Wir zwingen die KI zu exakt 3 Argumenten
                                "maxItems": 3
                            },
                            "script": {"type": "string"},
                            "market_price_estimate": {"type": "integer"} # Zwingend Integer!
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
        # Sicherer Fallback
        ai_result = {
            "rating": "FAIR",
            "arguments": ["Daten konnten nicht vollständig analysiert werden.", "Bitte manuell prüfen.", "Preis scheint marktüblich."],
            "script": "Ich habe mir das Auto angesehen. Können wir über den Preis sprechen?",
            "market_price_estimate": price
        }

    # Datenstruktur für Frontend normalisieren
    final_output = {
        "meta": { "title": title, "url": url, "image": image_url },
        "data": { "price": price, "km": km },
        "analysis": {
            "de": ai_result, 
            "en": ai_result 
        }
    }
    
    # DB Save
    try:
        est = ai_result.get("market_price_estimate", price)
        supabase.table("scans").insert({
            "url": str(url), "title": str(title), "price": int(price),
            "ai_market_estimate": int(est), 
            "rating": str(ai_result.get("rating", "FAIR"))
        }).execute()
    except: pass

    return final_output

# --- ENDPOINTS ---

@app.get("/")
def read_root(): return {"status": "Deal Anwalt Online v3.0 (Strict)"}

@app.post("/analyze")
def analyze_endpoint(request: CarRequest):
    return run_analysis_logic(request.url, "de")

# --- TELEGRAM BOT ---
@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        
        # 1. CALLBACK QUERY
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

        # 2. MESSAGE
        if "message" not in data: return {"status": "ok"}
        
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        
        if text == "/start":
            keyboard = {"inline_keyboard": [[{"text": "🇩🇪 Deutsch", "callback_data": "lang_de"}, {"text": "🇺🇸 English", "callback_data": "lang_en"}]]}
            send_telegram_message(chat_id, "Bitte wähle deine Sprache / Choose language:", reply_markup=keyboard)
            return {"status": "ok"}

        url_match = re.search(r'(https?://[^\s]+)', text)
        if not url_match:
            send_telegram_message(chat_id, "Bitte Link senden oder /start für Sprache.")
            return {"status": "ok"}
            
        url = url_match.group(1)
        user_lang = USER_LANGUAGES.get(chat_id, "de")
        
        send_telegram_message(chat_id, "🕵️‍♂️..." if user_lang == "de" else "🕵️‍♂️ Analyzing...")

        try:
            result = run_analysis_logic(url, user_lang)
            ai_data = result["analysis"]["de"] # Ist jetzt die korrekte Sprache
            
            est_price = ai_data.get("market_price_estimate", 0)
            curr_price = result["data"]["price"]
            rating = ai_data.get("rating", "FAIR")
            
            diff = curr_price - est_price
            
            if user_lang == "de":
                msg = f"🚗 *{result['meta']['title']}*\n\n"
                msg += f"💶 Aktuell: `{curr_price:,.0f} €`\n".replace(",", ".")
                msg += f"⚖️ Schätzung: `{est_price:,.0f} €`\n".replace(",", ".")
                msg += f"📊 Rating: *{rating}*\n\n"
                if diff > 0: msg += f"📉 *Verhandle um: -{diff:,.0f} €*\n\n".replace(",", ".")
                else: msg += f"✅ *Guter Preis!*\n\n"
                msg += "🔥 *Fakten-Check:*\n"
                for arg in ai_data.get("arguments", []): 
                    # Formatierung verbessern
                    clean_arg = arg.replace("Depreciation:", "📉").replace("Equipment:", "🛠").replace("Market:", "📊")
                    msg += f"{clean_arg}\n"
                msg += f"\n💬 *Script:*\n_{ai_data.get('script')}_"
            else:
                msg = f"🚗 *{result['meta']['title']}*\n\n"
                msg += f"💶 Current: `{curr_price:,} €`\n"
                msg += f"⚖️ Estimate: `{est_price:,} €`\n"
                msg += f"📊 Rating: *{rating}*\n\n"
                if diff > 0: msg += f"📉 *Negotiate: -{diff:,} €*\n\n"
                else: msg += f"✅ *Good Price!*\n\n"
                msg += "🔥 *Facts:*\n"
                for arg in ai_data.get("arguments", []): 
                     clean_arg = arg.replace("Depreciation:", "📉").replace("Equipment:", "🛠").replace("Market:", "📊")
                     msg += f"{clean_arg}\n"
                msg += f"\n💬 *Script:*\n_{ai_data.get('script')}_"

            send_telegram_message(chat_id, msg)
            
        except Exception as e:
            print(f"Error: {e}")
            send_telegram_message(chat_id, "⚠️ Error.")

    except Exception as e:
        print(f"Webhook Error: {e}")
    
    return {"status": "ok"}