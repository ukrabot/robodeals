#!/usr/bin/env python3
"""
RoboDeals — AliExpress Affiliate Pinterest Bot
Images stored in: github.com/ukrabot/robodeals-img (free CDN, no credit card)
"""

import os, json, time, hmac, hashlib, re, base64, requests
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI

# ── Credentials ───────────────────────────────────────────────────────────────
ALI_APP_KEY     = os.environ["ALIEXPRESS_APP_KEY"]
ALI_APP_SECRET  = os.environ["ALIEXPRESS_APP_SECRET"]
ALI_TRACKING    = os.environ.get("ALIEXPRESS_TRACKING_ID", "default")
GITHUB_TOKEN    = os.environ["GH_TOKEN"]
GITHUB_IMG_REPO = "ukrabot/robodeals-img"
IMG_BASE_URL    = f"https://raw.githubusercontent.com/{GITHUB_IMG_REPO}/main/p"
OPENAI_API_KEY  = os.environ["OPENAI_API_KEY"]
SITE_URL        = os.environ.get("SITE_URL", "https://ukrabot.github.io/robodeals")

# ── Paths ─────────────────────────────────────────────────────────────────────
DOCS      = Path("docs")
PAGES_DIR = DOCS / "p"
DB_FILE   = DOCS / "products.json"
INDEX_FILE= DOCS / "index.html"
SITEMAP   = DOCS / "sitemap.xml"
for d in [DOCS, PAGES_DIR]: d.mkdir(parents=True, exist_ok=True)

ALI_API_URL      = "https://api-sg.aliexpress.com/sync"
PRODUCTS_PER_RUN = 100
MAX_STORED       = 5000
KEYWORDS = [
    "robot kit", "arduino robot", "raspberry pi kit", "drone fpv",
    "3d printer kit", "esp32 board", "lidar sensor", "robotic arm kit",
    "AI camera module", "smart home automation", "cnc router kit",
    "servo motor robot", "obstacle avoidance robot",
    "hexapod robot kit", "jetson nano"
]

# ── AliExpress ─────────────────────────────────────────────────────────────────
def ali_sign(params):
    s = ALI_APP_SECRET + "".join(f"{k}{v}" for k,v in sorted(params.items())) + ALI_APP_SECRET
    return hmac.new(ALI_APP_SECRET.encode(), s.encode(), hashlib.sha256).hexdigest().upper()

def ali_request(method, extra):
    ts = str(int(time.time() * 1000))
    p  = {"app_key": ALI_APP_KEY, "method": method, "timestamp": ts,
          "sign_method": "sha256", "format": "json", "v": "2.0", **extra}
    p["sign"] = ali_sign(p)
    r = requests.post(ALI_API_URL, data=p, timeout=25)
    r.raise_for_status()
    return r.json()

def fetch_ali_products(keyword, page_size=20):
    try:
        data = ali_request("aliexpress.affiliate.product.query", {
            "keywords": keyword, "tracking_id": ALI_TRACKING,
            "page_no": "1", "page_size": str(page_size),
            "sort": "SALE_PRICE_ASC", "target_currency": "USD", "target_language": "EN",
            "fields": ("product_id,product_title,product_main_image_url,"
                       "sale_price,original_price,discount,commission_rate,"
                       "product_detail_url,evaluate_rate,second_level_category_name"),
        })
        items = (data["aliexpress_affiliate_product_query_response"]
                     ["resp_result"]["result"]["products"]["product"])
        return items if isinstance(items, list) else [items]
    except Exception as e:
        print(f"  ⚠ AliExpress '{keyword}': {e}")
        return []

# ── GitHub image CDN ──────────────────────────────────────────────────────────
GH_H = {"Authorization": f"token {GITHUB_TOKEN}",
         "Accept": "application/vnd.github.v3+json"}

def upload_image_to_github(image_url, product_id):
    path    = f"p/{product_id}.jpg"
    api_url = f"https://api.github.com/repos/{GITHUB_IMG_REPO}/contents/{path}"
    r = requests.get(api_url, headers=GH_H, timeout=10)
    if r.status_code == 200:
        return f"{IMG_BASE_URL}/{product_id}.jpg"
    try:
        img_r = requests.get(image_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        img_r.raise_for_status()
        b64 = base64.b64encode(img_r.content).decode()
    except Exception as e:
        print(f"  ⚠ Download {product_id}: {e}"); return None
    r = requests.put(api_url, headers=GH_H,
                     json={"message": f"img {product_id}", "content": b64}, timeout=20)
    if r.status_code in (200, 201):
        return f"{IMG_BASE_URL}/{product_id}.jpg"
    print(f"  ⚠ GH upload {product_id}: {r.status_code}")
    return None

# ── GPT-4o-mini ───────────────────────────────────────────────────────────────
oai = OpenAI(api_key=OPENAI_API_KEY)

def generate_description(title, price, category, keyword):
    try:
        resp = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content":
                f"Write a 150-180 word product description in English for an AliExpress affiliate page.\n"
                f"Product: {title}\nPrice: ${price}\nCategory: {category or keyword}\n"
                f"Rules: compelling hook, 3-4 use cases, SEO keywords for robotics/tech, "
                f"end with 'Check the current price on AliExpress'. Plain text only."}],
            max_tokens=300, temperature=0.75)
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"  ⚠ GPT: {e}")
        return f"{title} — excellent robotics product at a great price. Check the current price on AliExpress."

def slugify(text, pid):
    s = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_]+", "-", s)[:60].strip("-") + f"-{pid}"

# ── Product page ──────────────────────────────────────────────────────────────
def build_product_page(p):
    badge = (f'<div class="badge">−{p["discount"]}% OFF</div>'
             if p.get("discount") and str(p["discount"]) not in ("0","") else "")
    orig  = (f'<s class="original">${p["original_price"]}</s>'
             if p.get("original_price") and p["original_price"] != p["price"] else "")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{p['title']} — RoboDeals</title>
  <meta name="description" content="{p['description'][:155]}">
  <link rel="canonical" href="{SITE_URL}/p/{p['slug']}.html">
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;500;700&display=swap" rel="stylesheet">
  <meta property="og:title" content="{p['title']}">
  <meta property="og:image" content="{p['gh_image']}">
  <meta property="og:type" content="product">
  <script type="application/ld+json">
  {{"@context":"https://schema.org/","@type":"Product",
    "name":"{p['title'].replace('"','&quot;')}",
    "image":"{p['gh_image']}",
    "description":"{p['description'][:300].replace('"','&quot;')}",
    "sku":"{p['id']}",
    "offers":{{"@type":"Offer","url":"{p['url']}","priceCurrency":"USD",
               "price":"{p['price']}","availability":"https://schema.org/InStock"}}}}
  </script>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    :root{{--bg:#0a0a0f;--surface:#13131a;--surface2:#1c1c27;--border:#2a2a3a;
          --accent:#00e5ff;--text:#e8e8f0;--muted:#8888aa;--red:#ff4466;--green:#00ff88;--radius:14px}}
    body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;min-height:100vh}}
    header{{background:rgba(10,10,15,.95);border-bottom:1px solid var(--border);padding:0 24px;position:sticky;top:0;z-index:10}}
    .hi{{max-width:900px;margin:0 auto;height:60px;display:flex;align-items:center;justify-content:space-between}}
    .logo{{font-family:'Space Mono',monospace;color:var(--accent);text-decoration:none;font-size:1.1rem}}
    .back{{color:var(--muted);text-decoration:none;font-size:.85rem}} .back:hover{{color:var(--accent)}}
    main{{max-width:900px;margin:48px auto;padding:0 24px 80px}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:40px}}
    @media(max-width:640px){{.grid{{grid-template-columns:1fr}}}}
    .iw{{position:relative;border-radius:var(--radius);overflow:hidden;background:var(--surface);border:1px solid var(--border)}}
    .iw img{{width:100%;display:block;aspect-ratio:1/1;object-fit:cover}}
    .badge{{position:absolute;top:12px;left:12px;background:var(--red);color:#fff;
            font-family:'Space Mono',monospace;font-size:.7rem;padding:4px 10px;border-radius:20px;font-weight:700}}
    .info{{display:flex;flex-direction:column;gap:20px}}
    .cat{{font-family:'Space Mono',monospace;font-size:.7rem;color:#7c3aed;text-transform:uppercase;letter-spacing:1px}}
    h1{{font-size:1.4rem;line-height:1.4;font-weight:700}}
    .prices{{display:flex;align-items:baseline;gap:12px}}
    .price{{font-family:'Space Mono',monospace;font-size:2rem;font-weight:700;color:var(--accent)}}
    .original{{color:var(--muted);font-size:1rem}}
    .comm{{display:inline-block;background:rgba(0,255,136,.1);border:1px solid rgba(0,255,136,.3);
           color:var(--green);font-family:'Space Mono',monospace;font-size:.7rem;padding:4px 12px;border-radius:20px}}
    .desc{{line-height:1.8;color:#c0c0d8;font-size:.95rem}}
    .cta{{display:block;text-align:center;background:var(--accent);color:#000;font-weight:700;
          font-family:'Space Mono',monospace;padding:16px 32px;border-radius:var(--radius);
          text-decoration:none;font-size:1rem;transition:opacity .2s}} .cta:hover{{opacity:.85}}
    .disc{{font-size:.72rem;color:var(--muted);text-align:center;margin-top:8px}}
    footer{{text-align:center;padding:32px;border-top:1px solid var(--border);
            font-size:.75rem;color:var(--muted);font-family:'Space Mono',monospace}}
  </style>
</head>
<body>
<header><div class="hi">
  <a class="logo" href="{SITE_URL}">🤖 RoboDeals</a>
  <a class="back" href="{SITE_URL}">← Back to gallery</a>
</div></header>
<main><div class="grid">
  <div class="iw">{badge}<img src="{p['gh_image']}" alt="{p['title']}" width="600" height="600"></div>
  <div class="info">
    <span class="cat">{p.get('category','Robotics & Tech')}</span>
    <h1>{p['title']}</h1>
    <div class="prices"><span class="price">${p['price']}</span>{orig}</div>
    <span class="comm">💰 Affiliate — {p.get('commission','5')}% commission</span>
    <p class="desc">{p['description']}</p>
    <a class="cta" href="{p['url']}" target="_blank" rel="noopener sponsored">🛒 View Deal on AliExpress →</a>
    <p class="disc">Affiliate link · Price may vary · {p['fetched_at'][:10]}</p>
  </div>
</div></main>
<footer>RoboDeals — Affiliate links · © {datetime.now().year}</footer>
</body></html>"""
    (PAGES_DIR / f"{p['slug']}.html").write_text(html, encoding="utf-8")

# ── Index + Sitemap ───────────────────────────────────────────────────────────
def build_index(products):
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cards = ""
    for p in products:
        d = (f'<span class="badge">−{p["discount"]}%</span>'
             if p.get("discount") and str(p["discount"]) not in ("0","") else "")
        cards += f"""<a class="card" href="{SITE_URL}/p/{p['slug']}.html">{d}
      <div class="iw"><img src="{p['gh_image']}" alt="{p['title']}" loading="lazy" onerror="this.closest('.card').remove()"></div>
      <div class="info"><p class="title">{p['title']}</p>
        <div class="bottom"><span class="price">${p['price']}</span><span class="tag">{p.get('keyword','tech')}</span></div>
      </div></a>\n"""

    INDEX_FILE.write_text(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>RoboDeals — Robotics & Tech Gadgets from AliExpress</title>
  <meta name="description" content="Best robotics and tech gadgets from AliExpress. Arduino, drones, 3D printers, robot kits. Updated daily.">
  <link rel="canonical" href="{SITE_URL}">
  <link rel="sitemap" type="application/xml" href="{SITE_URL}/sitemap.xml">
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;500;700&display=swap" rel="stylesheet">
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    :root{{--bg:#0a0a0f;--surface:#13131a;--surface2:#1c1c27;--border:#2a2a3a;
          --accent:#00e5ff;--accent2:#7c3aed;--text:#e8e8f0;--muted:#8888aa;--red:#ff4466;--radius:14px}}
    body{{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;overflow-x:hidden}}
    header{{position:sticky;top:0;z-index:100;background:rgba(10,10,15,.9);backdrop-filter:blur(20px);
            border-bottom:1px solid var(--border);padding:0 20px}}
    .hi{{max-width:1600px;margin:0 auto;height:62px;display:flex;align-items:center;justify-content:space-between;gap:16px}}
    .logo{{font-family:'Space Mono',monospace;font-size:1.1rem;font-weight:700;color:var(--accent);text-decoration:none}}
    .logo em{{color:var(--text);font-style:normal}}
    .sw{{flex:1;max-width:460px;display:flex;align-items:center;background:var(--surface2);
         border:1px solid var(--border);border-radius:40px;padding:0 16px;gap:8px}}
    .sw input{{background:none;border:none;outline:none;color:var(--text);font:inherit;font-size:.9rem;width:100%;padding:10px 0}}
    .upd{{font-family:'Space Mono',monospace;font-size:.6rem;color:var(--muted);text-align:right;line-height:1.6}}
    .hero{{text-align:center;padding:52px 20px 32px}}
    .hero h1{{font-family:'Space Mono',monospace;font-size:clamp(1.8rem,5vw,3.2rem);line-height:1.1;
              background:linear-gradient(135deg,var(--accent),var(--accent2));
              -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
    .hero p{{margin-top:12px;color:var(--muted);font-size:.95rem;max-width:480px;margin-inline:auto}}
    .stats{{display:flex;justify-content:center;gap:32px;margin-top:28px;flex-wrap:wrap}}
    .stat{{font-family:'Space Mono',monospace;font-size:.75rem;color:var(--muted)}}
    .stat strong{{display:block;font-size:1.3rem;color:var(--accent)}}
    .gw{{max-width:1600px;margin:0 auto;padding:8px 16px 80px}}
    .grid-sizer,.card{{width:calc(20% - 12px)}}
    @media(max-width:1200px){{.grid-sizer,.card{{width:calc(25% - 11px)}}}}
    @media(max-width:900px){{.grid-sizer,.card{{width:calc(33.33% - 10px)}}}}
    @media(max-width:600px){{.grid-sizer,.card{{width:calc(50% - 8px)}}}}
    .card{{margin-bottom:14px;background:var(--surface);border:1px solid var(--border);
           border-radius:var(--radius);text-decoration:none;color:inherit;display:block;
           overflow:hidden;position:relative;transition:transform .25s,box-shadow .25s,border-color .25s}}
    .card:hover{{transform:translateY(-4px);border-color:var(--accent);
                 box-shadow:0 12px 40px rgba(0,0,0,.6),0 0 0 1px var(--accent)}}
    .badge{{position:absolute;top:8px;left:8px;z-index:2;background:var(--red);color:#fff;
            font-family:'Space Mono',monospace;font-size:.6rem;font-weight:700;padding:2px 7px;border-radius:20px}}
    .iw{{width:100%;overflow:hidden;background:var(--surface2)}}
    .iw img{{width:100%;display:block;object-fit:cover;opacity:0;transition:transform .4s,opacity .4s}}
    .iw img.loaded{{opacity:1}} .card:hover .iw img{{transform:scale(1.06)}}
    .info{{padding:10px 10px 12px}}
    .title{{font-size:.78rem;font-weight:500;line-height:1.4;display:-webkit-box;
            -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
    .bottom{{display:flex;justify-content:space-between;align-items:center;margin-top:8px;gap:4px;flex-wrap:wrap}}
    .price{{font-family:'Space Mono',monospace;font-size:.88rem;font-weight:700;color:var(--accent)}}
    .tag{{font-size:.6rem;color:var(--muted);background:var(--surface2);border:1px solid var(--border);
          border-radius:20px;padding:2px 6px;font-family:'Space Mono',monospace;white-space:nowrap;overflow:hidden;max-width:90px;text-overflow:ellipsis}}
    .empty{{text-align:center;padding:80px 20px;color:var(--muted);font-family:'Space Mono',monospace;display:none}}
    footer{{text-align:center;padding:32px 24px;border-top:1px solid var(--border);
            font-size:.72rem;color:var(--muted);font-family:'Space Mono',monospace;line-height:1.9}}
  </style>
</head>
<body>
<header><div class="hi">
  <a class="logo" href="{SITE_URL}">🤖 Robo<em>Deals</em></a>
  <div class="sw">
    <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
    </svg>
    <input type="text" id="si" placeholder="Search robots, drones, Arduino…" autocomplete="off">
  </div>
  <div class="upd">🔄 {updated}</div>
</div></header>
<section class="hero">
  <h1>Robotics &amp;<br>Tech Gadgets</h1>
  <p>Top AliExpress affiliate deals, updated daily.</p>
  <div class="stats">
    <div class="stat"><strong id="tc">{len(products)}</strong>products</div>
    <div class="stat"><strong>100</strong>added daily</div>
    <div class="stat"><strong>4–7%</strong>commission</div>
  </div>
</section>
<div class="gw">
  <div class="grid" id="grid"><div class="grid-sizer"></div>{cards}</div>
  <div class="empty" id="empty">No products found.</div>
</div>
<footer>RoboDeals — Affiliate links · © {datetime.now().year}</footer>
<script src="https://cdnjs.cloudflare.com/ajax/libs/masonry/4.2.2/masonry.pkgd.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jquery.imagesloaded/5.0.0/imagesloaded.pkgd.min.js"></script>
<script>
  const g = document.getElementById('grid');
  const m = new Masonry(g,{{itemSelector:'.card',columnWidth:'.grid-sizer',percentPosition:true,gutter:14,transitionDuration:'0.3s'}});
  imagesLoaded(g).on('progress',(i,r)=>{{r.img.classList.add('loaded');m.layout()}});
  const si=document.getElementById('si'),tc=document.getElementById('tc'),em=document.getElementById('empty');
  si.addEventListener('input',()=>{{
    const q=si.value.toLowerCase().trim();let v=0;
    g.querySelectorAll('.card').forEach(c=>{{
      const t=c.querySelector('.title').textContent.toLowerCase()+c.querySelector('.tag').textContent.toLowerCase();
      const s=!q||t.includes(q);c.style.display=s?'':'none';if(s)v++;
    }});
    tc.textContent=v;em.style.display=v===0?'block':'none';m.layout();
  }});
</script>
</body></html>""", encoding="utf-8")
    print(f"✅ index.html → {len(products)} products")

def build_sitemap(products):
    urls = f'  <url><loc>{SITE_URL}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>\n'
    for p in products:
        t = p['title'].replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
        urls += f'  <url><loc>{SITE_URL}/p/{p["slug"]}.html</loc><changefreq>weekly</changefreq><priority>0.8</priority><image:image><image:loc>{p["gh_image"]}</image:loc><image:title>{t}</image:title></image:image></url>\n'
    SITEMAP.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
{urls}</urlset>""", encoding="utf-8")
    print(f"✅ sitemap.xml → {len(products)+1} URLs")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("🚀 RoboDeals pipeline starting…")
    now      = datetime.now(timezone.utc).isoformat()
    existing = json.loads(DB_FILE.read_text()) if DB_FILE.exists() else []
    seen_ids = {p["id"] for p in existing}
    print(f"📦 Existing: {len(existing)}")

    new_products = []
    for kw in KEYWORDS:
        if len(new_products) >= PRODUCTS_PER_RUN: break
        print(f"\n🔍 '{kw}'")
        for item in fetch_ali_products(kw):
            if len(new_products) >= PRODUCTS_PER_RUN: break
            pid = str(item.get("product_id",""))
            img = item.get("product_main_image_url","")
            if not pid or not img or pid in seen_ids: continue
            title = item.get("product_title","")[:100]
            price = item.get("sale_price","0")
            print(f"  📦 {title[:50]}… ${price}")
            gh_url = upload_image_to_github(img, pid)
            if not gh_url: continue
            desc = generate_description(title, price, item.get("second_level_category_name",""), kw)
            product = {
                "id": pid, "slug": slugify(title, pid), "title": title,
                "price": price, "original_price": item.get("original_price",""),
                "discount": item.get("discount",""), "commission": item.get("commission_rate",""),
                "rating": item.get("evaluate_rate",""), "url": item.get("product_detail_url",""),
                "gh_image": gh_url, "category": item.get("second_level_category_name","Robotics & Tech"),
                "keyword": kw, "description": desc, "fetched_at": now,
            }
            build_product_page(product)
            new_products.append(product)
            seen_ids.add(pid)
            time.sleep(0.3)
        time.sleep(0.5)

    print(f"\n✅ {len(new_products)} new products")
    all_products = (new_products + existing)[:MAX_STORED]
    DB_FILE.write_text(json.dumps(all_products, ensure_ascii=False, indent=2))
    build_index(all_products)
    build_sitemap(all_products)
    print(f"🎉 Done! Total: {len(all_products)}")

if __name__ == "__main__":
    main()
