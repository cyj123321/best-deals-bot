"""
Best Deals Bot - 抓取 Amazon 每日优惠并生成 index.html

上游原版用的是 `.DealContent` 选择器，Amazon 早已改版，该选择器现在匹配 0 个元素，
所以原版跑出来是空列表。这里改用当前的 `div.dcl-product` 卡片结构，并保留旧结构兜底。

仅依赖 requests + beautifulsoup4，GitHub Actions 里 `pip install requests beautifulsoup4` 即可。
"""

import html as html_lib
import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

SOURCE_URL = "https://www.amazon.in/gp/goldbox"

# >>> 换成你自己的联盟标签，例如 "yourname-21" <<<
# 保持原样（占位符）时不会拼接 tag 参数，避免生成带别人/无效标签的链接。
AFFILIATE_TAG = "YOUR_TAG_HERE"

MAX_DEALS = 12
TIMEOUT = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")
OFF_RE = re.compile(r"(\d+)\s*%\s*off", re.I)

# Amazon 限流时会回 503/429，失败后按这个间隔退避重试。
RETRY_DELAYS = (5, 15, 40)
RETRY_STATUS = {429, 500, 502, 503, 504}


def _text(node):
    """取节点文本，取不到就返回空串。"""
    return node.get_text(" ", strip=True) if node else ""


def _build_url(asin):
    """用干净的 ASIN 拼链接，不带页面自带的 pd_rd_* 追踪参数。"""
    url = f"https://www.amazon.in/dp/{asin}"
    if AFFILIATE_TAG and AFFILIATE_TAG != "YOUR_TAG_HERE":
        return f"{url}?tag={AFFILIATE_TAG}"
    return url


def _parse_new_cards(cards):
    """解析当前 Amazon 的 dcl-product 卡片结构。"""
    deals, seen = [], set()

    for card in cards:
        anchor = card.select_one("a.dcl-product-link")
        if not anchor:
            continue

        match = ASIN_RE.search(anchor.get("href", ""))
        if not match:
            continue
        asin = match.group(1)
        if asin in seen:
            continue

        title = _text(card.select_one(".dcl-product-label")) or _text(anchor)
        if not title:
            continue
        seen.add(asin)

        # 注意：.a-price 的整段文本会把 a-offscreen 和 aria-hidden 各抄一遍
        # （得到 "₹85.00₹8500"），必须只取 .a-offscreen。
        price = _text(card.select_one(".dcl-price-single .a-offscreen")) or "价格未标注"
        old_price = _text(card.select_one(".dcl-product-old-price .a-offscreen"))

        discount, deal_tag = "", ""
        badge = card.select_one(".dcl-badge")
        if badge:
            bits = [t for t in (_text(b) for b in badge.select("[role=presentation]")) if t]
            if bits:
                discount = bits[0]
                deal_tag = bits[1] if len(bits) > 1 else ""

        image = ""
        img = card.select_one("img")
        if img and img.get("src", "").startswith("http"):
            image = img["src"]

        pct_match = OFF_RE.search(discount)
        deals.append({
            "asin": asin,
            "title": title,
            "url": _build_url(asin),
            "price": price,
            "old_price": old_price,
            "discount": discount,
            "deal_tag": deal_tag,
            "image": image,
            "percent": int(pct_match.group(1)) if pct_match else 0,
        })

    return deals


def _parse_legacy_cards(cards):
    """旧版 Goldbox 结构兜底，万一 Amazon 换回去还能用。"""
    deals = []

    for card in cards:
        title = _text(card.select_one(".DealTitle"))
        anchor = card.select_one("a[href]")
        if not title or not anchor:
            continue

        href = anchor["href"]
        asin_match = ASIN_RE.search(href)
        asin = asin_match.group(1) if asin_match else None
        url = _build_url(asin) if asin else "https://www.amazon.in" + href

        deals.append({
            "asin": asin,
            "title": title,
            "url": url,
            "price": _text(card.select_one(".a-price .a-offscreen")) or "价格未标注",
            "old_price": "",
            "discount": "",
            "deal_tag": "",
            "image": "",
            "percent": 0,
        })

    return deals


def _fetch(url):
    """带退避重试的抓取。Amazon 偶尔会对短时间内的重复请求回 503。"""
    attempts = len(RETRY_DELAYS) + 1
    last_error = None

    for i in range(attempts):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if response.status_code in RETRY_STATUS:
                raise requests.HTTPError(
                    f"HTTP {response.status_code}", response=response
                )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if i < attempts - 1:
                wait = RETRY_DELAYS[i]
                print(f"[重试] {exc} —— {wait} 秒后重试 ({i + 2}/{attempts})")
                time.sleep(wait)

    raise last_error


def get_deals():
    """抓取并返回优惠列表，按折扣力度从高到低排序。"""
    print(f"[抓取] {SOURCE_URL}")
    response = _fetch(SOURCE_URL)
    print(f"[抓取] HTTP {response.status_code}, {len(response.content)} 字节")

    soup = BeautifulSoup(response.content, "html.parser")

    cards = soup.select("div.dcl-product")
    if cards:
        print(f"[解析] 命中 dcl-product 卡片 {len(cards)} 张")
        deals = _parse_new_cards(cards)
    else:
        cards = soup.select(".DealContent")
        print(f"[解析] 新结构为空，回退旧结构，命中 {len(cards)} 张")
        deals = _parse_legacy_cards(cards)

    deals.sort(key=lambda d: d["percent"], reverse=True)
    return deals[:MAX_DEALS]


def generate_html(deals):
    """生成商品卡片页。"""
    e = html_lib.escape
    today = datetime.now().strftime("%d %B %Y")

    cards = []
    for d in deals:
        old = f'<span class="old">原价 {e(d["old_price"])}</span>' if d["old_price"] else ""
        off = f'<span class="off">{e(d["discount"])}</span>' if d["discount"] else ""
        tag = f'<span class="tag">{e(d["deal_tag"])}</span>' if d["deal_tag"] else ""
        pic = (
            f'<img src="{e(d["image"])}" alt="{e(d["title"])}" loading="lazy">'
            if d["image"] else ""
        )

        cards.append(f"""    <article class="card">
      {pic}
      <div class="meta">{off}{tag}</div>
      <h2>{e(d['title'])}</h2>
      <div class="price">{e(d['price'])}{old}</div>
      <a class="buy" href="{e(d['url'])}" target="_blank" rel="noopener">查看优惠 →</a>
    </article>""")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Best Deals - {today}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 32px 20px; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         background: #f6f7f9; color: #1a1a1a; }}
  header {{ max-width: 1080px; margin: 0 auto 28px; }}
  h1 {{ margin: 0 0 6px; font-size: 28px; }}
  header p {{ margin: 0; color: #666; }}
  .grid {{ max-width: 1080px; margin: 0 auto; display: grid; gap: 20px;
           grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }}
  .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px;
           display: flex; flex-direction: column; }}
  .card img {{ width: 100%; height: 160px; object-fit: contain; background: #fff; border-radius: 8px; }}
  .meta {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0 0; min-height: 22px; }}
  .off {{ background: #cc0c39; color: #fff; font-size: 12px; padding: 2px 8px; border-radius: 99px; }}
  .tag {{ background: #fff1f2; color: #cc0c39; font-size: 12px; padding: 2px 8px; border-radius: 99px; }}
  .card h2 {{ font-size: 14px; line-height: 1.45; margin: 10px 0; flex: 1;
              display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
  .price {{ font-size: 20px; font-weight: 700; color: #cc0c39; margin-bottom: 10px; }}
  .old {{ display: block; font-size: 12px; font-weight: 400; color: #999; text-decoration: line-through; }}
  .buy {{ display: block; text-align: center; background: #ff9900; color: #000; text-decoration: none;
          padding: 10px; border-radius: 8px; font-weight: 600; font-size: 14px; }}
  .buy:hover {{ background: #e68a00; }}
  footer {{ max-width: 1080px; margin: 32px auto 0; color: #999; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
  <header>
    <h1>Top Amazon Deals - {today}</h1>
    <p>共 {len(deals)} 条优惠，由 Best Deals Bot 自动抓取</p>
  </header>
  <main class="grid">
{chr(10).join(cards)}
  </main>
  <footer>价格与库存以 Amazon 页面为准 · 由 GitHub Actions 每日自动更新</footer>
</body>
</html>
"""


if __name__ == "__main__":
    deals = get_deals()

    # 抓取失败时不要覆盖已有的 index.html，否则每天定时任务会把博客清空。
    if not deals:
        print("[中止] 没有抓到任何优惠，保留原有 index.html 不覆盖。")
        sys.exit(1)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(generate_html(deals))

    print(f"[完成] 已写入 index.html，共 {len(deals)} 条优惠")
    if AFFILIATE_TAG == "YOUR_TAG_HERE":
        print("[提醒] AFFILIATE_TAG 还是占位符，当前链接未带联盟标签。")
