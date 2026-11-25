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

  # Flipkart URLs: preserve pid/lid so product & offer pages resolve properly
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
  final_url = url
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
        final_url = str(resp.url)
        break
    except Exception as e:
      print(f"Fetch error ({impersonate}): {e}", flush=True)

  if not html:
    try:
      resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
      html = resp.text
      final_url = str(resp.url)
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
  return (title[:60] if title else "Product"), text


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
        print(
            f"AI retry: {model} {e.code} {e.message}",
            flush=True,
        )
        if e.code in (503, 429, 500, 502, 504):
          time.sleep(2 * (attempt + 1))
          continue
        break
      except Exception as e:
        print(f"AI parse error ({model}): {e}", flush=True)
        time.sleep(1.5)
  return None


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
