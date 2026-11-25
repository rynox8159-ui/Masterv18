"""Quotex (market-qx) Playwright observer.

Opens a REAL Chromium/Chrome window with a persistent profile. You log in there yourself
(captcha / 2FA OK). The session is saved, so next time no login is needed. The observer reads
the broker's own WebSocket feed and forwards the pair list (names, payout, open state), live
ticks and 1m history candles to the backend observation API.

Run standalone:
  python playwright_observer.py                      -> uses PLAYWRIGHT_TARGET_URL / default
  python playwright_observer.py https://other-site/  -> any site URL (opens a real window for it)

Modes:
  OBSERVER_MODE=playwright_observer  -> launches its own real browser window (default)
  OBSERVER_MODE=ws_sniffer           -> attaches to YOUR Chrome tab (remote debugging)

Useful env:
  QX_REMOTE_VIEW=1   -> also stream screenshots to the UI "View browser" panel (slow, OFF by default)
  QX_AUTO_LOGIN=1    -> auto-fill QX_EMAIL / QX_PASSWORD (OFF by default, you log in yourself)
  QX_BROWSER_CHANNEL -> chrome (default) | msedge | chromium
"""
import asyncio
import atexit
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
FROZEN = getattr(sys, 'frozen', False)
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else HERE
if FROZEN:
    load_dotenv(APP_DIR / '.env')
    local_app_data = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    DEFAULT_PROFILE = local_app_data / 'MasterV12' / 'ws-sniffer' / 'profile'
    BROWSER_PATH = APP_DIR / 'ms-playwright'
    if BROWSER_PATH.is_dir():
        os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(BROWSER_PATH))
    ENV_HINT = str(APP_DIR / '.env')
else:
    ROOT = HERE.parents[3]
    BACKEND_ROOT = ROOT / 'main_source_code' / 'backend'
    load_dotenv(HERE / '.env')
    load_dotenv(BACKEND_ROOT / '.env')
    sys.path.insert(0, str(BACKEND_ROOT))
    DEFAULT_PROFILE = HERE / '.qx-profile'
    ENV_HINT = str(BACKEND_ROOT / '.env')

import httpx
from playwright.async_api import async_playwright

BACKEND = os.environ.get('OBSERVER_BACKEND_URL', f"http://127.0.0.1:{os.environ.get('BACKEND_PORT', '7007')}").rstrip('/')
# Any site: pass the URL as first CLI argument, or PLAYWRIGHT_TARGET_URL.
_CLI_URL = next((a for a in sys.argv[1:] if a.startswith('http')), '')
TARGET = _CLI_URL or os.environ.get('PLAYWRIGHT_TARGET_URL', 'https://market-qx.info/en/trade')
CDP_URL = os.environ.get('PLAYWRIGHT_CDP_URL', 'http://127.0.0.1:9222')
MODE = os.environ.get('OBSERVER_MODE', 'playwright_observer')
PROFILE = Path(os.environ.get('QX_PROFILE_DIR', DEFAULT_PROFILE))
FOLLOW_LIMIT = int(os.environ.get('QX_FOLLOW_LIMIT', '200'))  # follow every open pair by default
TICK_INTERVAL = float(os.environ.get('QX_TICK_MIN_INTERVAL', '0.5'))
SESSION = f'qx-{uuid.uuid4().hex[:12]}'
NO_DISPLAY = sys.platform.startswith('linux') and not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY')
QX_HEADLESS_MODE = os.environ.get('QX_HEADLESS', 'auto').lower()
# Screenshot streaming to the UI is the main cause of lag + "fake browser" - OFF unless asked for.
REMOTE_VIEW = os.environ.get('QX_REMOTE_VIEW', '0').lower() in {'1', 'true', 'yes'}
# Auto-typing credentials fights with you while you log in yourself - OFF unless asked for.
AUTO_LOGIN = os.environ.get('QX_AUTO_LOGIN', '0').lower() in {'1', 'true', 'yes'}
BROWSER_CHANNEL = os.environ.get('QX_BROWSER_CHANNEL', 'chrome').strip().lower()
VIRTUAL_DISPLAY = None


def _start_virtual_display():
    """On a screen-less Linux server run a real (headed) Chromium inside Xvfb.

    Headless Chromium is flagged by Cloudflare immediately; a headed browser on a
    virtual screen can pass the Turnstile check."""
    global VIRTUAL_DISPLAY
    if not (NO_DISPLAY and QX_HEADLESS_MODE in {'auto', 'xvfb'} and shutil.which('Xvfb')):
        return False
    for num in range(99, 120):
        if Path(f'/tmp/.X11-unix/X{num}').exists() or Path(f'/tmp/.X{num}-lock').exists():
            continue
        proc = subprocess.Popen(['Xvfb', f':{num}', '-screen', '0', '1366x768x24', '-nolisten', 'tcp'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        if proc.poll() is None:
            os.environ['DISPLAY'] = f':{num}'
            VIRTUAL_DISPLAY = proc
            atexit.register(proc.terminate)
            return True
    return False


VIRTUAL = _start_virtual_display()
HEADLESS = not VIRTUAL and (QX_HEADLESS_MODE == 'true' or (QX_HEADLESS_MODE == 'auto' and NO_DISPLAY))
# True only when a human is NOT in front of the browser (server). Then we click Cloudflare for them.
UNATTENDED = HEADLESS or VIRTUAL
# QX_PROXY=http://user:pass@host:port (or socks5://host:port) routes the browser through
# a proxy in a country where Quotex is available (needed on US/EU cloud servers).
QX_PROXY = os.environ.get('QX_PROXY', '').strip()


def _proxy_settings():
    if not QX_PROXY:
        return None
    parsed = urlsplit(QX_PROXY if '://' in QX_PROXY else f'http://{QX_PROXY}')
    proxy = {'server': f'{parsed.scheme}://{parsed.hostname}:{parsed.port}' if parsed.port else f'{parsed.scheme}://{parsed.hostname}'}
    if parsed.username:
        proxy['username'] = unquote(parsed.username)
        proxy['password'] = unquote(parsed.password or '')
    return proxy
CHROME_CANDIDATES = [
    os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
    os.path.expandvars(r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe'),
    os.path.expandvars(r'%LocalAppData%\Google\Chrome\Application\chrome.exe'),
    os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
]
SAFE = re.compile(r'[^A-Za-z0-9 /_.()&+-]')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('qx-observer')
logging.getLogger('httpx').setLevel(logging.WARNING)

INIT_SCRIPT = """
(() => {
  if (window.__qxHooked) return; window.__qxHooked = true; window.__qxSockets = [];
  const Native = window.WebSocket;
  function Hooked(...args) { const ws = new Native(...args); window.__qxSockets.push(ws); return ws; }
  Hooked.prototype = Native.prototype;
  Object.assign(Hooked, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  window.WebSocket = Hooked;
  window.__qxSend = (msg) => window.__qxSockets.filter(s => s.readyState === 1).forEach(s => { try { s.send(msg); } catch (e) {} });
})();
"""


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def clean_name(name: str, symbol: str) -> str:
    name = SAFE.sub('', (name or symbol).replace('\n', ' ')).strip()
    name = re.sub(r'\s+', ' ', name)
    if symbol.lower().endswith('_otc') and 'OTC' not in name.upper():
        name = f'{name} (OTC)'
    return name[:64] or symbol


def backend_api_credential() -> str:
    credential = (os.environ.get('BACKEND_API_KEY') or '').strip()
    if len(credential) < 32:
        raise RuntimeError(f'BACKEND_API_KEY is missing or invalid; configure it in {ENV_HINT}.')
    return credential


class QuotexObserver:
    def __init__(self):
        self.http = httpx.AsyncClient(
            base_url=BACKEND, timeout=10, headers={'X-API-Key': backend_api_credential()},
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20))
        self.cf_clicks = 0
        self.assets = {}          # provider symbol -> {name, payout, open, otc}
        self.last_sent = {}
        self.latest_quote = {}
        self.pending_event = None
        self.page = None
        self.followed = set()
        self.stats = {'ticks': 0, 'candles': 0, 'pairs': 0}
        self.remote_error_at = 0.0
        self.attached_cdp = False
        self.sockets_seen = 0
        self.last_frame_at = 0.0
        self.logged_in_notice = False

    def base(self, dedupe):
        return {'source': 'MARKET_QX_BROWSER_OBSERVATION', 'schema_version': 2, 'session_id': SESSION,
                'dedupe_id': dedupe[:256], 'observationMethod': 'cdp-websocket'}

    async def post(self, path, body):
        try:
            response = await self.http.post(f'/api/v1/observation/{path}', json=body)
            if response.status_code >= 400:
                log.warning('Backend %s -> %s %s', path, response.status_code, response.text[:200])
        except httpx.HTTPError as exc:
            log.warning('Backend unreachable (%s): %s', BACKEND, exc)

    async def remote_frame(self, page):
        """Optional screenshot stream for the UI 'View browser' panel (QX_REMOTE_VIEW=1 only)."""
        if page.is_closed():
            return
        try:
            frame = await page.screenshot(type='jpeg', quality=65)
            viewport = page.viewport_size or {'width': 1366, 'height': 768}
            headers = {
                'Content-Type': 'image/jpeg',
                'X-Page-Url': page.url,
                'X-Page-Title': quote(await page.title(), safe=''),
                'X-Viewport-Width': str(viewport['width']),
                'X-Viewport-Height': str(viewport['height']),
            }
            control_key = os.environ.get('PROVIDER_CONTROL_KEY', '')
            if control_key:
                headers['X-Provider-Control-Key'] = control_key
            response = await self.http.post(
                '/api/v1/observer/remote/frame',
                content=frame,
                headers=headers,
            )
            if response.status_code >= 400:
                if time.time() - self.remote_error_at >= 30:
                    log.warning('Remote browser frame rejected: HTTP %s', response.status_code)
                    self.remote_error_at = time.time()
                return
            self.remote_error_at = 0.0
            for event in response.json().get('events', []):
                await self.apply_remote_input(page, event)
        except (httpx.HTTPError, ValueError) as exc:
            if time.time() - self.remote_error_at >= 30:
                log.warning('Remote browser frame failed: %s', exc)
                self.remote_error_at = time.time()
        except Exception as exc:  # noqa: BLE001
            if time.time() - self.remote_error_at >= 30:
                log.warning('Remote browser capture failed: %s', exc)
                self.remote_error_at = time.time()

    @staticmethod
    async def apply_remote_input(page, event):
        kind = event.get('type')
        if kind in {'click', 'dblclick'}:
            await page.mouse.click(event['x'], event['y'], click_count=2 if kind == 'dblclick' else 1)
        elif kind == 'type':
            await page.keyboard.type(event['text'])
        elif kind == 'key':
            await page.keyboard.press(event['key'])
        elif kind == 'scroll':
            await page.mouse.wheel(0, event.get('dy', 0))
        elif kind == 'reload':
            await page.reload(wait_until='domcontentloaded')

    # ---------- Quotex frame decoding ----------
    def on_frame(self, payload):
        self.last_frame_at = time.time()
        if isinstance(payload, (bytes, bytearray)):
            raw = bytes(payload)
            text = raw[1:].decode('utf-8', 'ignore') if raw[:1] == b'\x04' else raw.decode('utf-8', 'ignore')
            try:
                data = json.loads(text)
            except ValueError:
                return
            asyncio.create_task(self.dispatch(self.pending_event, data))
            self.pending_event = None
            return
        text = str(payload)
        match = re.match(r'^\d+-?(\[.*\])$', text, re.S)
        if not match:
            return
        try:
            message = json.loads(match.group(1))
        except ValueError:
            return
        if not message or not isinstance(message[0], str):
            return
        if len(message) > 1 and isinstance(message[1], dict) and message[1].get('_placeholder'):
            self.pending_event = message[0]
        elif len(message) > 1:
            asyncio.create_task(self.dispatch(message[0], message[1]))

    async def dispatch(self, event, data):
        event = event or ''
        if event.startswith('instruments/list') or self.looks_like_instruments(data):
            await self.handle_instruments(data)
        elif event.startswith('quotes/stream') or self.looks_like_quotes(data):
            await self.handle_quotes(data)
        elif event.startswith('history/list') or (isinstance(data, dict) and 'candles' in data and 'asset' in data):
            await self.handle_history(data)

    @staticmethod
    def looks_like_instruments(data):
        return isinstance(data, list) and len(data) > 5 and all(isinstance(i, list) and len(i) > 14 and isinstance(i[1], str) for i in data[:5])

    @staticmethod
    def looks_like_quotes(data):
        return isinstance(data, list) and data and all(
            isinstance(i, list) and len(i) >= 3 and isinstance(i[0], str) and isinstance(i[1], (int, float)) and i[1] > 1e9 for i in data[:3])

    async def handle_instruments(self, data):
        for item in data:
            try:
                symbol = str(item[1])
                payout = next((float(v) for v in (item[18] if len(item) > 18 else None, item[5]) if isinstance(v, (int, float)) and 10 <= v <= 100), None)
                self.assets[symbol] = {
                    'name': clean_name(str(item[2]), symbol), 'payout': payout,
                    'open': bool(item[14]), 'otc': symbol.lower().endswith('_otc'),
                }
            except (IndexError, TypeError, ValueError):
                continue
        pairs = [{'symbol': a['name'], 'providerSymbol': s, 'timeframe': '1m', 'label': a['name'],
                  'payoutPercent': a['payout'], 'isOpenAtBroker': a['open']} for s, a in self.assets.items()]
        for start in range(0, len(pairs), 100):
            chunk = pairs[start:start + 100]
            await self.post('pairs', {**self.base(f'pairs-{int(time.time())}-{start}'), 'pairs': chunk})
        self.stats['pairs'] = len(pairs)
        log.info('Quotex pair list received: %s pairs (%s open)', len(pairs), sum(a['open'] for a in self.assets.values()))
        await self.follow_assets()

    async def handle_quotes(self, data):
        now = time.time()
        for row in data:
            symbol, ts, price = row[0], float(row[1]), float(row[2])
            asset = self.assets.get(symbol)
            if not asset or price <= 0:
                continue
            # V16.3 smooth feed: keep the LATEST quote per symbol; throttled ones are flushed later, never dropped.
            self.latest_quote[symbol] = (ts, price, now)
            if now - self.last_sent.get(symbol, 0) >= TICK_INTERVAL:
                await self.flush_quote(symbol)
        if not getattr(self, '_flusher', None) or self._flusher.done():
            self._flusher = asyncio.create_task(self.flush_pending())

    async def flush_quote(self, symbol):
        item = self.latest_quote.pop(symbol, None)
        asset = self.assets.get(symbol)
        if not item or not asset:
            return
        ts, price, seen = item
        self.last_sent[symbol] = time.time()
        self.stats['ticks'] += 1
        await self.post('tick', {**self.base(f'{symbol}-{ts}'), 'symbol': asset['name'], 'providerSymbol': symbol,
                                 'price': price, 'timestamp': iso(seen), 'providerTimestamp': iso(ts)})

    async def flush_pending(self):
        await asyncio.sleep(TICK_INTERVAL)
        if self.latest_quote:
            await asyncio.gather(*(self.flush_quote(symbol) for symbol in list(self.latest_quote)), return_exceptions=True)

    async def handle_history(self, data):
        symbol = data.get('asset')
        asset = self.assets.get(symbol)
        if not asset or int(data.get('period') or 60) != 60:
            return
        rows = []
        for candle in (data.get('candles') or [])[-300:-1]:
            try:
                ts, o, c, h, low = float(candle[0]), float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])
            except (IndexError, TypeError, ValueError):
                continue
            if h < max(o, c) or low > min(o, c):
                o, h, low, c = o, c, h, low  # alternate [t, o, h, l, c] ordering
            if h < max(o, c) or low > min(o, c):
                continue
            bucket = int(ts // 60 * 60)
            self.stats['candles'] += 1
            rows.append({**self.base(f'{symbol}-1m-{bucket}'), 'symbol': asset['name'], 'providerSymbol': symbol,
                         'timeframe': '1m', 'timestamp': iso(bucket), 'closeTimestamp': iso(bucket + 60),
                         'open': o, 'high': h, 'low': low, 'close': c})
        # Post history in parallel chunks so the live quote stream is never blocked by a 300-candle backfill.
        for index in range(0, len(rows), 25):
            await asyncio.gather(*(self.post('event', row) for row in rows[index:index + 25]), return_exceptions=True)
        log.info('History backfill %s: %s candles', asset['name'], len(data.get('candles') or []))

    async def follow_assets(self):
        if not self.page:
            return
        wanted = [s for s, a in sorted(self.assets.items(), key=lambda kv: -(kv[1]['payout'] or 0)) if a['open']][:FOLLOW_LIMIT]
        for symbol in wanted:
            if symbol in self.followed:
                continue
            for message in (f'42["instruments/update",{{"asset":"{symbol}","period":60}}]', f'42["depth/follow","{symbol}"]',
                            f'42["history/load",{{"asset":"{symbol}","index":{int(time.time() * 100)},"time":{int(time.time())},"offset":18000,"period":60}}]'):
                try:
                    await self.page.evaluate('m => window.__qxSend && window.__qxSend(m)', message)
                except Exception:  # noqa: BLE001 - page may be navigating
                    pass
            self.followed.add(symbol)
            await asyncio.sleep(0.3)

    # ---------- browser ----------
    async def try_login(self, page):
        """Optional auto-fill. Disabled unless QX_AUTO_LOGIN=1 (you normally log in yourself)."""
        if not AUTO_LOGIN:
            return
        email, password = os.environ.get('QX_EMAIL'), os.environ.get('QX_PASSWORD')
        if not (email and password) or 'sign-in' not in page.url:
            return
        if getattr(self, '_login_attempts', 0) >= 3:
            return
        self._login_attempts = getattr(self, '_login_attempts', 0) + 1

        async def first_visible(selectors):
            for selector in selectors:
                locator = page.locator(f'{selector}:visible').first
                try:
                    await locator.wait_for(state='visible', timeout=4000)
                    return locator
                except Exception:  # noqa: BLE001
                    continue
            return None

        try:
            email_box = await first_visible(['input[name="email"]', 'input[type="email"]', 'input[autocomplete="email"]', 'input[autocomplete="username"]'])
            password_box = await first_visible(['input[name="password"]', 'input[type="password"]'])
            if email_box is None or password_box is None:
                log.warning('Auto-login: login form not found on %s (Cloudflare / region page?)', page.url[:80])
                return
            await email_box.fill(email)
            await password_box.fill(password)
            submit = await first_visible(['button[type="submit"]', 'form button:has-text("Sign in")', 'form button:has-text("Log in")'])
            await (submit.click() if submit else password_box.press('Enter'))
            log.info('Submitted login form for %s (complete any captcha / 2FA in the window).', email[:3] + '***')
            await asyncio.sleep(10)
        except Exception as exc:  # noqa: BLE001
            log.warning('Auto-login skipped: %s', str(exc).splitlines()[0][:160])

    def attach(self, page):
        def hook(ws):
            self.sockets_seen += 1
            ws.on('framereceived', self.on_frame)
        page.on('websocket', hook)

    async def open_context(self, pw):
        if MODE == 'ws_sniffer':
            try:
                browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=4000)
                log.info('Attached to your Chrome at %s (your own browser tab - log in manually there)', CDP_URL)
                self.attached_cdp = True
                return browser.contexts[0] if browser.contexts else await browser.new_context()
            except Exception:  # noqa: BLE001
                chrome = next((path for path in CHROME_CANDIDATES if path and Path(path).is_file()), None)
                if chrome and not HEADLESS:
                    port = CDP_URL.rsplit(':', 1)[-1].strip('/') or '9222'
                    profile = PROFILE.parent / '.qx-chrome-profile'
                    profile.mkdir(parents=True, exist_ok=True)
                    await asyncio.create_subprocess_exec(chrome, f'--remote-debugging-port={port}', f'--user-data-dir={profile}', '--no-first-run', TARGET)
                    log.info('Started Chrome with remote debugging (%s); attaching…', chrome)
                    for _ in range(20):
                        await asyncio.sleep(1)
                        try:
                            browser = await pw.chromium.connect_over_cdp(CDP_URL, timeout=3000)
                            self.attached_cdp = True
                            return browser.contexts[0] if browser.contexts else await browser.new_context()
                        except Exception:  # noqa: BLE001
                            continue
                log.warning('No Chrome with remote debugging found at %s - using the built-in Chromium instead.', CDP_URL)
        PROFILE.mkdir(parents=True, exist_ok=True)
        stealth = ['--disable-blink-features=AutomationControlled']
        if VIRTUAL:
            args = ['--no-sandbox', '--disable-dev-shm-usage', '--window-size=1366,768', '--window-position=0,0', *stealth]
        else:
            args = (['--start-maximized'] if not HEADLESS else ['--no-sandbox', '--disable-dev-shm-usage']) + stealth
        fixed_viewport = HEADLESS or VIRTUAL
        launch = dict(
            user_data_dir=str(PROFILE), headless=HEADLESS,
            viewport={'width': 1366, 'height': 768} if fixed_viewport else None, args=args,
            ignore_default_args=['--enable-automation'], proxy=_proxy_settings(),
            user_agent=None if not HEADLESS else 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36')
        context = None
        # Prefer the user's REAL installed Chrome/Edge (fewer captcha problems, smoother), fall back to bundled Chromium.
        if BROWSER_CHANNEL in {'chrome', 'msedge'} and not UNATTENDED:
            try:
                context = await pw.chromium.launch_persistent_context(channel=BROWSER_CHANNEL, **launch)
                log.info('Opened your installed %s as a real window.', BROWSER_CHANNEL)
            except Exception as exc:  # noqa: BLE001
                log.info('Installed %s not usable (%s) - using bundled Chromium.', BROWSER_CHANNEL, str(exc).splitlines()[0][:100])
        if context is None:
            try:
                context = await pw.chromium.launch_persistent_context(**launch)
            except Exception as exc:  # noqa: BLE001
                if "Executable doesn't exist" in str(exc):
                    raise RuntimeError(
                        f'Playwright Chromium is not installed for this Python. Run: "{sys.executable}" -m playwright install chromium chromium-headless-shell'
                    ) from exc
                raise
        mode = 'visible window on virtual screen (Xvfb)' if VIRTUAL else ('headless - no screen on this machine' if HEADLESS else 'visible window')
        log.info('Browser launched (%s, proxy %s, profile %s)', mode, 'on' if QX_PROXY else 'off', PROFILE)
        return context

    async def pass_cloudflare(self, page):
        """Click the Cloudflare Turnstile "Verify you are human" checkbox like a user would.
        Only used on a screen-less server; on your PC you click it yourself."""
        try:
            frame_el = None
            for frame in page.frames:
                if 'challenges.cloudflare.com' in frame.url:
                    frame_el = await frame.frame_element()
                    break
            box = await frame_el.bounding_box() if frame_el else None
            x, y = (box['x'] + 28, box['y'] + box['height'] / 2) if box else (256, 337)
            await page.mouse.move(x - 120, y - 40)
            await page.mouse.move(x, y, steps=12)
            await page.mouse.click(x, y)
            self.cf_clicks += 1
            log.info('Cloudflare check: clicked "Verify you are human" (attempt %s)', self.cf_clicks)
        except Exception as exc:  # noqa: BLE001
            log.debug('Cloudflare click failed: %s', exc)

    @staticmethod
    async def region_blocked(page):
        try:
            return 'not available in your region' in (await page.inner_text('body', timeout=2000)).lower()
        except Exception:  # noqa: BLE001
            return False

    async def run(self):
        log.info('Observer mode=%s backend=%s target=%s remote_view=%s', MODE, BACKEND, TARGET, REMOTE_VIEW)
        try:
            response = await self.http.get('/api/health')
            log.info('Backend reachable: HTTP %s', response.status_code)
        except httpx.HTTPError as exc:
            log.error('Backend NOT reachable at %s (%s). Set OBSERVER_BACKEND_URL in %s.', BACKEND, exc, ENV_HINT)
        async with async_playwright() as pw:
            context = await self.open_context(pw)
            await context.add_init_script(INIT_SCRIPT)
            context.on('page', self.attach)
            for page in context.pages:
                self.attach(page)
            host = (urlsplit(TARGET).hostname or '').split('.')
            site_key = host[-2] if len(host) >= 2 else (host[0] if host else 'qx')
            qx_page = next((p for p in context.pages if site_key in p.url or 'qx' in p.url or 'quotex' in p.url), None)
            if self.attached_cdp:
                # Your own Chrome: reuse the tab (keeps your manual login) or open one new tab.
                page = qx_page or await context.new_page()
                self.page = page
                if qx_page is None:
                    await page.goto(TARGET, wait_until='domcontentloaded', timeout=60000)
                    log.info('Opened a tab in your Chrome (%s). Log in there manually.', TARGET)
                else:
                    # WebSocket hooks only see sockets opened after attach -> one reload (login cookies are kept).
                    await page.reload(wait_until='domcontentloaded', timeout=60000)
                    log.info('Reusing your tab %s (reloaded once to capture its WebSocket feed).', page.url[:80])
            else:
                page = qx_page or (context.pages[0] if context.pages else await context.new_page())
                self.page = page
                await page.goto(TARGET, wait_until='domcontentloaded', timeout=60000)
                log.info('Site opened at %s. Log in in the browser window if asked; the session is saved in %s.', TARGET, PROFILE)
            try:
                await page.bring_to_front()  # the real window pops up in front of the UI
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(3)
            if not self.attached_cdp:
                await self.try_login(page)
            last_report = 0
            last_follow = 0
            last_cf = 0
            last_reload = time.time()
            while not page.is_closed():
                await asyncio.sleep(1)
                if REMOTE_VIEW:
                    await self.remote_frame(page)
                # Self-heal: tab open but no WebSocket frame for 90s -> reload (socket was opened before attach or died).
                if self.attached_cdp and 'sign-in' not in page.url and time.time() - max(self.last_frame_at, last_reload) > 90 and time.time() - last_reload > 90:
                    last_reload = time.time()
                    try:
                        log.info('No WebSocket frames for 90s - reloading your tab to re-capture the feed.')
                        await page.reload(wait_until='domcontentloaded', timeout=60000)
                    except Exception:  # noqa: BLE001
                        pass
                # Auto Cloudflare click ONLY on an unattended server - on your PC it would steal your mouse.
                if UNATTENDED and not self.assets and self.cf_clicks < 6 and time.time() - last_cf > 8:
                    try:
                        if 'just a moment' in (await page.title()).lower():
                            last_cf = time.time()
                            await asyncio.sleep(2)
                            await self.pass_cloudflare(page)
                    except Exception:  # noqa: BLE001
                        pass
                if self.assets and not self.logged_in_notice:
                    self.logged_in_notice = True
                    log.info('Data is flowing - you can leave the browser window open (minimise it, do not close it).')
                if time.time() - last_report > 30:
                    last_report = time.time()
                    title = ''
                    try:
                        title = await page.title()
                    except Exception:  # noqa: BLE001
                        pass
                    if not self.assets and await self.region_blocked(page):
                        state = ('REGION BLOCKED - site is not available in this server\'s country'
                                 + (' even through QX_PROXY; use a proxy in another country' if QX_PROXY
                                    else '; set QX_PROXY (proxy in an allowed country) in backend/.env or run the observer on your own PC'))
                    elif not self.assets and 'just a moment' in title.lower():
                        state = ('CLOUDFLARE CHECK - this server IP is blocked; run the observer on your own PC' if HEADLESS
                                 else 'CLOUDFLARE CHECK - click "Verify you are human" in the browser window')
                    elif not self.assets and 'sign-in' in page.url:
                        state = 'LOGIN REQUIRED - log in in the browser window (manual login, captcha/2FA OK)'
                        await self.try_login(page)
                    else:
                        state = 'waiting for pair list' if not self.assets else 'streaming'
                    log.info('Status: %s · pairs %s · ticks sent %s · candles %s · sockets %s · page %s', state, self.stats['pairs'], self.stats['ticks'], self.stats['candles'], self.sockets_seen, page.url[:80])
                if self.assets and len(self.followed) < FOLLOW_LIMIT and time.time() - last_follow >= 5:
                    last_follow = time.time()
                    await self.follow_assets()
            log.info('Browser window closed - observer stopped.')


if __name__ == '__main__':
    try:
        asyncio.run(QuotexObserver().run())
    except KeyboardInterrupt:
        pass