# sh-checker-api

Shopify card-check HTTP API — VPS-ready deployment.

## Project files

| File | Purpose |
|---|---|
| `server.py` | Flask application |
| `sh_checker.py` | Core Shopify checker logic |
| `requirements.txt` | Python dependencies |
| `deploy.sh` | One-shot VPS deployment script |
| `sh-checker-api.service` | systemd service unit |
| `nginx.conf` | Nginx reverse-proxy config |
| `.env.example` | Environment variable template |
| `Procfile` | Heroku/PaaS compatibility |

---

## Quick Deploy (Ubuntu/Debian VPS)

```bash
# 1. Upload the project to your VPS
scp -r ./sh-checker-api root@YOUR_VPS_IP:/tmp/sh-checker-api

# 2. SSH into the VPS
ssh root@YOUR_VPS_IP

# 3. Run the deploy script from /tmp/sh-checker-api
cd /tmp/sh-checker-api
chmod +x deploy.sh
sudo ./deploy.sh
```

The script will:
- Install Python 3, Nginx, and dependencies
- Create a Python virtualenv at `/opt/sh-checker-api/venv`
- Create a `.env` config from `.env.example`
- Register and start the `sh-checker-api` systemd service
- Configure Nginx as a reverse proxy on port 80

---

## Manual / Step-by-step

### 1. Install system packages
```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip nginx
```

### 2. Copy files and set up virtualenv
```bash
sudo mkdir -p /opt/sh-checker-api
sudo cp -r ./* /opt/sh-checker-api/
cd /opt/sh-checker-api

python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 3. Configure environment
```bash
sudo cp .env.example .env
sudo nano .env   # set PORT, WORKERS, etc.
```

### 4. Set up the systemd service
```bash
sudo cp sh-checker-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sh-checker-api
sudo systemctl start sh-checker-api

# Check status
sudo systemctl status sh-checker-api
sudo journalctl -u sh-checker-api -f
```

### 5. Configure Nginx
```bash
sudo cp nginx.conf /etc/nginx/sites-available/sh-checker-api
sudo ln -s /etc/nginx/sites-available/sh-checker-api /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default   # remove default site
sudo nginx -t
sudo systemctl reload nginx
```

### 6. (Optional) HTTPS with Let's Encrypt
```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain.com
```
Then uncomment the HTTPS block in `nginx.conf`.

---

## API Usage

### Check a card
```
GET /chk?cc=4111111111111111|12|26|123&site=https://example.myshopify.com
GET /chk?cc=...&site=...&proxy=ip:port:user:pass
```

### Health check
```
GET /health
→ 200 OK
```

### Response format (plain text)
```
Cc: 4111111111111111|12|26|123
Response: CARD_DECLINED
Amount: 1.00 USD
Site: https://example.myshopify.com
Proxy: None
Time: 4.5s
```

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5002` | Port gunicorn binds to |
| `WORKERS` | `4` | Number of gunicorn workers |

---

## Service management

```bash
sudo systemctl start sh-checker-api
sudo systemctl stop sh-checker-api
sudo systemctl restart sh-checker-api
sudo systemctl status sh-checker-api
sudo journalctl -u sh-checker-api -f   # live logs
```
