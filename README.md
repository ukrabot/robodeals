# 🤖 RoboDeals — AliExpress Affiliate Bot

Galería tipo Pinterest de robótica y tecnología.
Corre automáticamente cada día con GitHub Actions.

```
AliExpress API → Cloudflare R2 → GPT-4o-mini → GitHub Pages
```

---

## 📁 Estructura

```
robodeals/
├── .github/workflows/daily-update.yml   ← GitHub Action automático
├── docs/
│   ├── index.html                        ← Galería Masonry (GitHub Pages)
│   ├── sitemap.xml                       ← Image sitemap para Google
│   ├── robots.txt
│   ├── products.json                     ← Base de datos local
│   └── p/
│       ├── robot-arm-kit-12345.html      ← Página individual por producto
│       └── ...
├── scripts/
│   └── fetch_and_build.py               ← Pipeline principal
├── requirements.txt
└── README.md
```

---

## 🚀 Setup completo

### Paso 1 — Crear repo en GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/TU_USUARIO/robodeals.git
git branch -M main
git push -u origin main
```

### Paso 2 — Activar GitHub Pages

1. **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: `main` / Folder: `/docs`
4. Tu sitio: `https://TU_USUARIO.github.io/robodeals`

### Paso 3 — Crear bucket en Cloudflare R2 (gratis)

1. Entra a https://dash.cloudflare.com
2. Menú izquierdo → **R2 Object Storage**
3. **Create bucket** → nombre: `robodeals` (o el que quieras)
4. En el bucket → **Settings → Public access → Allow access**
   - Copia la URL pública: `https://pub-XXXX.r2.dev`
5. Vuelve al dashboard → **R2 → Manage R2 API Tokens**
6. **Create API Token** con permisos de **Object Read & Write**
   - Anota: Account ID, Access Key ID, Secret Access Key

### Paso 4 — Agregar secrets en GitHub

**Settings → Secrets and variables → Actions → New repository secret**

| Secret | Dónde obtenerlo |
|---|---|
| `ALIEXPRESS_APP_KEY` | portals.aliexpress.com → App Management |
| `ALIEXPRESS_APP_SECRET` | mismo lugar |
| `ALIEXPRESS_TRACKING_ID` | tu tracking ID de afiliado |
| `CF_ACCOUNT_ID` | Cloudflare dashboard → R2 |
| `CF_R2_ACCESS_KEY` | API Token que creaste |
| `CF_R2_SECRET_KEY` | API Token que creaste |
| `CF_R2_BUCKET` | nombre del bucket (ej: `robodeals`) |
| `CF_R2_PUBLIC_URL` | `https://pub-XXXX.r2.dev` |
| `OPENAI_API_KEY` | platform.openai.com → API keys |
| `SITE_URL` | `https://TU_USUARIO.github.io/robodeals` |

### Paso 5 — Editar robots.txt

Abre `docs/robots.txt` y cambia la URL del sitemap con tu usuario real:
```
Sitemap: https://TU_USUARIO.github.io/robodeals/sitemap.xml
```

### Paso 6 — Primera ejecución

1. **Actions → 🤖 RoboDeals Daily Update → Run workflow**
2. Espera ~30-45 minutos (100 productos + GPT + R2)
3. ¡Tu galería está lista!

### Paso 7 — Registrar en Google Search Console

1. https://search.google.com/search-console
2. Agregar propiedad → tu URL de GitHub Pages
3. Verificar con el método HTML file
4. **Sitemaps → Agregar sitemap** → `sitemap.xml`

---

## ⚙️ Cómo funciona el pipeline

```
GitHub Actions (10:00 UTC diario)
         │
         ▼
fetch_and_build.py
         │
         ├─ AliExpress API (15 keywords × 20 productos)
         │   filtra duplicados vs products.json
         │
         ├─ Por cada producto nuevo:
         │   ├─ Descarga imagen de AliExpress
         │   ├─ Sube imagen a Cloudflare R2 (tu CDN)
         │   ├─ GPT-4o-mini genera descripción única 150-180 palabras
         │   └─ Genera /docs/p/slug-producto.html con Schema.org
         │
         ├─ Regenera docs/index.html (galería Masonry)
         ├─ Regenera docs/sitemap.xml (image sitemap)
         └─ git commit & push → GitHub Pages se actualiza
```

---

## 💰 Costos operativos

| Servicio | Costo |
|---|---|
| GitHub Actions | **Gratis** (plan free) |
| GitHub Pages | **Gratis** |
| Cloudflare R2 | **Gratis** hasta 10GB (~65,000 imágenes) |
| GPT-4o-mini | ~$0.03/día (100 descripciones × $0.0003) |
| **Total** | **~$1/mes** |

---

## 🔧 Personalización

**Cambiar keywords** → `scripts/fetch_and_build.py` → variable `KEYWORDS`

**Cambiar horario** → `.github/workflows/daily-update.yml` → línea `cron:`
- `"0 10 * * *"` = 10:00 UTC (07:00 Chile)
- `"0 14 * * *"` = 14:00 UTC (11:00 Chile)

**Cambiar idioma de descripciones** → función `generate_description()` → cambia `"in English"` por `"in Spanish"`

---

## 📈 Proyección de crecimiento

| Mes | Productos indexados | Ingresos estimados |
|---|---|---|
| 1-2 | 500-2,000 | $5-20 USD |
| 3-6 | 5,000-15,000 | $60-200 USD |
| 7-12 | 20,000-36,000 | $300-800 USD |
| Año 2 | 36,000+ | $800-2,000 USD |
