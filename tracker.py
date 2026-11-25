import json
import os
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from google import genai
from google.genai import errors as genai_errors
import requests

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEFAULT_CHAT_ID = [
    c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()
][0]

client = genai.Client(api_key=GEMINI_API_KEY)
TG_BASE = f"https://api.telegram.org/bot{TG_TOKEN}"


def clean_url(url: str) -> str:
  url = url.strip()
  if not url.startswith("http"):
    url = "https://" + url
  parsed = urlparse(url)

  # Flipkart URLs: keep pid/lid so product & offer pages resolve properly
  if "flipkart.com" in parsed.netloc:
    qs = parse_qs(parsed.query)
    kept = {}
    for k in ("pid", "lid"):
      if k in qs and qs[k]:
        kept[k] = qs[k][0]
    new_query = urlencode(kept)
    return urlunparse((
        parsed.scheme or "https",
        parsed.netloc,
        parsed.path,
        "",
        new_query,
        "",
    ))

  # Amazon URLs: keep clean /dp/ASIN path
  m = re.search(r"/(?:dp|gp/product|aw/d)/([A-Z0-9]{10})", parsed.path)
  if m:
    host = parsed.netloc or "www.amazon.in"
    return f"https://{host}/dp/{m.group(1)}"

  return urlunparse(
      (parsed.scheme or "https", parsed.netloc, parsed.path, "", "", "")
  )


def get_page_info(url: str):
  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          " (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
      ),
      "Accept": (
          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
      ),
      "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
      "Referer": "https://www.google.co.in/",
  }

  html = ""
  for impersonate in ("chrome124", "chrome120", "safari17_0"):
    try:
      resp = cffi_requests.get(
          url,
          headers=headers,
          impersonate=impersonate,
          timeout=25,
          allow_redirects=True,
      )
      if resp.status_code == 200 and len(resp.text) > 1500:
        html = resp.text
        break
    except Exception as e:
      print(f"Fetch error ({impersonate}): {e}", flush=True)

  if not html:
    try:
      resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
      html = resp.text
    except Exception as e:
      print(f"Fallback requests error: {e}", flush=True)
      return "Product", ""

  soup = BeautifulSoup(html, "html.parser")
  for tag in soup(["script", "style", "noscript", "svg"]):
    tag.decompose()

  title = ""
  if soup.title and soup.title.string:
    title = re.sub(r"\s+", " ", soup.title.string).strip()
  for suffix in (" : Amazon.in", " - Buy ", " | Flipkart.com"):
    if suffix in title:
      title = title.split(suffix)[0].strip()

  lines = [
      re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n").splitlines()
  ]
  text = "\n".join(ln for ln in lines if ln)
  return (title[:80] if title else "Product"), text


def ask_ai(prompt: str):
  models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-3.5-flash-lite"]
  for model in models:
    for attempt in range(3):
      try:
        res = client.models.generate_content(
            model=model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        raw = (res.text or "").strip()
        if raw.startswith("```"):
          raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S).strip()
        return json.loads(raw)
      except genai_errors.APIError as e:
        print(f"AI retry: {model} {e.code} {e.message}", flush=True)
        if e.code in (503, 429, 500, 502, 504):
          time.sleep(2 * (attempt + 1))
          continue
        break
      except Exception as e:
        print(f"AI parse error ({model}): {e}", flush=True)
        time.sleep(1.5)
  return None


def parse_product_offers(title: str, page_text: str):
  prompt = f"""
Extract pricing and bank/EMI offers from this product page text.
STRICT RULES:
1. ONLY exclude offers specifically tied to "Amazon Pay ICICI" (or "Amazon ICICI") and "Bajaj Finserv" / "Bajaj Finance".
   DO NOT exclude regular "ICICI Bank Credit Card", "HDFC Credit Card", "SBI", "Axis", or other standard bank credit/debit card offers.
2. Extract numeric amounts (in INR) where possible, plus concise offer notes.
3. For No Cost EMI:
   - Look for tenures like "3m", "6m", "9m", "12m", "3 months", "6 months", "9 months", "12 months", "18 months", "24 months" associated with No Cost EMI. Set "no_cost_tenure" to the longest/max No Cost tenure found (e.g., "12 months"), or "None" if truly absent.
   - Extract interest discount amount (INR) and note on No Cost EMI.
   - Extract any bank/card discount applicable with No Cost EMI (excluding Amazon Pay ICICI & Bajaj). If none, set amount to 0.
4. Return ONLY valid JSON with this exact schema:
{{
  "product_name": "{title}",
  "base_price": 0,
  "coupon_discount": 0,
  "full_payment_bank_discount": 0,
  "full_payment_bank_note": "",
  "no_cost_tenure": "None",
  "no_cost_interest_discount": 0,
  "no_cost_interest_note": "",
  "no_cost_bank_discount": 0,
  "no_cost_bank_note": ""
}}

Page text:
{page_text[:16000]}
"""
  return ask_ai(prompt)


def _fmt_inr(val):
  try:
    n = int(round(float(val)))
    return f"₹{n:,}"
  except Exception:
    return str(val)


def format_offer_message(
    data: dict, url: str, header_tag: str = "🆕 INITIAL PRICE & OFFERS"
):
  name = data.get("product_name") or "Product"
  base = int(round(float(data.get("base_price") or 0)))
  coupon = int(round(float(data.get("coupon_discount") or 0)))

  fp_bank = int(round(float(data.get("full_payment_bank_discount") or 0)))
  fp_note = (data.get("full_payment_bank_note") or "").strip()
  fp_net = max(0, base - coupon - fp_bank)

  tenure = (data.get("no_cost_tenure") or "None").strip()
  nc_int = int(round(float(data.get("no_cost_interest_discount") or 0)))
  nc_int_note = (data.get("no_cost_interest_note") or "").strip()
  nc_bank = int(round(float(data.get("no_cost_bank_discount") or 0)))
  nc_bank_note = (data.get("no_cost_bank_note") or "").strip()

  nc_net = max(0, base - coupon - nc_int - nc_bank)

  fp_bank_str = (
      f"{_fmt_inr(fp_bank)} ({fp_note})" if fp_note else _fmt_inr(fp_bank)
  )
  nc_int_str = (
      f"{_fmt_inr(nc_int)} ({nc_int_note})" if nc_int_note else _fmt_inr(nc_int)
  )
  nc_bank_str = (
      f"{_fmt_inr(nc_bank)} ({nc_bank_note})"
      if nc_bank_note
      else _fmt_inr(nc_bank)
  )

  return (
      f"*{header_tag}*\n\n"
      f"*{name}*\n"
      f"• Base Price: {_fmt_inr(base)}\n\n"
      f"💳 *Full Payment Offer:*\n"
      f"• Bank Discount: {fp_bank_str}\n"
      f"• Final Net Price: {_fmt_inr(fp_net)}\n\n"
      f"📅 *No Cost EMI (Excl. Amazon ICICI & Bajaj):*\n"
      f"• Max No Cost Tenure: {tenure}\n"
      f"• Interest Discount: {nc_int_str}\n"
      f"• Bank Discount: {nc_bank_str}\n"
      f"• Net Cost (Base - Coupon - Interest - Bank): {_fmt_inr(nc_net)}\n\n"
      f"[Open Product Link]({url})"
  )


def send_tg(msg: str, chat_id: str = None, reply_to: int = None):
  target_chat = chat_id or DEFAULT_CHAT_ID
  payload = {
      "chat_id": target_chat,
      "text": msg,
      "parse_mode": "Markdown",
      "disable_web_page_preview": True,
  }
  if reply_to:
    payload["reply_to_message_id"] = reply_to
  try:
    r = requests.post(f"{TG_BASE}/sendMessage", json=payload, timeout=15)
    if not r.ok and "parse_mode" in payload:
      payload.pop("parse_mode")
      requests.post(f"{TG_BASE}/sendMessage", json=payload, timeout=15)
  except Exception as e:
    print("Telegram send error:", e, flush=True)
