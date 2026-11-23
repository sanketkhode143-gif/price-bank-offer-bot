import os, json, time, requests
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def send_tg(msg):
    api = "https://api." + "telegram.org/bot" + os.environ["TELEGRAM_TOKEN"] + "/sendMessage"
    requests.post(api, json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": msg, "parse_mode": "Markdown"})

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
                print("Retry:", m, e)
                time.sleep(2)
    return None

def main():
    products = json.load(open("products.json"))
    history = json.load(open("price_history.json")) if os.path.exists("price_history.json") else {}

    for item in products:
        try:
            reader = "https://r." + "jina.ai/" + item["url"].strip()
            r = requests.get(reader, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            text = r.text
            print(item["name"], "len:", len(text))
            if len(text) < 800: continue

            data = ask_ai(f"Extract current selling price (INR) and best instant bank/card discount from this page for {item['name']}. Return JSON: {{\"base\": number, \"net\": number, \"offer\": \"string\"}}.\n\n{text[:15000]}")
            if not data or not data.get("net"): continue

            print(item["name"], "->", data)
            prev = history.get(item["url"])

            if prev and (data["net"] < prev["net"] or data["base"] < prev["base"] or data["offer"] != prev["offer"]):
                send_tg(f"🚨 *{item['name']}*\nBase: ₹{data['base']:,} | Net: *₹{data['net']:,}*\nOffer: {data['offer']}\n[Link]({item['url']})")

            history[item["url"]] = data
            time.sleep(2)
        except Exception as e:
            print(item["name"], "err:", e)

    json.dump(history, open("price_history.json", "w"), indent=2)

if __name__ == "__main__":
    main()
