import os, json, requests
from playwright.sync_api import sync_playwright
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def send_tg(msg):
    requests.post(f"https://telegram.org{os.environ['TELEGRAM_TOKEN']}/sendMessage",
                  json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": msg, "parse_mode": "Markdown"})

def main():
    products = json.load(open("products.json"))
    history = json.load(open("price_history.json")) if os.path.exists("price_history.json") else {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36")

        for item in products:
            try:
                page.goto(item["url"], wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(2500)
                text = page.inner_text("body")[:14000]
                if len(text) < 800: continue

                res = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=f"Extract current selling price (INR) and best bank card instant discount from this text for {item['name']}. Return JSON: {{\"base\": number, \"net\": number, \"offer\": \"string\"}}.\n\n{text}",
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                data = json.loads(res.text)
                prev = history.get(item["url"])

                if prev and (data["net"] < prev["net"] or data["base"] < prev["base"] or data["offer"] != prev["offer"]):
                    send_tg(f"🚨 *{item['name']}*\nBase: ₹{data['base']:,} | Net: *₹{data['net']:,}*\nOffer: {data['offer']}\n[Link]({item['url']})")

                history[item["url"]] = data
            except Exception as e:
                print(item["name"], e)
        browser.close()

    json.dump(history, open("price_history.json", "w"), indent=2)

if __name__ == "__main__":
    main()
