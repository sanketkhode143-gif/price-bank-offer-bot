import os
import json
import time
import requests
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from google import genai
from google.genai import types

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

client = genai.Client(api_key=GEMINI_API_KEY)

def send_telegram(message):
    url = f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    })

def fetch_page_text(url):
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata"
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)

            # Retry once if Amazon/Flipkart serves a lightweight bot-check page
            if len(page.inner_text("body")) < 1000:
                page.wait_for_timeout(3000)
                page.reload(wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)

            offer_selectors = [
                "text=/Bank Offer/i",
                "text=/View.*more offers/i",
                "text=/Available offers/i",
                "text=/See All Offers/i"
            ]
            for sel in offer_selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=1000):
                        loc.click(timeout=1500)
                        page.wait_for_timeout(1500)
                except Exception:
                    pass

            title = page.title()
            text = page.inner_text("body")
            print(f"Loaded page title: {title[:80]} | Text length: {len(text)}")
            return text[:18000]
        finally:
            browser.close()

def analyze_offers(product_name, page_text):
    prompt = f"""
    Analyze this e-commerce product page text for "{product_name}".
    1. Find the current base selling price in INR (ignore MRP/strikethrough price).
    2. Scan all "Bank Offers", "Instant Discount", "Credit Card", or "EMI" offers visible on the page.
    3. Calculate the lowest possible NET EFFECTIVE PRICE after applying the single best instant bank/card discount (check minimum order value rules carefully so the discount actually applies to this item's price).
    4. Summarize the active bank offers in a clean, consistent one-line format (e.g., "HDFC ₹1500 off, ICICI ₹1000 off" or "None").

    Respond ONLY in valid JSON with this exact schema:
    {{
      "base_price": number,
      "net_effective_price": number,
      "bank_offer_summary": "string",
      "is_blocked_or_unavailable": boolean
    }}

    Page Text:
    {page_text}
    """
    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json")
    )
    return json.loads(response.text)

def main():
    with open("products.json", "r") as f:
        products = json.load(f)

    state_file = "price_history.json"
    history = json.load(open(state_file)) if os.path.exists(state_file) else {}

    for item in products:
        name = item["name"]
        url = item["url"]
        print(f"Checking: {name} -> {url}")

        try:
            text = fetch_page_text(url)
            if len(text) < 800:
                print(f"Skipping {name}: page blocked (text too short).")
                continue

            data = analyze_offers(name, text)
            print(f"AI Result for {name}: {data}")

            if data.get("is_blocked_or_unavailable") or not data.get("net_effective_price"):
                print(f"Skipping {name}: page blocked or out of stock.")
                continue

            base_price = data["base_price"]
            net_price = data["net_effective_price"]
            bank_offer = data.get("bank_offer_summary", "None").strip()

            prev = history.get(url)

            if prev is not None:
                prev_base = prev["base_price"]
                prev_net = prev["net_price"]
                prev_offer = prev.get("bank_offer", "None")

                price_dropped = (base_price < prev_base) or (net_price < prev_net)
                offer_changed = (bank_offer.lower() != prev_offer.lower()) and (bank_offer.lower() != "none")

                if price_dropped or offer_changed:
                    reasons = []
                    if base_price < prev_base:
                        reasons.append(f"Base price dropped (₹{prev_base:,} → ₹{base_price:,})")
                    if net_price < prev_net:
                        reasons.append(f"Net price dropped (₹{prev_net:,} → ₹{net_price:,})")
                    if offer_changed:
                        reasons.append("New/Updated Bank Offer")

                    msg = (
                        f"🚨 *{' & '.join(reasons)}*\n\n"
                        f"*{name}*\n"
                        f"• *Base Price:* ₹{base_price:,} _(was ₹{prev_base:,})_\n"
                        f"• *Best Net Price:* *₹{net_price:,}* _(was ₹{prev_net:,})_\n"
                        f"• *Bank Offers:* {bank_offer}\n\n"
                        f"[Open Product Link]({url})"
                    )
                    send_telegram(msg)

            history[url] = {
                "base_price": base_price,
                "net_price": net_price,
                "bank_offer": bank_offer
            }
            time.sleep(4)

        except Exception as e:
            print(f"Error checking {name}: {e}")

    with open(state_file, "w") as f:
        json.dump(history, f, indent=2)

if __name__ == "__main__":
    main()
