import os, json, time, requests
from curl_cffi import requests as cffi_req
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def send_tg(msg):
    api = "https://api." + "telegram.org/bot" + os.environ["TELEGRAM_TOKEN"] + "/sendMessage"
    requests.post(api, json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": msg, "parse_mode": "Markdown"}, timeout=10)

def ask_ai(prompt):
    for m in ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"]:
        for _ in range(2):
            try:
                res = client.models.generate_content(
                    model=m, contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                return json.loads(res.text)
            except Exception as e:
                print("AI retry:", m, e, flush=True)
                time.sleep(2)
    return None

def get_page_text(url):
    headers = {"Accept-Language": "en-IN,en;q=0.9", "Referer": "https://google.com"}
    for browser in ["chrome", "chrome124", "safari"]:
        r = cffi_req.get(url.strip(), impersonate=browser, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
        if len(text) >= 800:
            return text
        time.sleep(1.5)
    return ""

def main():
    products = json.load(open("products.json"))
    history = json.load(open("price_history.json")) if os.path.exists("price_history.json") else {}

    for item in products:
        try:
            text = get_page_text(item["url"])
            print(item["name"], "len:", len(text), flush=True)

            if len(text) < 800:
                continue

            data = ask_ai(f"Extract current selling price (INR) and best instant bank/card discount from this product page text for {item['name']}. Return JSON: {{\"base\": number, \"net\": number, \"offer\": \"string\"}}.\n\n{text[:15000]}")
            if not data or not data.get("net"):
                continue

            print(item["name"], "->", data, flush=True)
            prev = history.get(item["url"])

            if prev and (data["net"] < prev["net"] or data["base"] < prev["base"] or data["offer"] != prev["offer"]):
                send_tg(f"🚨 *{item['name']}*\nBase: ₹{data['base']:,} | Net: *₹{data['net']:,}*\nOffer: {data['offer']}\n[Link]({item['url']})")

            history[item["url"]] = data
            time.sleep(1)
        except Exception as e:
            print(item["name"], "err:", e, flush=True)

    json.dump(history, open("price_history.json", "w"), indent=2)

if __name__ == "__main__":
    main()
