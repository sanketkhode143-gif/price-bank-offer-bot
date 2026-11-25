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
  print("AI JSON:", data, flush=True)
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

  base = int(round(float(data.get("base") or
