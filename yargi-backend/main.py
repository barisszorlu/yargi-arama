from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
import anthropic
import os
import json
from typing import Optional

app = FastAPI(title="Yargı Arama API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── Request modelleri ──────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    court: str = "yargitay"   # yargitay | danistay | emsal
    page: int = 1
    page_size: int = 10

class SummarizeRequest(BaseModel):
    document_id: str
    court: str = "yargitay"
    title: str = ""

# ── Yargıtay API ───────────────────────────────────────────────
async def search_yargitay(query: str, page: int = 1, page_size: int = 10):
    url = "https://karararama.yargitay.gov.tr/YargitayBilgiBankasiIstemciWeb/servlet/YargitayBilgiBankasiIstemciServlet"
    params = {
        "aranan": query,
        "mahkeme": "",
        "daire": "",
        "esasYil": "",
        "esasNo": "",
        "kararYil": "",
        "kararNo": "",
        "baslangicTarihi": "",
        "bitisTarihi": "",
        "siralama": "0",
        "siralamaDirection": "0",
        "pageSize": str(page_size),
        "pageNumber": str(page),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://karararama.yargitay.gov.tr/",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

# ── Danıştay API ───────────────────────────────────────────────
async def search_danistay(query: str, page: int = 1, page_size: int = 10):
    url = "https://karararama.danistay.gov.tr/DanistayBilgiBankasiIstemciWeb/servlet/DanistayBilgiBankasiIstemciServlet"
    params = {
        "aranan": query,
        "daire": "",
        "esasYil": "",
        "esasNo": "",
        "kararYil": "",
        "kararNo": "",
        "baslangicTarihi": "",
        "bitisTarihi": "",
        "siralama": "0",
        "siralamaDirection": "0",
        "pageSize": str(page_size),
        "pageNumber": str(page),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://karararama.danistay.gov.tr/",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

# ── Emsal (UYAP) API ───────────────────────────────────────────
async def search_emsal(query: str, page: int = 1, page_size: int = 10):
    url = "https://emsal.uyap.gov.tr/BilgiBankasiIstemciWeb/servlet/BilgiBankasiIstemciServlet"
    params = {
        "aranan": query,
        "mahkeme": "",
        "daire": "",
        "pageSize": str(page_size),
        "pageNumber": str(page),
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://emsal.uyap.gov.tr/",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

# ── Claude ile özetle ──────────────────────────────────────────
def summarize_with_claude(results_json: dict, query: str, court_label: str) -> list:
    """Ham API sonuçlarını Claude ile işleyip temizlenmiş liste döndür"""
    if not ANTHROPIC_API_KEY:
        # API key yoksa ham veriyi döndür
        return raw_parse(results_json, court_label)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = (
        "Aşağıdaki Türk mahkeme kararı arama sonuçlarını analiz et.\n"
        f"Aranan konu: '{query}'\nMahkeme: {court_label}\n\n"
        "Ham veri:\n" + json.dumps(results_json, ensure_ascii=False)[:4000] + "\n\n"
        "Her karar için şu JSON array formatında döndür (başka hiçbir şey yazma):\n"
        '[{"id":"...","title":"kararın konusu","court":"daire adı","date":"YYYY-MM-DD",'
        '"caseNo":"E. XXXX/XXXX K. XXXX/XXXX","summary":"2-3 cümle özet","relevance":"bu arama ile ilgisi"}]'
    )

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)

def raw_parse(data: dict, court_label: str) -> list:
    """Claude yoksa ham API verisini basitçe parse et"""
    results = []
    items = data.get("data", data.get("kararlar", data.get("results", [])))
    if not isinstance(items, list):
        items = []
    for item in items[:20]:
        results.append({
            "id": str(item.get("id", item.get("kararId", ""))),
            "title": item.get("kararOzeti", item.get("icerik", "")[:100]),
            "court": item.get("birimAdi", court_label),
            "date": item.get("kararTarihi", ""),
            "caseNo": f"E. {item.get('esasNo', '')} K. {item.get('kararNo', '')}",
            "summary": item.get("kararOzeti", item.get("icerik", ""))[:300],
            "relevance": "",
        })
    return results

# ── Endpoints ─────────────────────────────────────────────────
@app.post("/api/search")
async def search(req: SearchRequest):
    court_labels = {
        "yargitay": "Yargıtay",
        "danistay": "Danıştay",
        "emsal": "Emsal (UYAP)",
    }
    court_label = court_labels.get(req.court, "Yargıtay")

    try:
        if req.court == "yargitay":
            raw = await search_yargitay(req.query, req.page, req.page_size)
        elif req.court == "danistay":
            raw = await search_danistay(req.query, req.page, req.page_size)
        elif req.court == "emsal":
            raw = await search_emsal(req.query, req.page, req.page_size)
        else:
            raise HTTPException(status_code=400, detail="Geçersiz mahkeme")

        results = summarize_with_claude(raw, req.query, court_label)
        total = raw.get("totalCount", raw.get("toplamKayit", len(results)))

        return {
            "results": results,
            "totalFound": total,
            "court": court_label,
            "query": req.query,
        }

    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Mahkeme API hatası: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok", "claude": bool(ANTHROPIC_API_KEY)}

# Frontend statik dosyaları sun (opsiyonel)
if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
