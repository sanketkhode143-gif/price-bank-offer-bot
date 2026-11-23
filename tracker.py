import os, re, json, time, requests
from curl_cffi import requests as cffi_req
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])
TG_BASE = "https://api." + "telegram.org/bot" + TG_TOKEN

def send_tg(msg):
    requests.post(f"{TG_BASE}/sendMessage", json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown", "disable_web_page_preview": True}, timeout=10)

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

def clean_url(url):
    url = url.strip()
    m = re.search(r"amazon\.in.*?/dp/([A-Z0-9]{10})", url)
    if m:
        return "https://www." + "amazon.in/dp/" + m.group(1)
    return url.split("?")

def get_page_info(url):
    headers = {"Accept-Language": "en-IN,en;q=0.9", "Referer": "https://google.com"}
    for browser in ["chrome", "chrome124", "safari"]:
        r = cffi_req.get(url, impersonate=browser, headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        raw_title = soup.title.string.strip() if soup.title and soup.title.string else "Product"
        title = re.sub(r"^(Amazon\.in\s*:\s*|Buy\s+)", "", raw_title).split(":").split("|")[:60].strip()
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
        if len(text) >= 800:
            return title, text
        time.sleep(1.5)
    return "Product", ""

def process_telegram_commands(products, history):
    offset_file = "tg_offset.txt"
    offset = int(open(offset_file).read().strip()) if os.path.exists(offset_file) else 0
    try:
        r = requests.get(f"{TG_BASE}/getUpdates", params={"offset": offset, "timeout": 5}, timeout=10).json()
    except Exception as e:
        print("TG getUpdates err:", e, flush=True)
        return products, history

    new_offset = offset
    for upd in r.get("result", []):
        new_offset = max(new_offset, upd["update_id"] + 1)
        msg = upd.get("message", {})
        if str(msg.get("chat", {}).get("id")) != CHAT_ID:
            continue
        text = msg.get("text", "").strip()
        if not text:
            continue

        if text.lower() == "/list":
            if not products:
                send_tg("📋 *Your tracking list is empty.* Send any product link to add it!")
            else:
                lines = [f"{i+1}. *{p['name']}*\n   {p['url']}" for i, p in enumerate(products)]
                send_tg("📋 *Currently Tracking Products:*\n\n" + "\n".join(lines))

        elif text.lower().startswith("/remove"):
            parts = text.split()
            if len(parts) == 2 and parts.isdigit():
                idx = int(parts) - 1
                if 0 <= idx < len(products):
                    removed = products.pop(idx)
                    history.pop(removed["url"], None)
                    send_tg(f"🗑️ *Removed:* {removed['name']}")
                else:
                    send_tg("⚠️ Invalid number. Send `/list` to see product numbers.")

        else:
            urls = re.findall(r"https?://\S+", text)
            if urls:
                raw_url = urls
                cleaned = clean_url(raw_url)
                custom_name = text.replace(raw_url, "").strip()

                if any(p["url"] == cleaned for p in products):
                    send_tg(f"ℹ️ *Already in your list:*\n{cleaned}")
                else:
                    auto_title, _ = get_page_info(cleaned)
                    final_name = custom_name if custom_name else auto_title
                    products.append({"name": final_name, "url": cleaned})
                    send_tg(
                        f"✅ *Product Confirmed & Added!*\n\n"
                        f"• *Name:* {final_name}\n"
                        f"• *Clean URL:* {cleaned}\n\n"
                        f"⏳ Checking live price & offers right now..."
                    )

    if new_offset != offset:
        open(offset_file, "w").write(str(new_offset))
    return products, history

def main():
    products = json.load(open("products.json")) if os.path.exists("products.json") else []
    history = json.load(open("price_history.json")) if os.path.exists("price_history.json") else {}

    products, history = process_telegram_commands(products, history)
    json.dump(products, open("products.json", "w"), indent=2)

    for item in products:
        try:
            _, text = get_page_info(item["url"])
            print(item["name"], "len:", len(text), flush=True)
            if len(text) < 800:
                continue

            prompt = f"""
            Analyze this Indian e-commerce product page text for "{item['name']}".

            STRICT EXCLUSION RULE:
            - Completely IGNORE and EXCLUDE any offers, discounts, cashback, or No Cost EMI plans that are ONLY for "Amazon Pay ICICI Credit Card" or "Bajaj Finserv / Bajaj Finance EMI Card".
            - Only consider standard bank Credit/Debit cards (like HDFC, SBI, ICICI regular cards, Axis, Kotak, IDFC, BOB, OneCard, etc.).

            Extract these exact fields:
            1. "base": Current selling price in INR as an integer (ignore MRP/strikethrough).
            2. "full_discount": Best instant bank/card discount amount in INR on FULL PAYMENT (one-time payment, excluding Amazon Pay ICICI & Bajaj Finance). Put 0 if none.
            3. "net": Final net price for full payment (base - full_discount).
            4. "full_offer_text": Short summary of eligible banks/cards for full payment discount (excluding Amazon Pay ICICI & Bajaj Finance, or "None").
            5. "nc_emi_months": Maximum No Cost EMI tenure in months available on eligible bank cards (e.g., "6 Months", "9 Months", "12 Months", or "None").
            6. "nc_emi_interest_discount": Maximum interest discount amount in INR (as a number, e.g., 1957) available on eligible bank cards for No Cost EMI (excluding Amazon Pay ICICI & Bajaj Finance). Put 0 if none.
            7. "nc_emi_banks": Short list of eligible banks for this No Cost EMI offer (excluding Amazon Pay ICICI & Bajaj Finance, or "None").

            Return ONLY valid JSON with these exact keys:
            {{"base": number, "full_discount": number, "net": number, "full_offer_text": "string", "nc_emi_months": "string", "nc_emi_interest_discount": number, "nc_emi_banks": "string"}}

            Page Text:
            {text[:22000]}
            """

            data = ask_ai(prompt)
            if not data or not data.get("base"):
                continue

            base = int(round(data.get("base", 0)))
            full_disc = int(round(data.get("full_discount", 0)))
            net = int(round(data.get("net", base - full_disc)))
            emi_disc = int(round(data.get("nc_emi_interest_discount", 0)))
            emi_net = base - emi_disc

            data["base"] = base
            data["full_discount"] = full_disc
            data["net"] = net
            data["nc_emi_interest_discount"] = emi_disc
            data["emi_net"] = emi_net

            print(item["name"], "->", data, flush=True)
            prev = history.get(item["url"])

            is_new = prev is None
            changed = False
            if prev:
                if (
                    net < prev.get("net", net)
                    or base < prev.get("base", base)
                    or emi_net < prev.get("emi_net", emi_net)
                    or data.get("full_offer_text") != prev.get("full_offer_text", "")
                    or data.get("nc_emi_months") != prev.get("nc_emi_months", "")
                ):
                    changed = True

            if is_new or changed:
                header = "🆕 *INITIAL PRICE & OFFERS*" if is_new else "🚨 *PRICE / OFFER UPDATE*"
                msg = (
                    f"{header}\n\n"
                    f"*{item['name']}*\n"
                    f"• *Base Price:* ₹{base:,}\n\n"
                    f"💳 *Full Payment Offer:*\n"
                    f"• *Discount:* ₹{full_disc:,} ({data.get('full_offer_text', 'None')})\n"
                    f"• *Final Net Price:* *₹{net:,}*\n\n"
                    f"📅 *No Cost EMI (Excl. Amazon ICICI & Bajaj):*\n"
                    f"• *Max No Cost Tenure:* {data.get('nc_emi_months', 'None')}\n"
                    f"• *Interest Discount:* ₹{emi_disc:,} ({data.get('nc_emi_banks', 'None')})\n"
                    f"• *Net Cost (Base - Interest):* *₹{emi_net:,}*\n\n"
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
