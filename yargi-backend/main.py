from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "tr,en;q=0.9",
    "Content-Type": "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://karararama.yargitay.gov.tr",
    "Referer": "https://karararama.yargitay.gov.tr/",
}

# ── Request modelleri ──────────────────────────────────────────
class SearchRequest(BaseModel):
    query: str
    court: str = "yargitay"
    page: int = 1
    page_size: int = 10

# ── Session yönetimi: her istekte taze JSESSIONID al ──────────
async def get_session_cookie(client: httpx.AsyncClient, base_url: str) -> str:
    resp = await client.get(base_url, follow_redirects=True)
    for cookie in client.cookies.jar:
        if cookie.name == "JSESSIONID":
            return cookie.value
    # response cookie header'dan da dene
    set_cookie = resp.headers.get("set-cookie", "")
    if "JSESSIONID=" in set_cookie:
        return set_cookie.split("JSESSIONID=")[1].split(";")[0]
    return ""

# ── Yargıtay ──────────────────────────────────────────────────
async def search_yargitay(query: str, page: int, page_size: int) -> dict:
    base = "https://karararama.yargitay.gov.tr"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        # 1. Session cookie al
        await get_session_cookie(client, base)

        # 2. Arama yap
        payload = {"data": {"aranan": query, "arananKelime": query,
                             "pageSize": page_size, "pageNumber": page}}
        resp = await client.post(f"{base}/aramalist", json=payload, headers=HEADERS)
        resp.raise_for_status()
        return resp.json()

# ── Danıştay ──────────────────────────────────────────────────
async def search_danistay(query: str, page: int, page_size: int) -> dict:
    base = "https://karararama.danistay.gov.tr"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        await get_session_cookie(client, base)
        payload = {"data": {"aranan": query, "arananKelime": query,
                             "pageSize": page_size, "pageNumber": page}}
        resp = await client.post(f"{base}/aramalist", json=payload, headers={
            **HEADERS,
            "Origin": base,
            "Referer": base + "/",
        })
        resp.raise_for_status()
        return resp.json()

# ── Emsal (UYAP) ───────────────────────────────────────────────
async def search_emsal(query: str, page: int, page_size: int) -> dict:
    base = "https://emsal.uyap.gov.tr"
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        await get_session_cookie(client, base)
        payload = {"data": {"aranan": query, "arananKelime": query,
                             "pageSize": page_size, "pageNumber": page}}
        resp = await client.post(f"{base}/aramalist", json=payload, headers={
            **HEADERS,
            "Origin": base,
            "Referer": base + "/",
        })
        resp.raise_for_status()
        return resp.json()

# ── Sonuçları parse et ────────────────────────────────────────
def parse_results(raw: dict, court_label: str) -> list:
    """API'den gelen ham veriyi temizlenmiş listeye çevir"""
    # Yargıtay API yanıt yapısı: {"data": [...]} veya direkt liste
    items = []
    data = raw.get("data", raw)
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("data", data.get("kararlar", data.get("belgeler", [])))
    if not isinstance(items, list):
        items = []

    results = []
    for item in items:
        doc_id = str(item.get("id", item.get("belgeId", item.get("kararId", ""))))
        daire = item.get("birimAdi", item.get("daire", court_label))
        esas = item.get("esasNo", item.get("esas", ""))
        karar = item.get("kararNo", item.get("karar", ""))
        tarih = item.get("kararTarihi", item.get("tarih", ""))
        ozet = item.get("kararOzeti", item.get("ozet", item.get("icerik", "")))
        if isinstance(ozet, str):
            ozet = ozet[:500]

        results.append({
            "id": doc_id,
            "title": ozet[:100] if ozet else f"{daire} Kararı",
            "court": daire,
            "date": tarih,
            "caseNo": f"E. {esas} K. {karar}".strip(". ") if (esas or karar) else "",
            "summary": ozet,
            "relevance": "",
        })
    return results

# ── Claude ile özetle (opsiyonel) ─────────────────────────────
def enrich_with_claude(results: list, query: str) -> list:
    if not ANTHROPIC_API_KEY or not results:
        return results
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = (
            "Aşağıdaki Türk mahkeme kararı listesini analiz et. "
            "Aranan konu: '" + query + "'\n\n"
            "Her karar için 'relevance' alanına bu aramayla ilgisini 1 cümleyle yaz. "
            "Eğer özet eksikse kısa bir başlık öner. "
            "JSON array olarak döndür, her obje şu alanları içersin: id, title, court, date, caseNo, summary, relevance\n\n"
            "Veri:\n" + json.dumps(results[:10], ensure_ascii=False)
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = msg.content[0].text.replace("```json", "").replace("```", "").strip()
        # JSON array bul
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            enriched = json.loads(text[start:end])
            return enriched
    except Exception:
        pass
    return results

# ── Ana endpoint ───────────────────────────────────────────────
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

        results = parse_results(raw, court_label)
        results = enrich_with_claude(results, req.query)

        # Toplam sayı
        data = raw.get("data", raw)
        if isinstance(data, dict):
            total = data.get("total", data.get("toplamKayit", data.get("totalCount", len(results))))
        else:
            total = len(results)

        return {
            "results": results,
            "totalFound": total,
            "court": court_label,
            "query": req.query,
        }

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Mahkeme API hatası: {e.response.status_code} {e.response.text[:200]}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Bağlantı hatası: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok", "claude": bool(ANTHROPIC_API_KEY)}

# Frontend
if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
