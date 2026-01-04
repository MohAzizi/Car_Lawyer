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

# Speicher für Nutzersprachen (Im RAM - resettet bei Neustart, für MVP okay)
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

def parse_price(value):
    """Macht aus '24.000', '24k', '24.0' eine saubere Zahl 24000"""
    try:
        if isinstance(value, int): return value
        if isinstance(value, float): 
            # Wenn 24.0 -> wahrscheinlich 24000 gemeint, wenn Kontext Auto ist? 
            # Nein, KI könnte 24.000 gemeint haben.
            # Sicherer: Wenn < 1000, mal 1000 nehmen? Riskant.
            # Besser: Wir zwingen KI im Prompt zu Integers.
            return int(value)
        
        if isinstance(value, str):
            clean = value.lower().replace('k', '000').replace('.', '').replace(',', '')
            match = re.search(r'\d+', clean)
            if match:
                return int(match.group(0))
    except: pass
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
    if reply_markup:
        payload["reply_markup"] = reply_markup
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
    price = parse_price(structured.get('price', 0))
    km = 0
    if structured.get('km'): km = int(float(structured['km']))
    elif "km" in tech_data_text:
        km_match = re.search(r'(\d{1,3}(?:\.?\d{3})*)\s*km', tech_data_text, re.IGNORECASE)
        if km_match: km = int(km_match.group(1).replace('.', ''))

    # 2. KI (Vision & Text)
    valid_image_url = image_url if image_url and "http" in image_url else None
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # PROMPT UPDATE: Faktenbasiert & Integer-Zwang
    system_instruction = f"""
    You are a professional automotive expert and valuation analyst.
    Language: {lang.upper()} (Output ONLY in {lang.upper()}).

    TASK:
    Analyze the car deal based on FACTS:
    1. Depreciation: Is the price appropriate for the age/KM?
    2. Equipment: Does it lack standard features for this class? (e.g. "Missing Navi in luxury class").
    3. Market: Is it a "shelf warmer" (Standuhr)?
    
    CRITICAL RULES:
    - 'market_price_estimate' MUST be a plain INTEGER (e.g. 24500). NO dots, NO 'k'.
    - Arguments must be logical and specific to the car data.
    - Script must be professional but firm.
    
    Output JSON keys: "rating" (EXPENSIVE/FAIR/GOOD_DEAL), "arguments" (List of 3 strings), "script" (String), "market_price_estimate" (Integer).
    """

    user_message_content = [{
        "type": "text", 
        "text": f"ANALYZE: Title: {title}, Price: {price} EUR, KM: {km}\nDATA: {raw_text_for_ai}"
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
        # Direktes Ergebnis (kein nested 'de'/'en' mehr nötig, da wir Sprache im Prompt setzen)
        ai_result = json.loads(completion.choices[0].message.content)
    except Exception as e:
        print(f"KI Error: {e}")
        ai_result = {}

    # Datenstruktur für Frontend normalisieren (damit Frontend nicht bricht)
    final_output = {
        "meta": { "title": title, "url": url, "image": image_url },
        "data": { "price": price, "km": km },
        "analysis": {
            # Wir packen das Ergebnis in beide Keys, damit das Frontend immer was findet
            "de": ai_result, 
            "en": ai_result 
        }
    }
    
    # DB Save
    try:
        est = ai_result.get("market_price_estimate", price)
        supabase.table("scans").insert({
            "url": str(url), "title": str(title), "price": int(price),
            "ai_market_estimate": int(est) if est else 0, 
            "rating": str(ai_result.get("rating", "fair"))
        }).execute()
    except: pass

    return final_output

# --- ENDPOINTS ---

@app.get("/")
def read_root(): return {"status": "Deal Anwalt Online"}

@app.post("/analyze")
def analyze_endpoint(request: CarRequest):
    # Frontend ruft das auf (Standard DE vorerst, oder wir erweitern Request)
    return run_analysis_logic(request.url, "de")

# --- TELEGRAM BOT ---
@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        
        # 1. CALLBACK QUERY (Button Klick)
        if "callback_query" in data:
            cb = data["callback_query"]
            chat_id = cb["message"]["chat"]["id"]
            action = cb["data"]
            
            if action in ["lang_de", "lang_en"]:
                lang = "de" if action == "lang_de" else "en"
                USER_LANGUAGES[chat_id] = lang
                # Bestätigung senden
                msg = "🇩🇪 Sprache auf Deutsch gesetzt! Schick mir einen Link." if lang == "de" else "🇺🇸 Language set to English! Send me a link."
                send_telegram_message(chat_id, msg)
                # Callback schließen (wichtig für Telegram UX)
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb["id"]})
            return {"status": "ok"}

        # 2. NORMALE NACHRICHT
        if "message" not in data: return {"status": "ok"}
        
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")
        
        # START COMMAND
        if text == "/start":
            keyboard = {
                "inline_keyboard": [[
                    {"text": "🇩🇪 Deutsch", "callback_data": "lang_de"},
                    {"text": "🇺🇸 English", "callback_data": "lang_en"}
                ]]
            }
            send_telegram_message(chat_id, "Welcome! Please choose your language / Bitte wähle deine Sprache:", reply_markup=keyboard)
            return {"status": "ok"}

        # LINK CHECK
        url_match = re.search(r'(https?://[^\s]+)', text)
        if not url_match:
            send_telegram_message(chat_id, "Bitte schick mir einen Link von Mobile.de oder AutoScout24.\n(Oder tippe /start um die Sprache zu ändern).")
            return {"status": "ok"}
            
        url = url_match.group(1)
        
        # SPRACHE LADEN
        user_lang = USER_LANGUAGES.get(chat_id, "de") # Default Deutsch
        
        waiting_msg = "🕵️‍♂️ Analysiere..." if user_lang == "de" else "🕵️‍♂️ Analyzing..."
        send_telegram_message(chat_id, waiting_msg)

        try:
            # Analyse läuft...
            result = run_analysis_logic(url, user_lang)
            
            # Ergebnis holen
            # Da run_analysis_logic jetzt "de" und "en" gleich befüllt (mit der gewählten Sprache), nehmen wir einfach 'de' Key
            ai_data = result["analysis"]["de"] 
            
            est_price = parse_price(ai_data.get("market_price_estimate", 0))
            curr_price = result["data"]["price"]
            rating = ai_data.get("rating", "INFO")
            
            # Diff berechnen
            diff = curr_price - est_price
            
            # Text bauen
            if user_lang == "de":
                msg = f"🚗 *{result['meta']['title']}*\n\n"
                msg += f"💶 Aktuell: `{curr_price:,.0f} €`\n".replace(",", ".")
                msg += f"⚖️ Schätzung: `{est_price:,.0f} €`\n".replace(",", ".")
                msg += f"📊 Rating: *{rating}*\n\n"
                if diff > 0: msg += f"📉 *Verhandle um: -{diff:,.0f} €*\n\n".replace(",", ".")
                else: msg += f"✅ *Preis ist gut!*\n\n"
                msg += "🔥 *Argumente:*\n"
                for arg in ai_data.get("arguments", []): msg += f"• {arg}\n"
                msg += f"\n💬 *Script:*\n_{ai_data.get('script')}_"
            else:
                msg = f"🚗 *{result['meta']['title']}*\n\n"
                msg += f"💶 Current: `{curr_price:,} €`\n"
                msg += f"⚖️ Estimate: `{est_price:,} €`\n"
                msg += f"📊 Rating: *{rating}*\n\n"
                if diff > 0: msg += f"📉 *Negotiate: -{diff:,} €*\n\n"
                else: msg += f"✅ *Good Price!*\n\n"
                msg += "🔥 *Arguments:*\n"
                for arg in ai_data.get("arguments", []): msg += f"• {arg}\n"
                msg += f"\n💬 *Script:*\n_{ai_data.get('script')}_"

            send_telegram_message(chat_id, msg)
            
        except Exception as e:
            err_msg = "⚠️ Fehler beim Abruf." if user_lang == "de" else "⚠️ Error fetching data."
            print(f"Error: {e}")
            send_telegram_message(chat_id, err_msg)

    except Exception as e:
        print(f"Webhook Error: {e}")
    
    return {"status": "ok"}