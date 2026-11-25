from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def clean_url(url: str) -> str:
  url = url.strip()
  parsed = urlparse(url)

  # Flipkart URLs: keep ?pid=... (crucial for /item/p/product?pid=... links)
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
