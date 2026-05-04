#!/bin/bash
# ─── Backend SSL Init (Standalone mode only) ──────────────────────────────────
# Run ONLY if backend is deployed on its own separate server.
# If backend is on the same server as sat_front, run sat_front/init-letsencrypt.sh instead.
#
# Usage:
#   chmod +x init-letsencrypt.sh
#   sudo bash init-letsencrypt.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

EMAIL="nodirbekshukurov382@gmail.com"
DOMAIN="nodir.exambridge.uz"

# ── 1. Create dummy cert so nginx can start ───────────────────────────────────
echo "▶ Creating temporary self-signed certificate..."
mkdir -p "./certbot_init/live/$DOMAIN"
openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
  -keyout "./certbot_init/live/$DOMAIN/privkey.pem" \
  -out    "./certbot_init/live/$DOMAIN/fullchain.pem" \
  -subj "/CN=localhost" 2>/dev/null

docker run --rm \
  -v sat_certbot_certs:/etc/letsencrypt \
  -v "$(pwd)/certbot_init:/src" \
  alpine sh -c "cp -r /src/live /etc/letsencrypt/"
rm -rf "./certbot_init"

# ── 2. Build and start nginx ──────────────────────────────────────────────────
echo "▶ Building and starting services..."
docker-compose -f docker-compose.standalone.yml up -d --build nginx
sleep 5

# ── 3. Get real Let's Encrypt cert ────────────────────────────────────────────
echo "▶ Obtaining SSL certificate for $DOMAIN..."
docker-compose -f docker-compose.standalone.yml run --rm certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  --force-renewal \
  -d "$DOMAIN"

# ── 4. Start everything ───────────────────────────────────────────────────────
echo "▶ Reloading nginx and starting all services..."
docker-compose -f docker-compose.standalone.yml exec nginx nginx -s reload
docker-compose -f docker-compose.standalone.yml up -d

echo ""
echo "✅ Done! https://nodir.exambridge.uz is live."
echo "Auto-renewal active — certbot checks every 12 hours."
