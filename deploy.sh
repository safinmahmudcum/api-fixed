#!/usr/bin/env bash
# ============================================================
# deploy.sh — One-shot VPS deployment script for sh-checker-api
# Run as root (or with sudo) on a fresh Ubuntu/Debian VPS:
#   chmod +x deploy.sh && sudo ./deploy.sh
# ============================================================
set -euo pipefail

APP_DIR="/opt/sh-checker-api"
SERVICE_NAME="sh-checker-api"
NGINX_CONF="/etc/nginx/sites-available/${SERVICE_NAME}"
LOG_DIR="/var/log/${SERVICE_NAME}"
PYTHON="python3"

# ─── 1. System packages ───────────────────────────────────────
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx curl

# ─── 2. Copy application files ───────────────────────────────
echo "[2/7] Copying application files to ${APP_DIR}..."
mkdir -p "${APP_DIR}"
cp -r ./* "${APP_DIR}/"
cp -r ./.[^.]* "${APP_DIR}/" 2>/dev/null || true   # copy dotfiles like .env.example

# ─── 3. Create .env if not present ───────────────────────────
if [ ! -f "${APP_DIR}/.env" ]; then
    echo "[3/7] Creating .env from .env.example..."
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    echo "      ⚠  Edit ${APP_DIR}/.env to customise settings."
else
    echo "[3/7] .env already exists — skipping."
fi

# ─── 4. Python virtual environment & dependencies ─────────────
echo "[4/7] Setting up Python virtualenv and installing dependencies..."
$PYTHON -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip -q
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt" -q

# ─── 5. Log directory & permissions ───────────────────────────
echo "[5/7] Creating log directory and setting permissions..."
mkdir -p "${LOG_DIR}"
chown -R www-data:www-data "${APP_DIR}" "${LOG_DIR}"
chmod 750 "${APP_DIR}"
chmod 640 "${APP_DIR}/.env" 2>/dev/null || true

# ─── 6. systemd service ───────────────────────────────────────
echo "[6/7] Installing and enabling systemd service..."
cp "${APP_DIR}/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
echo "      Service status:"
systemctl status "${SERVICE_NAME}" --no-pager -l || true

# ─── 7. Nginx reverse proxy ───────────────────────────────────
echo "[7/7] Configuring nginx..."
cp "${APP_DIR}/nginx.conf" "${NGINX_CONF}"
ln -sf "${NGINX_CONF}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
# Remove default site if it's still enabled
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx

echo ""
echo "✅  Deployment complete!"
echo "    API endpoint : http://$(curl -s ifconfig.me)/chk?cc=...&site=..."
echo "    Health check : http://$(curl -s ifconfig.me)/health"
echo "    Logs         : journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "    To enable HTTPS, run:"
echo "      apt-get install -y certbot python3-certbot-nginx"
echo "      certbot --nginx -d your.domain.com"
