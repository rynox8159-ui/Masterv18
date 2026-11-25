"""Persisted QX_PROXY setting for the Quotex observer: validate, test, save to backend/.env."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from provider_control import ProviderController, _authorize

ENV_FILE = Path(__file__).resolve().parent / '.env'
TEST_URL = os.environ.get('PLAYWRIGHT_TARGET_URL', 'https://market-qx.info/en/trade')


class ProxyBody(BaseModel):
    proxy: str = Field(default='', max_length=500)


def mask(proxy):
    if not proxy:
        return None
    parts = urlsplit(proxy)
    auth = '***@' if parts.username else ''
    return f'{parts.scheme}://{auth}{parts.hostname}:{parts.port}'


def validate(proxy):
    parts = urlsplit(proxy)
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme not in {'http', 'https', 'socks5'} or not parts.hostname or not port:
        raise HTTPException(422, 'Format: http://user:pass@host:port or socks5://host:port')
    if parts.scheme == 'socks5' and parts.username:
        raise HTTPException(422, 'Chromium does not support SOCKS5 with username/password; use an http:// proxy')


def save_env(proxy):
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.is_file() else []
    lines = [line for line in lines if not re.match(r'\s*QX_PROXY\s*=', line)]
    if proxy:
        lines.append(f'QX_PROXY={proxy}')
    ENV_FILE.write_text('\n'.join(lines) + '\n')
    if proxy:
        os.environ['QX_PROXY'] = proxy
    else:
        os.environ.pop('QX_PROXY', None)


async def test_proxy(proxy):
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=20, follow_redirects=True, headers={'User-Agent': 'Mozilla/5.0'}) as client:
            response = await client.get(TEST_URL)
            ip = (await client.get('https://api.ipify.org?format=json')).json().get('ip')
    except Exception as exc:  # noqa: BLE001 - report any proxy failure to the operator
        return {'ok': False, 'status': None, 'ip': None, 'message': f'Proxy connection failed: {type(exc).__name__}'}
    if response.status_code == 403:
        return {'ok': False, 'status': 403, 'ip': ip, 'message': 'Proxy works but Quotex still blocks this IP/country'}
    return {'ok': response.status_code < 400, 'status': response.status_code, 'ip': ip, 'message': 'Quotex reachable through proxy' if response.status_code < 400 else f'Quotex returned HTTP {response.status_code}'}


def quotex_proxy_router(controller: ProviderController):
    router = APIRouter(prefix='/api/v1/providers/qx-proxy', tags=['provider-control'])
    last = {'test': None}

    @router.get('')
    async def status():
        return {'configured': bool(os.environ.get('QX_PROXY')), 'proxy': mask(os.environ.get('QX_PROXY', '')), 'lastTest': last['test']}

    @router.post('')
    async def update(body: ProxyBody, control_key: str | None = Header(default=None, alias='X-Provider-Control-Key')):
        _authorize(control_key)
        proxy = body.proxy.strip()
        result = None
        if proxy:
            validate(proxy)
            result = await test_proxy(proxy)
            last['test'] = result
            if not result['ok']:
                return {'saved': False, 'configured': bool(os.environ.get('QX_PROXY')), 'proxy': mask(os.environ.get('QX_PROXY', '')), 'test': result}
        save_env(proxy)
        if controller._process_status('playwright_observer')['state'] == 'RUNNING':
            await controller.stop('playwright_observer')
        if proxy:
            await controller.start('playwright_observer')
        return {'saved': True, 'configured': bool(proxy), 'proxy': mask(proxy), 'test': result}

    return router
