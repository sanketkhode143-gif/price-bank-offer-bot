import os, json, time, requests
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def send_tg(msg):
    requests.post(f"https://telegram.org{os.environ['TELEGRAM_TOKEN']}/sendMessage",
                  json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": msg, "parse_mode": "Markdown"})

def ask_ai(prompt):
    for m in ["gemini-3.8-flash", "gemini-3.8-flash-lite"]:
        for _ in range(3):
            try:
                res = client.models.generate_content(
                    model=m, contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                return json.loads(res.text)
            except Exception as e:
                print(f"Retry {m}: {e}")
                time.sleep(2)
    return None

def main():
    products = json.load(open("products.json"))
    history = json.load(open("price_history.json")) if os.path.exists("price_history.json") else {}

    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-IN", timezone_id="Asia/Kolkata"
        )
        page = context.new_page()

        for item in products:
            try:
                page.goto(item["url"], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3500)
                text = page.inner_text("body")
                print(item["name"], "len:", len(text))
                if len(text) < 800: continue

                data = ask_ai(f"Extract current selling price (INR) and best instant bank/card discount from this page for {item['name']}. Return JSON: {{\"base\": number, \"net\": number, \"offer\": \"string\"}}.\n\n{text[:15000]}")
                if not data or not data.get("net"): continue

                print(item["name"], "->", data)
                prev = history.get(item["url"])

                if prev and (data["net"] < prev["net"] or data["base"] < prev["base"] or data["offer"] != prev["offer"]):
                    send_tg(f"🚨 *{item['name']}*\nBase: ₹{data['base']:,} | Net: *₹{data['net']:,}*\nOffer: {data['offer']}\n[Link]({item['url']})")

                history[item["url"]] = data
                time.sleep(3)
            except Exception as e:
                print(item["name"], "err:", e)
        browser.close()

    json.dump(history, open("price_history.json", "w"), indent=2)

if __name__ == "__main__":
    main()
