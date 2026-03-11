# Yargı Arama — Backend

Berkay için Yargıtay/Danıştay/Emsal karar arama uygulaması.

## Nasıl çalışır

1. Kullanıcı arama yapar
2. Backend resmi Yargıtay/Danıştay API'sine istek atar
3. Claude sonuçları özetler ve Türkçe açıklar
4. Frontend temiz liste olarak gösterir

## Railway'e Deploy (5 dakika)

1. https://railway.app'e git, GitHub ile giriş yap
2. "New Project" → "Deploy from GitHub repo"
3. Bu klasörü GitHub'a push et, repo'yu seç
4. Environment Variables ekle:
   - `ANTHROPIC_API_KEY` = sk-ant-...
5. Deploy'u bekle, URL al
6. Berkay'a URL'yi ver, bitti

## Local Test

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn main:app --reload
# http://localhost:8000 aç
```

## Notlar

- ANTHROPIC_API_KEY olmadan da çalışır, sadece özetleme olmaz
- Günde 5-10 arama için maliyet ihmal edilebilir düzeyde
