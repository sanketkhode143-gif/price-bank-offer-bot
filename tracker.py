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
    for m in ["gemini-3.5-flash-lite", "gemini-3.8-flash"]:
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

            prompt = f"""
            Analyze this Indian e-commerce product page text for "{item['name']}".
            Extract:
            1. "base": Current selling price in INR (ignore MRP/strikethrough).
            2. "full_discount": Best instant bank/card discount amount in INR applicable on FULL PAYMENT (one-time swipe, non-EMI). Put 0 if none.
            3. "net": Final net price for full payment (base - full_discount).
            4. "full_offer_text": Short summary of which bank/card gives the full payment discount (or "None").
            5. "nc_emi_months": Maximum No Cost EMI tenure available (e.g., "6 Months", "9 Months", "12 Months", or "None").
            6. "nc_emi_interest_discount": Maximum interest discount / upfront EMI discount amount in INR mentioned for No Cost EMI (e.g., "Upto ₹4,500 interest discount on HDFC/ICICI" or "None").

            Return ONLY valid JSON with these exact keys:
            {{"base": number, "full_discount": number, "net": number, "full_offer_text": "string", "nc_emi_months": "string", "nc_emi_interest_discount": "string"}}

            Page Text:
            {text[:18000]}
            """

            data = ask_ai(prompt)
            if not data or not data.get("net"):
                continue

            print(item["name"], "->", data, flush=True)
            prev = history.get(item["url"])

            changed = False
            if prev:
                if (
                    data["net"] < prev.get("net", data["net"])
                    or data["base"] < prev.get("base", data["base"])
                    or data["full_offer_text"] != prev.get("full_offer_text", "")
                    or data["nc_emi_months"] != prev.get("nc_emi_months", "")
                    or data["nc_emi_interest_discount"] != prev.get("nc_emi_interest_discount", "")
                ):
                    changed = True

            if changed:
                msg = (
                    f"🚨 *PRICE / OFFER UPDATE*\n\n"
                    f"*{item['name']}*\n"
                    f"• *Base Price:* ₹{data['base']:,}\n\n"
                    f"💳 *Full Payment Offer:*\n"
                    f"• *Discount:* ₹{data['full_discount']:,} ({data['full_offer_text']})\n"
                    f"• *Final Net Price:* *₹{data['net']:,}*\n\n"
                    f"📅 *No Cost EMI Offer:*\n"
                    f"• *Max Tenure:* {data['nc_emi_months']}\n"
                    f"• *Interest Discount:* {data['nc_emi_interest_discount']}\n\n"
                    f"[Open Product Link]({item['url']})"
                )
                send_tg(msg)

            history[item["url"]] = data
            time.sleep(1)
        except Exception as e:
            print(item["name"], "err:", e, flush=True)

    json.dump(history, open("price_history.json", "w"), indent=2)

if __name__ == "__main__":
    main()
