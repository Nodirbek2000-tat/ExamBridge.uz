# Stripe — Production (Serverga qo'yish) qo'llanmasi

Bu loyiha lokal test'da to'liq ishlaydi. Serverda HAQIQIY to'lov ishlashi uchun
quyidagi sozlamalarni o'zgartiring. **Kodga tegmaysiz — faqat `.env`.**

---

## 1. Stripe akkountni LIVE rejimga o'tkazing

- Stripe Dashboard → **Activate account** (biznes ma'lumoti + bank hisobi kiritiladi)
- Tasdiqlangach **Live mode** yoqiladi (yuqori chap burchakdagi tugma)

## 2. LIVE API kalitlarni oling

Dashboard → **Developers → API keys** (Live mode'da):

```ini
STRIPE_SECRET_KEY=sk_live_XXXXXXXXXXXX       # sk_test_ EMAS
STRIPE_PUBLISHABLE_KEY=pk_live_XXXXXXXXXXXX   # pk_test_ EMAS
```

## 3. Webhook endpoint qo'shing (CLI emas — Dashboard orqali!)

`stripe listen` faqat lokal test uchun edi. Serverda doimiy webhook kerak:

- Dashboard → **Developers → Webhooks → Add endpoint**
- **Endpoint URL** (backend domeni — nginx /api/ ni Django'ga proxy qiladi):
  ```
  https://nodir.exambridge.uz/api/payments/webhook/
  ```
- **Select events** (faqat shu 4 tasi yetadi):
  - `checkout.session.completed`
  - `invoice.payment_succeeded`
  - `customer.subscription.deleted`
  - `invoice.payment_failed`
- **Add endpoint** → ochilgan sahifada **Signing secret** ko'rinadi (`whsec_...`):
  ```ini
  STRIPE_WEBHOOK_SECRET=whsec_XXXXXXXXXXXX
  ```

## 4. Frontend URL — haqiqiy domen

```ini
FRONTEND_URL=https://exambridge.uz
```

## 5. Narxlar (allaqachon to'g'ri, tekshiring)

```ini
STRIPE_PRICE_1MONTH=799     # $7.99
STRIPE_PRICE_3MONTH=1599    # $15.99
STRIPE_PRICE_6MONTH=2899    # $28.99
```

## 6. HTTPS SHART

Stripe webhook faqat `https://` bilan ishlaydi. SSL sertifikat bo'lishi kerak
(Let's encrypt / Nginx). `http://` da webhook KELMAYDI → premium ochilmaydi.

---

## To'liq production `.env` (Stripe qismi)

```ini
# ── Stripe (PRODUCTION) ──
STRIPE_SECRET_KEY=sk_live_XXXXXXXXXXXX
STRIPE_PUBLISHABLE_KEY=pk_live_XXXXXXXXXXXX
STRIPE_WEBHOOK_SECRET=whsec_XXXXXXXXXXXX
FRONTEND_URL=https://exambridge.uz
STRIPE_PRICE_1MONTH=799
STRIPE_PRICE_3MONTH=1599
STRIPE_PRICE_6MONTH=2899
```

---

## Deploy buyruqlari (server'da)

```bash
# 1. Kodni tortib olish
git pull

# 2. Birinchi marta SSL (faqat bir marta)
bash init-letsencrypt.sh

# 3. Frontend (80/443 va shared network egasi) — AVVAL shu
cd sat_front && docker-compose up -d --build

# 4. Backend
cd ../sat && docker-compose up -d --build
```

Ma'lumotlar (postgres_data, media, SSL) volume'da — `--build` ularni O'CHIRMAYDI.
⚠️ Hech qachon `docker-compose down -v` qilmang (-v volume'larni o'chiradi).

---

## Deploy'dan keyin tekshirish

1. `pip install -r requirements.txt`  (stripe==15.2.0 o'rnatiladi)
2. `python manage.py migrate`  (yangi maydonlar uchun)
3. `python manage.py collectstatic --noinput`
4. Gunicorn/uWSGI + Nginx qayta ishga tushadi
5. Say'tda biror plan tanlab, HAQIQIY karta bilan to'lab ko'ring (oz summali 1 oylik)
6. Stripe Dashboard → Webhooks → endpoint → "Recent deliveries" da `200 OK` ko'rinishi kerak
7. Admin Dashboard → Revenue va Premium statistikasi yangilanadi

---

## MUHIM xavfsizlik eslatmalari

- `.env` ni HECH QACHON git'ga commit qilmang (`.gitignore` da bo'lsin)
- `sk_live_` kalit — maxfiy, hech kimga bermang
- Webhook endpoint (`/api/payments/webhook/`) middleware'dan ozod qilingan
  (`accounts/middleware.py` → `_EXEMPT_PATHS`) — bu to'g'ri, Stripe header yubormaydi
- Live'ga o'tgach test kartalar (`4242...`) ishlamaydi — haqiqiy karta kerak
