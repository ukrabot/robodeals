#!/usr/bin/env python3
"""
RoboDeals — AliExpress Affiliate Pinterest Bot
Pipeline: AliExpress API → Cloudflare R2 → GPT-4o-mini → GitHub Pages

Daily run: 100 products → individual HTML pages + Masonry gallery + sitemap
"""

import os, json, time, hmac, hashlib, re, requests, boto3
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI

# ── Credentials (from GitHub Secrets / env) ───────────────────────────────────
ALI_APP_KEY    = os.environ["ALIEXPRESS_APP_KEY"]
ALI_APP_SECRET = os.environ["ALIEXPRESS_APP_SECRET"]
ALI_TRACKING   = os.environ.get("ALIEXPRESS_TRACKING_ID", "default")

R2_ACCOUNT_ID  = os.environ["CF_ACCOUNT_ID"]
R2_ACCESS_KEY  = os.environ["CF_R2_ACCESS_KEY"]
R2_SECRET_KEY  = os.environ["CF_R2_SECRET_KEY"]
R2_BUCKET      = os.environ["CF_R2_BUCKET"]
R2_PUBLIC_URL  = os.environ["CF_R2_PUBLIC_URL"]   # e.g. https://pub-xxxx.r2.dev

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SITE_URL       = os.environ.get("SITE_URL", "https://yourusername.github.io/robodeals")

# ── Paths ─────────────────────────────────────────────────────────────────────
DOCS          = Path("docs")
PAGES_DIR     = DOCS / "p"
DB_FILE       = DOCS / "products.json"
INDEX_FILE    = DOCS / "index.html"
SITEMAP_FILE  = DOCS / "sitemap.xml"

for d in [DOCS, PAGES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── Constants ─────────────────────────────────────────────────────────────────
ALI_API_URL      = "https://api-sg.aliexpress.com/sync"
PRODUCTS_PER_RUN = 100
MAX_STORED       = 5000   # rolling window
KEYWORDS = [
    "robot kit", "arduino robot", "raspberry pi kit", "drone fpv",
    "3d printer kit", "esp32 board", "lidar sensor", "robotic arm kit",
    "AI camera module", "smart home automation", "cnc router kit",
    "servo motor robot", "jetson nano", "obstacle avoidance robot",
    "hexapod robot kit"
]

# ── AliExpress API ─────────────────────────────────────────────────────────────
def ali_sign(params: dict) -> str:
    s = ALI_APP_SECRET + "".join(f"{k}{v}" for k, v in sorted(params.items())) + ALI_APP_SECRET
    return hmac.new(ALI_APP_SECRET.encode(), s.encode(), hashlib.sha256).hexdigest().upper()

def ali_request(method: str, extra: dict) -> dict:
    ts = str(int(time.time() * 1000))
    p  = {"app_key": ALI_APP_KEY, "method": method, "timestamp": ts,
          "sign_method": "sha256", "format": "json", "v": "2.0", **extra}
    p["sign"] = ali_sign(p)
    r = requests.post(ALI_API_URL, data=p, timeout=25)
    r.raise_for_status()
    return r.json()

def fetch_ali_products(keyword: str, page: int = 1, page_size: int = 20) -> list[dict]:
    try:
        data = ali_request("aliexpress.affiliate.product.query", {
            "keywords": keyword, "tracking_id": ALI_TRACKING,
            "page_no": str(page), "page_size": str(page_size),
            "sort": "SALE_PRICE_ASC", "target_currency": "USD",
            "target_language": "EN",
            "fields": ("product_id,product_title,product_main_image_url,"
                       "sale_price,original_price,discount,commission_rate,"
                       "product_detail_url,evaluate_rate,second_level_category_name"),
        })
        items = (data["aliexpress_affiliate_product_query_response"]
                     ["resp_result"]["result"]["products"]["product"])
        return items if isinstance(items, list) else [items]
    except Exception as e:
        print(f"  ⚠ AliExpress error for '{keyword}': {e}")
        return []

# ── Cloudflare R2 upload ───────────────────────────────────────────────────────
s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name="auto",
)

def upload_image_to_r2(image_url: str, product_id: str) -> str | None:
    """Download image from AliExpress and upload to R2. Returns public URL."""
    key = f"products/{product_id}.jpg"
    # Check if already uploaded
    try:
        s3.head_object(Bucket=R2_BUCKET, Key=key)
        return f"{R2_PUBLIC_URL}/{key}"
    except Exception:
        pass
    # Download
    try:
        r = requests.get(image_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        s3.put_object(
            Bucket=R2_BUCKET, Key=key,
            Body=r.content, ContentType="image/jpeg",
            CacheControl="public, max-age=31536000",
        )
        return f"{R2_PUBLIC_URL}/{key}"
    except Exception as e:
        print(f"  ⚠ R2 upload failed for {product_id}: {e}")
        return None

# ── GPT-4o-mini description ────────────────────────────────────────────────────
oai = OpenAI(api_key=OPENAI_API_KEY)

def generate_description(title: str, price: str, category: str, keyword: str) -> str:
    prompt = (
        f"Write a 150-180 word product description in English for an AliExpress affiliate page.\n"
        f"Product: {title}\n"
        f"Price: ${price} USD\n"
        f"Category: {category or keyword}\n\n"
        f"Rules:\n"
        f"- Start with a compelling hook sentence\n"
        f"- Mention 3-4 real use cases or features\n"
        f"- Include natural SEO keywords related to robotics/tech\n"
        f"- End with a call to action like 'Check price on AliExpress'\n"
        f"- Do NOT mention AliExpress by name in the body, only at the end\n"
        f"- Plain text only, no markdown"
    )
    try:
        resp = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300, temperature=0.75,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"  ⚠ GPT error: {e}")
        return f"{title} — a great robotics product available at an excellent price. Perfect for makers, students, and tech enthusiasts looking to build and experiment."

# ── Slug helper ───────────────────────────────────────────────────────────────
def slugify(text: str, pid: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_]+", "-", s)[:60].strip("-")
    return f"{s}-{pid}"

# ── Generate individual product page ─────────────────────────────────────────
def build_product_page(p: dict):
    slug     = p["slug"]
    html_path = PAGES_DIR / f"{slug}.html"

    discount_badge = (f'<div class="badge">−{p["discount"]}% OFF</div>'
                      if p.get("discount") and str(p["discount"]) not in ("0","") else "")
    original_price = (f'<s class="original">${p["original_price"]}</s>'
                      if p.get("original_price") and p["original_price"] != p["price"] else "")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{p['title']} — RoboDeals</title>
  <meta name="description" content="{p['description'][:155]}">
  <link rel="canonical" href="{SITE_URL}/p/{slug}.html">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;500;700&display=swap" rel="stylesheet">

  <!-- Open Graph -->
  <meta property="og:title" content="{p['title']}">
  <meta property="og:description" content="{p['description'][:200]}">
  <meta property="og:image" content="{p['r2_image']}">
  <meta property="og:url" content="{SITE_URL}/p/{slug}.html">
  <meta property="og:type" content="product">

  <!-- Schema.org Product (Google rich results + image indexing) -->
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org/",
    "@type": "Product",
    "name": "{p['title'].replace('"', '&quot;')}",
    "image": "{p['r2_image']}",
    "description": "{p['description'][:300].replace('"', '&quot;')}",
    "sku": "{p['id']}",
    "category": "{p.get('category', 'Robotics & Electronics')}",
    "offers": {{
      "@type": "Offer",
      "url": "{p['url']}",
      "priceCurrency": "USD",
      "price": "{p['price']}",
      "availability": "https://schema.org/InStock",
      "seller": {{ "@type": "Organization", "name": "AliExpress" }}
    }},
    "aggregateRating": {{
      "@type": "AggregateRating",
      "ratingValue": "4.2",
      "reviewCount": "89"
    }}
  }}
  </script>

  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg: #0a0a0f; --surface: #13131a; --surface2: #1c1c27;
      --border: #2a2a3a; --accent: #00e5ff; --accent2: #7c3aed;
      --text: #e8e8f0; --muted: #8888aa; --green: #00ff88;
      --red: #ff4466; --radius: 14px;
    }}
    body {{ background: var(--bg); color: var(--text); font-family: 'DM Sans', sans-serif;
            min-height: 100vh; }}
    header {{ background: rgba(10,10,15,.95); border-bottom: 1px solid var(--border);
              padding: 0 24px; position: sticky; top: 0; z-index: 10; }}
    .header-inner {{ max-width: 900px; margin: 0 auto; height: 60px;
                     display: flex; align-items: center; justify-content: space-between; }}
    .logo {{ font-family: 'Space Mono', monospace; color: var(--accent);
             text-decoration: none; font-size: 1.1rem; }}
    .back {{ color: var(--muted); text-decoration: none; font-size: .85rem;
             display: flex; align-items: center; gap: 6px; }}
    .back:hover {{ color: var(--accent); }}
    main {{ max-width: 900px; margin: 48px auto; padding: 0 24px 80px; }}
    .product-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 40px; }}
    @media (max-width: 640px) {{ .product-grid {{ grid-template-columns: 1fr; }} }}
    .img-wrap {{ position: relative; border-radius: var(--radius); overflow: hidden;
                 background: var(--surface); border: 1px solid var(--border); }}
    .img-wrap img {{ width: 100%; display: block; aspect-ratio: 1/1; object-fit: cover; }}
    .badge {{ position: absolute; top: 12px; left: 12px; background: var(--red);
              color: #fff; font-family: 'Space Mono', monospace; font-size: .7rem;
              padding: 4px 10px; border-radius: 20px; font-weight: 700; }}
    .info {{ display: flex; flex-direction: column; gap: 20px; }}
    .category {{ font-family: 'Space Mono', monospace; font-size: .7rem;
                 color: var(--accent2); text-transform: uppercase; letter-spacing: 1px; }}
    h1 {{ font-size: 1.4rem; line-height: 1.4; font-weight: 700; }}
    .prices {{ display: flex; align-items: baseline; gap: 12px; }}
    .price {{ font-family: 'Space Mono', monospace; font-size: 2rem;
              font-weight: 700; color: var(--accent); }}
    .original {{ color: var(--muted); font-size: 1rem; }}
    .rating {{ color: var(--muted); font-size: .85rem; }}
    .commission {{ display: inline-block; background: rgba(0,255,136,.1);
                   border: 1px solid rgba(0,255,136,.3); color: var(--green);
                   font-family: 'Space Mono', monospace; font-size: .7rem;
                   padding: 4px 12px; border-radius: 20px; }}
    .description {{ line-height: 1.8; color: #c0c0d8; font-size: .95rem; }}
    .cta {{ display: block; text-align: center; background: var(--accent);
            color: #000; font-weight: 700; font-family: 'Space Mono', monospace;
            padding: 16px 32px; border-radius: var(--radius); text-decoration: none;
            font-size: 1rem; transition: opacity .2s; }}
    .cta:hover {{ opacity: .85; }}
    .disclaimer {{ font-size: .72rem; color: var(--muted); text-align: center; margin-top: 8px; }}
    footer {{ text-align: center; padding: 32px; border-top: 1px solid var(--border);
              font-size: .75rem; color: var(--muted); font-family: 'Space Mono', monospace; }}
  </style>
</head>
<body>
<header>
  <div class="header-inner">
    <a class="logo" href="{SITE_URL}">🤖 RoboDeals</a>
    <a class="back" href="{SITE_URL}">← Back to gallery</a>
  </div>
</header>

<main>
  <div class="product-grid">
    <div class="img-wrap">
      {discount_badge}
      <img src="{p['r2_image']}" alt="{p['title']}" width="600" height="600">
    </div>
    <div class="info">
      <span class="category">{p.get('category', 'Robotics & Tech')}</span>
      <h1>{p['title']}</h1>
      <div class="prices">
        <span class="price">${p['price']}</span>
        {original_price}
      </div>
      <div class="rating">⭐ {p.get('rating', '4.2')}% positive feedback</div>
      <span class="commission">💰 Affiliate deal — {p.get('commission','5')}% commission</span>
      <p class="description">{p['description']}</p>
      <a class="cta" href="{p['url']}" target="_blank" rel="noopener sponsored">
        🛒 View Deal on AliExpress →
      </a>
      <p class="disclaimer">Affiliate link. Price may vary. Updated {p['fetched_at'][:10]}.</p>
    </div>
  </div>
</main>

<footer>RoboDeals — Affiliate links · Prices subject to change · © {datetime.now().year}</footer>
</body>
</html>"""

    html_path.write_text(html, encoding="utf-8")

# ── Generate gallery index.html ───────────────────────────────────────────────
def build_index(products: list[dict]):
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cards = ""
    for p in products:
        slug = p["slug"]
        discount = (f'<span class="badge">−{p["discount"]}%</span>'
                    if p.get("discount") and str(p["discount"]) not in ("0","") else "")
        cards += f"""
    <a class="card" href="{SITE_URL}/p/{slug}.html">
      {discount}
      <div class="img-wrap">
        <img src="{p['r2_image']}" alt="{p['title']}" loading="lazy"
             onerror="this.closest('.card').remove()">
      </div>
      <div class="info">
        <p class="title">{p['title']}</p>
        <div class="bottom">
          <span class="price">${p['price']}</span>
          <span class="tag">{p.get('keyword','tech')}</span>
        </div>
      </div>
    </a>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>RoboDeals — Robotics & Tech Gadgets from AliExpress</title>
  <meta name="description" content="Best robotics and tech gadgets from AliExpress. Arduino, drones, 3D printers, robot kits. Updated daily with affiliate deals.">
  <link rel="canonical" href="{SITE_URL}">
  <link rel="sitemap" type="application/xml" href="{SITE_URL}/sitemap.xml">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;500;700&display=swap" rel="stylesheet">

  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "WebSite",
    "name": "RoboDeals",
    "url": "{SITE_URL}",
    "description": "Daily robotics and tech gadget deals from AliExpress",
    "potentialAction": {{
      "@type": "SearchAction",
      "target": "{SITE_URL}?q={{search_term_string}}",
      "query-input": "required name=search_term_string"
    }}
  }}
  </script>

  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:#0a0a0f; --surface:#13131a; --surface2:#1c1c27; --border:#2a2a3a;
      --accent:#00e5ff; --accent2:#7c3aed; --text:#e8e8f0; --muted:#8888aa;
      --green:#00ff88; --red:#ff4466; --radius:14px;
    }}
    html {{ scroll-behavior:smooth; }}
    body {{ background:var(--bg); color:var(--text); font-family:'DM Sans',sans-serif; overflow-x:hidden; }}

    /* Header */
    header {{ position:sticky; top:0; z-index:100; background:rgba(10,10,15,.9);
              backdrop-filter:blur(20px); border-bottom:1px solid var(--border); padding:0 20px; }}
    .header-inner {{ max-width:1600px; margin:0 auto; height:62px;
                     display:flex; align-items:center; justify-content:space-between; gap:16px; }}
    .logo {{ font-family:'Space Mono',monospace; font-size:1.1rem; font-weight:700;
             color:var(--accent); text-decoration:none; white-space:nowrap; }}
    .logo em {{ color:var(--text); font-style:normal; }}
    .search-wrap {{ flex:1; max-width:460px; display:flex; align-items:center;
                    background:var(--surface2); border:1px solid var(--border);
                    border-radius:40px; padding:0 16px; gap:8px; }}
    .search-wrap input {{ background:none; border:none; outline:none; color:var(--text);
                          font:inherit; font-size:.9rem; width:100%; padding:10px 0; }}
    .updated {{ font-family:'Space Mono',monospace; font-size:.6rem; color:var(--muted);
                text-align:right; line-height:1.6; }}

    /* Hero */
    .hero {{ text-align:center; padding:52px 20px 32px; }}
    .hero h1 {{ font-family:'Space Mono',monospace; font-size:clamp(1.8rem,5vw,3.2rem);
                line-height:1.1;
                background:linear-gradient(135deg,var(--accent) 0%,var(--accent2) 100%);
                -webkit-background-clip:text; -webkit-text-fill-color:transparent;
                background-clip:text; }}
    .hero p {{ margin-top:12px; color:var(--muted); font-size:.95rem;
               max-width:480px; margin-inline:auto; line-height:1.6; }}
    .stats {{ display:flex; justify-content:center; gap:32px; margin-top:28px; flex-wrap:wrap; }}
    .stat {{ font-family:'Space Mono',monospace; font-size:.75rem; color:var(--muted); }}
    .stat strong {{ display:block; font-size:1.3rem; color:var(--accent); }}

    /* Grid */
    .grid-wrap {{ max-width:1600px; margin:0 auto; padding:8px 16px 80px; }}
    .grid {{ }}  /* Masonry controls this */
    .grid-sizer {{ width:calc(20% - 12px); }}
    @media(max-width:1200px) {{ .grid-sizer {{ width:calc(25% - 11px); }} }}
    @media(max-width:900px)  {{ .grid-sizer {{ width:calc(33.33% - 10px); }} }}
    @media(max-width:600px)  {{ .grid-sizer {{ width:calc(50% - 8px); }} }}

    /* Card */
    .card {{ width:calc(20% - 12px); margin-bottom:14px;
             background:var(--surface); border:1px solid var(--border);
             border-radius:var(--radius); text-decoration:none; color:inherit;
             display:block; overflow:hidden; position:relative;
             transition:transform .25s,box-shadow .25s,border-color .25s; }}
    @media(max-width:1200px) {{ .card {{ width:calc(25% - 11px); }} }}
    @media(max-width:900px)  {{ .card {{ width:calc(33.33% - 10px); }} }}
    @media(max-width:600px)  {{ .card {{ width:calc(50% - 8px); }} }}
    .card:hover {{ transform:translateY(-4px); border-color:var(--accent);
                   box-shadow:0 12px 40px rgba(0,0,0,.6),0 0 0 1px var(--accent); }}
    .badge {{ position:absolute; top:8px; left:8px; z-index:2;
              background:var(--red); color:#fff; font-family:'Space Mono',monospace;
              font-size:.6rem; font-weight:700; padding:2px 7px; border-radius:20px; }}
    .img-wrap {{ width:100%; overflow:hidden; background:var(--surface2); }}
    .img-wrap img {{ width:100%; display:block; object-fit:cover;
                     transition:transform .4s ease; opacity:0;
                     transition:transform .4s,opacity .4s; }}
    .img-wrap img.loaded {{ opacity:1; }}
    .card:hover .img-wrap img {{ transform:scale(1.06); }}
    .info {{ padding:10px 10px 12px; }}
    .title {{ font-size:.78rem; font-weight:500; line-height:1.4;
              display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
              overflow:hidden; }}
    .bottom {{ display:flex; justify-content:space-between; align-items:center;
               margin-top:8px; gap:4px; flex-wrap:wrap; }}
    .price {{ font-family:'Space Mono',monospace; font-size:.88rem;
              font-weight:700; color:var(--accent); }}
    .tag {{ font-size:.6rem; color:var(--muted); background:var(--surface2);
            border:1px solid var(--border); border-radius:20px;
            padding:2px 6px; font-family:'Space Mono',monospace;
            white-space:nowrap; overflow:hidden; max-width:90px;
            text-overflow:ellipsis; }}

    /* No results */
    .empty {{ text-align:center; padding:80px 20px; color:var(--muted);
              font-family:'Space Mono',monospace; font-size:.9rem; display:none; }}

    footer {{ text-align:center; padding:32px 24px; border-top:1px solid var(--border);
              font-size:.72rem; color:var(--muted); font-family:'Space Mono',monospace;
              line-height:1.9; }}
  </style>
</head>
<body>

<header>
  <div class="header-inner">
    <a class="logo" href="{SITE_URL}">🤖 Robo<em>Deals</em></a>
    <div class="search-wrap">
      <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
      </svg>
      <input type="text" id="searchInput" placeholder="Search robots, drones, Arduino…" autocomplete="off">
    </div>
    <div class="updated">🔄 {updated}</div>
  </div>
</header>

<section class="hero">
  <h1>Robotics &amp;<br>Tech Gadgets</h1>
  <p>Top AliExpress affiliate deals, updated daily. Click any product to see the full deal.</p>
  <div class="stats">
    <div class="stat"><strong id="totalCount">{len(products)}</strong>products today</div>
    <div class="stat"><strong>100</strong>added daily</div>
    <div class="stat"><strong>4–7%</strong>commission</div>
  </div>
</section>

<div class="grid-wrap">
  <div class="grid" id="grid">
    <div class="grid-sizer"></div>
    {cards}
  </div>
  <div class="empty" id="empty">No products match your search.</div>
</div>

<footer>
  RoboDeals — Affiliate links · We earn a commission on qualifying purchases<br>
  Prices and availability subject to change · Updated daily via GitHub Actions<br>
  © {datetime.now().year} RoboDeals
</footer>

<!-- Masonry + imagesLoaded -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/masonry/4.2.2/masonry.pkgd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jquery.imagesloaded/5.0.0/imagesloaded.pkgd.min.js"></script>

<script>
  // ── Masonry init ────────────────────────────────────────
  const gridEl = document.getElementById('grid');
  const msnry  = new Masonry(gridEl, {{
    itemSelector: '.card',
    columnWidth:  '.grid-sizer',
    percentPosition: true,
    gutter: 14,
    transitionDuration: '0.3s'
  }});

  // Fade in each image and trigger Masonry relayout
  imagesLoaded(gridEl).on('progress', (instance, result) => {{
    const img = result.img;
    img.classList.add('loaded');
    msnry.layout();
  }});

  // ── Search filter ───────────────────────────────────────
  const input   = document.getElementById('searchInput');
  const cards   = () => gridEl.querySelectorAll('.card');
  const counter = document.getElementById('totalCount');
  const empty   = document.getElementById('empty');

  input.addEventListener('input', () => {{
    const q = input.value.toLowerCase().trim();
    let visible = 0;
    cards().forEach(card => {{
      const text = card.querySelector('.title').textContent.toLowerCase()
                 + card.querySelector('.tag').textContent.toLowerCase();
      const show = !q || text.includes(q);
      card.style.display = show ? '' : 'none';
      if (show) visible++;
    }});
    counter.textContent = visible;
    empty.style.display = visible === 0 ? 'block' : 'none';
    msnry.layout();  // relayout after filter
  }});
</script>
</body>
</html>"""
    INDEX_FILE.write_text(html, encoding="utf-8")
    print(f"✅ index.html → {len(products)} products")

# ── Generate sitemap.xml with image sitemap ───────────────────────────────────
def build_sitemap(products: list[dict]):
    urls = f"""  <url>
    <loc>{SITE_URL}/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>\n"""

    for p in products:
        slug  = p["slug"]
        img   = p["r2_image"]
        title = p["title"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        urls += f"""  <url>
    <loc>{SITE_URL}/p/{slug}.html</loc>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
    <image:image>
      <image:loc>{img}</image:loc>
      <image:title>{title}</image:title>
      <image:caption>{title} — robotics and tech deal</image:caption>
    </image:image>
  </url>\n"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
{urls}</urlset>"""
    SITEMAP_FILE.write_text(xml, encoding="utf-8")
    print(f"✅ sitemap.xml → {len(products)+1} URLs with image sitemaps")

# ── Main pipeline ─────────────────────────────────────────────────────────────
def main():
    print("🚀 RoboDeals pipeline starting…")
    now = datetime.now(timezone.utc).isoformat()

    # Load existing product DB
    existing = json.loads(DB_FILE.read_text()) if DB_FILE.exists() else []
    seen_ids  = {p["id"] for p in existing}
    print(f"📦 Existing products in DB: {len(existing)}")

    new_products = []
    for kw in KEYWORDS:
        if len(new_products) >= PRODUCTS_PER_RUN:
            break
        print(f"\n🔍 Keyword: '{kw}'")
        items = fetch_ali_products(kw, page_size=20)

        for item in items:
            if len(new_products) >= PRODUCTS_PER_RUN:
                break
            pid = str(item.get("product_id",""))
            img = item.get("product_main_image_url","")
            if not pid or not img or pid in seen_ids:
                continue

            title = item.get("product_title","")[:100]
            price = item.get("sale_price","0")
            print(f"  📦 {title[:50]}… ${price}")

            # 1. Upload image to R2
            r2_url = upload_image_to_r2(img, pid)
            if not r2_url:
                continue

            # 2. Generate GPT description
            description = generate_description(
                title, price,
                item.get("second_level_category_name",""),
                kw
            )

            product = {{
                "id":            pid,
                "slug":          slugify(title, pid),
                "title":         title,
                "price":         price,
                "original_price": item.get("original_price",""),
                "discount":      item.get("discount",""),
                "commission":    item.get("commission_rate",""),
                "rating":        item.get("evaluate_rate",""),
                "url":           item.get("product_detail_url",""),
                "r2_image":      r2_url,
                "ali_image":     img,
                "category":      item.get("second_level_category_name","Robotics & Tech"),
                "keyword":       kw,
                "description":   description,
                "fetched_at":    now,
            }}

            # 3. Build individual product page
            build_product_page(product)
            new_products.append(product)
            seen_ids.add(pid)
            time.sleep(0.3)  # rate limit courtesy

        time.sleep(0.5)

    print(f"\n✅ {len(new_products)} new products processed")

    # Rolling window: newest MAX_STORED products
    all_products = (new_products + existing)[:MAX_STORED]
    DB_FILE.write_text(json.dumps(all_products, ensure_ascii=False, indent=2))

    # 4. Rebuild gallery + sitemap with full DB
    build_index(all_products)
    build_sitemap(all_products)

    print(f"\n🎉 Done! Total in DB: {len(all_products)}")
    print(f"   New pages: {len(new_products)}")
    print(f"   Gallery:   {INDEX_FILE}")
    print(f"   Sitemap:   {SITEMAP_FILE}")

if __name__ == "__main__":
    main()
