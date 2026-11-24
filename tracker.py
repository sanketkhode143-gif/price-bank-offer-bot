import json
import os
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_req
from google import genai
from google.genai import types
import requests

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
ALLOWED_CHATS = [
    c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()
]
DEFAULT_CHAT_ID = ALLOWED_CHATS[0]
TG_BASE = "https://api." + "telegram.org/bot" + TG_TOKEN


def send_tg(msg, chat_id=None, reply_to=None):
  payload = {
      "chat_id": str(chat_id or DEFAULT_CHAT_ID),
      "text": msg,
      "parse_mode": "Markdown",
      "disable_web_page_preview": True,
  }
  if reply_to:
    payload["reply_to_message_id"] = reply_to
  r = requests.post(f"{TG_BASE}/sendMessage", json=payload, timeout=10)
  if not r.ok:
    payload.pop("parse_mode", None)
    requests.post(f"{TG_BASE}/sendMessage", json=payload, timeout=10)


def ask_ai(prompt):
  for m in ["gemini-3.5-flash-lite", "gemini-3.8-flash"]:
    for _ in range(2):
      try:
        res = client.models.generate_content(
            model=m,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        return json.loads(res.text)
      except Exception as e:
        print("AI retry:", m, e, flush=True)
        time.sleep(2)
  return None


def clean_url(url):
  if isinstance(url, list):
    url = next(iter(url), "")
  url = str(url).strip()
  m = re.search(r"amazon\.in.*?/dp/([A-Z0-9]{10})", url)
  if m:
    return "https://www." + "amazon.in/dp/" + m.group(1)
  return next(iter(url.split("?")), url)


def get_page_info(url):
  headers = {
      "Accept-Language": "en-IN,en;q=0.9",
      "Referer": "https://google.com",
  }
  for browser in ["chrome", "chrome124", "safari"]:
    r = cffi_req.get(url, impersonate=browser, headers=headers, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    raw_title = (
        soup.title.string.strip() if soup.title and soup.title.string else "Product"
    )
    clean_t = re.sub(r"^(Amazon\.in\s*:\s*|Buy\s+)", "", raw_title)
    clean_t = next(iter(clean_t.split(":")), clean_t)
    clean_t = next(iter(clean_t.split("|")), clean_t)[:60].strip()
    for tag in soup(["script", "style", "nav", "footer"]):
      tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    if len(text) >= 800:
      return clean_t, text
    time.sleep(1.5)
  return "Product", ""


def process_telegram_commands(products, history):
  offset_file = "tg_offset.txt"
  offset = (
      int(open(offset_file).read().strip()) if os.path.exists(offset_file) else 0
  )
  try:
    r = requests.get(
        f"{TG_BASE}/getUpdates", params={"offset": offset, "timeout": 5}, timeout=10
    ).json()
  except Exception as e:
    print("TG getUpdates err:", e, flush=True)
    return products, history

  new_offset = offset
  for upd in r.get("result", []):
    new_offset = max(new_offset, upd["update_id"] + 1)
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
      urls = re.findall(r"https?://\S+", text)
      if urls:
        raw_url = next(iter(urls))
        cleaned = clean_url(raw_url)
        custom_name = text.replace(raw_url, "").strip()

        if any(p["url"] == cleaned for p in products):
          send_tg(
              f"ℹ️ *Already in your list:*\n{cleaned}",
              chat_id=chat_id,
              reply_to=msg_id,
          )
        else:
          auto_title, _ = get_page_info(cleaned)
          final_name = custom_name if custom_name else auto_title
          products.append({
              "name": final_name,
              "url": cleaned,
              "chat_id": chat_id,
              "reply_to": msg_id,
          })
          send_tg(
              f"✅ *Product Confirmed & Added!*\n\n"
              f"• *Name:* {final_name}\n"
              f"• *Clean URL:* {cleaned}\n\n"
              f"⏳ Checking live price & offers right now...",
              chat_id=chat_id,
              reply_to=msg_id,
          )

  if new_offset != offset:
    open(offset_file, "w").write(str(new_offset))
  return products, history


def main():
  products = (
      json.load(open("products.json")) if os.path.exists("products.json") else []
  )
  history = (
      json.load(open("price_history.json"))
      if os.path.exists("price_history.json")
      else {}
  )

  products, history = process_telegram_commands(products, history)

  for item in products:
    try:
      scraped_title, text = get_page_info(item["url"])
      print(item["name"], "len:", len(text), flush=True)
      if len(text) < 800:
        continue
