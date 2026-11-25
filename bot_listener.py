import json
import os
import re
import threading
import time
from flask import Flask
import requests

# Tumcha tracker.py madhla logic reuse karnyasathi
from tracker import ask_ai, clean_url, get_page_info, send_tg

app = Flask(__name__)

TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED_CHATS = [
    c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()
]
DEFAULT_CHAT_ID = ALLOWED_CHATS[0]
TG_BASE = "https://api." + "telegram.org/bot" + TG_TOKEN


def load_json(path, default):
  if os.path.exists(path):
    try:
      return json.load(open(path))
    except Exception:
      return default
  return default


def save_json(path, data):
  with open(path, "w") as f:
    json.dump(data, f, indent=2)


def fetch_and_reply_product(item, products, history):
  pname = item.get("name", "Product")
  url = item["url"]
  chat_id = item.get("chat_id", DEFAULT_CHAT_ID)
  reply_to = item.get("reply_to")

  scraped_title, text = get_page_info(url)
  if len(text) < 800:
    send_tg(
        f"⚠️ *Could not read full page details right now* for *{pname}*.\n"
        "Tracker will retry automatically.",
        chat_id=chat_id,
        reply_to=reply_to,
    )
    return

  prompt = (
      f'Analyze this Indian e-commerce product page text for "{pname}".\n\n'
      "STRICT EXCLUSION RULE:\n"
      '- ONLY ignore and exclude offers specifically for "Amazon Pay ICICI'
      ' Credit Card" (or "Amazon ICICI") and "Bajaj Finserv / Bajaj Finance'
      ' EMI Card".\n'
      "- DO NOT exclude standard bank Credit/Debit cards (like HDFC, SBI,"
      " ICICI regular cards, Axis, Kotak, IDFC, BOB, OneCard, etc.).\n\n"
      "Extract these exact fields:\n"
      '1. "product_title": Clean short product title (max 60 chars) from'
      " the page.\n"
      '2. "base": Current selling price in INR as an integer (ignore'
      " MRP/strikethrough).\n"
      '3. "coupon": On-page clickable coupon/voucher discount in INR as an'
      ' integer (e.g., "Apply ₹2,000 coupon", "Save ₹500 with coupon").'
      " Put 0 if none.\n"
      '4. "full_discount": Best instant bank/card discount amount in INR on'
      " FULL PAYMENT (one-time payment, excluding Amazon Pay ICICI &"
      " Bajaj Finance). Put 0 if none.\n"
      '5. "full_offer_text": Short summary of eligible banks/cards for'
      " full payment discount (excluding Amazon Pay ICICI & Bajaj"
      ' Finance, or "None").\n'
      '6. "nc_emi_months": Find ALL No Cost EMI tenures mentioned in the'
      " text (such as 3m/3 months, 6m/6 months, 9m/9 months, 12m/12"
      " months, 18m/18 months, 24m/24 months, 36m/36 months) on eligible"
      " bank cards and return the MAXIMUM / LONGEST tenure (for example,"
      ' if both 6 months and 12 months appear, return "12 Months"; if 36'
      ' months appears, return "36 Months"). Do NOT settle for a smaller'
      " tenure when a larger No Cost EMI tenure is present. If no explicit"
      " tenure is shown in the text, FALLBACK to deriving the tenure from"
      " any mentioned monthly EMI / No Cost EMI monthly amount relative to"
      ' price (e.g. "~12 Months (approx)" or "~6 Months (approx)"). Only'
      ' return "None" if there is truly no EMI/No Cost EMI info at all.\n'
      '7. "nc_emi_interest_discount": Interest waiver / interest discount'
      " amount in INR (as an integer) on No Cost EMI for eligible bank"
      " cards (excluding Amazon Pay ICICI & Bajaj Finance). Put 0 if"
      " none.\n"
      '8. "nc_emi_banks": Short list of eligible banks for this No Cost'
      " EMI interest offer (excluding Amazon Pay ICICI & Bajaj Finance, or"
      ' "None").\n'
      '9. "nc_emi_bank_discount": Bank/card discount amount in INR (as an'
      " integer) shown on a No Cost EMI card option (for example, a bank"
      ' discount/savings like "-₹4,250" under a No Cost EMI card option,'
      " excluding Amazon Pay ICICI & Bajaj Finance). Put 0 if none.\n"
      '10. "nc_emi_bank_note": Short summary of eligible banks/cards for'
      " No Cost EMI bank discount (excluding Amazon Pay ICICI & Bajaj"
      ' Finance, or "None").\n\n'
      "Return ONLY valid JSON with these exact keys:\n"
      '{{"product_title": "string", "base": number, "coupon": number,'
      ' "full_discount": number, "full_offer_text": "string",'
      ' "nc_emi_months": "string", "nc_emi_interest_discount": number,'
      ' "nc_emi_banks": "string", "nc_emi_bank_discount": number,'
      ' "nc_emi_bank_note": "string"}}\n\n'
      f"Page Text:\n{text[:45000]}"
  )

  data = ask_ai(prompt)
  if not data or not data.get("base"):
    return

  if pname in ("Product", "", None) or len(pname) < 4:
    ai_title = (data.get("product_title") or "").strip()
    if ai_title and ai_title.lower() != "product":
      pname = ai_title[:60]
    elif scraped_title and scraped_title.lower() != "product":
      pname = scraped_title[:60]
    item["name"] = pname
    save_json("products.json", products)

  base = int(round(float(data.get("base") or 0)))
  coupon = int(round(float(data.get("coupon") or 0)))
  full_disc = int(round(float(data.get("full_discount") or 0)))
  net = max(0, base - coupon - full_disc)

  emi_disc = int(round(float(data.get("nc_emi_interest_discount") or 0)))
  emi_bank_disc = int(round(float(data.get("nc_emi_bank_discount") or 0)))
  emi_net = max(0, base - coupon - emi_disc - emi_bank_disc)

  data["base"] = base
  data["coupon"] = coupon
  data["full_discount"] = full_disc
  data["net"] = net
  data["nc_emi_interest_discount"] = emi_disc
  data["nc_emi_bank_discount"] = emi_bank_disc
  data["emi_net"] = emi_net

  history[url] = data
  save_json("price_history.json", history)

  coupon_line = f"• *Coupon Discount:* ₹{coupon:,}\n" if coupon > 0 else ""
  full_offer_txt = data.get("full_offer_text") or "None"
  nc_months = data.get("nc_emi_months") or "None"
  nc_banks = data.get("nc_emi_banks") or "None"
  nc_bank_note = data.get("nc_emi_bank_note") or "None"

  msg = (
      f"🆕 *INITIAL PRICE & OFFERS*\n\n"
      f"*{pname}*\n"
      f"• *Base Price:* ₹{base:,}\n"
      f"{coupon_line}\n"
      f"💳 *Full Payment Offer:*\n"
      f"• *Bank Discount:* ₹{full_disc:,} ({full_offer_txt})\n"
      f"• *Final Net Price:* *₹{net:,}*\n\n"
      f"📅 *No Cost EMI (Excl. Amazon ICICI & Bajaj):*\n"
      f"• *Max No Cost Tenure:* {nc_months}\n"
      f"• *Interest Discount:* ₹{emi_disc:,} ({nc_banks})\n"
      f"• *Bank Discount:* ₹{emi_bank_disc:,} ({nc_bank_note})\n"
      f"• *Net Cost (Base - Coupon - Interest - Bank):* *₹{emi_net:,}*\n\n"
      f"[Open Product Link]({url})"
  )
  send_tg(msg, chat_id=chat_id, reply_to=reply_to)


def poll_loop():
  offset = 0
  while True:
    try:
      r = requests.get(
          f"{TG_BASE}/getUpdates",
          params={"offset": offset, "timeout": 25},
          timeout=35,
      ).json()

      products = load_json("products.json", [])
      history = load_json("price_history.json", {})

      for upd in r.get("result", []):
        offset = max(offset, upd["update_id"] + 1)
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat_id = str(msg.get("chat", {}).get("id", ""))
        msg_id = msg.get("message_id")

        if chat_id not in ALLOWED_CHATS:
          continue

        text = (msg.get("text") or "").strip()
        if not text:
          continue

        if text.lower() == "/list":
          if not products:
            send_tg(
                "📋 *Your tracking list is empty.* Send any product link to add"
                " it!",
                chat_id=chat_id,
                reply_to=msg_id,
            )
          else:
            lines = [
                f"{i+1}. *{p['name']}*\n   {p['url']}"
                for i, p in enumerate(products)
            ]
            send_tg(
                "📋 *Currently Tracking Products:*\n\n" + "\n".join(lines),
                chat_id=chat_id,
                reply_to=msg_id,
            )

        elif text.lower().startswith("/remove"):
          parts = text.split()
          if len(parts) == 2 and parts[-1].isdigit():
            idx = int(parts[-1]) - 1
            if 0 <= idx < len(products):
              removed = products.pop(idx)
              history.pop(removed["url"], None)
              save_json("products.json", products)
              save_json("price_history.json", history)
              send_tg(
                  f"🗑️ *Removed:* {removed['name']}",
                  chat_id=chat_id,
                  reply_to=msg_id,
              )
            else:
              send_tg(
                  "⚠️ Invalid number. Send `/list` to see product numbers.",
                  chat_id=chat_id,
                  reply_to=msg_id,
              )

        else:
          urls = re.findall(
              r"(?:https?://)?(?:www\.)?(?:amazon\.in|amzn\.in|amzn\.to|flipkart\.com|dl\.flipkart\.com)\S+",
              text,
          )
          if urls:
            raw_url = next(iter(urls))
            if not raw_url.startswith("http"):
              raw_url = "https://" + raw_url
            cleaned = clean_url(raw_url)
            custom_name = text.replace(next(iter(urls)), "").strip()

            existing = next((p for p in products if p["url"] == cleaned), None)
            if existing:
              send_tg(
                  f"ℹ️ *Already in your list:*\n{cleaned}\n⏳ Fetching latest"
                  " offers...",
                  chat_id=chat_id,
                  reply_to=msg_id,
              )
              fetch_and_reply_product(existing, products, history)
            else:
              auto_title, _ = get_page_info(cleaned)
              final_name = custom_name if custom_name else auto_title
              new_item = {
                  "name": final_name,
                  "url": cleaned,
                  "chat_id": chat_id,
                  "reply_to": msg_id,
              }
              products.append(new_item)
              save_json("products.json", products)

              send_tg(
                  f"✅ *Added & Checking Live Offers...*\n\n"
                  f"• *Name:* {final_name}\n"
                  f"• *URL:* {cleaned}",
                  chat_id=chat_id,
                  reply_to=msg_id,
              )
              fetch_and_reply_product(new_item, products, history)

    except Exception as e:
      print("Listener error:", e, flush=True)
      time.sleep(3)


@app.route("/")
def health():
  return "Bot Listener Active", 200


if __name__ == "__main__":
  t = threading.Thread(target=poll_loop, daemon=True)
  t.start()
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)
