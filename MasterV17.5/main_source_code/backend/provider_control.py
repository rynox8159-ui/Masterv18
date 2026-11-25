"""Authenticated controls for the fixed, allowlisted market providers."""
import asyncio
import os
import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException

from deriv_service import DerivService
from market_auth import authorize_operator_key
from market_config import (
    DERIV_CANDLE_SYMBOLS,
    DERIV_ENABLED,
    DERIV_MAX_CANDLE_SYMBOLS,
    DERIV_SYMBOLS,
    PROVIDER_CONTROL_KEY_SHA256,
)

ROOT = Path(__file__).resolve().parents[2]
OBSERVER_DIR = ROOT / 'main_source_code' / 'extensions' / 'ws-sniffer' / 'playwright'
OBSERVER_SCRIPT = OBSERVER_DIR / 'playwright_observer.py'
OBSERVER_EXECUTABLE = OBSERVER_DIR / 'dist' / 'ws-sniffer' / 'ws-sniffer.exe'
PROVIDERS = ('deriv', 'ws_sniffer', 'playwright_observer')
LOG_DIR = Path(__file__).resolve().parent / 'logs'


def log_tail(provider, lines=12):
    path = LOG_DIR / f'{provider}.log'
    if not path.is_file():
        return []
    text = path.read_text(encoding='utf-8', errors='ignore').replace('\x00', '')
    return [line[:500] for line in text.splitlines() if line.strip()][-lines:]


def _signal_group(process, name):
    """Signal the observer and its children (Chromium, Xvfb); plain terminate/kill on Windows."""
    if os.name == 'nt':
        process.terminate() if name == 'SIGTERM' else process.kill()
        return
    import signal
    try:
        os.killpg(process.pid, getattr(signal, name))
    except ProcessLookupError:
        pass


class ProviderController:
    def __init__(self, deriv: DerivService):
        self.deriv = deriv
        self.deriv_task = None
        self.processes = {}

    @staticmethod
    def configuration():
        return {
            'deriv': {
                'enabled': DERIV_ENABLED,
                'symbols': list(DERIV_SYMBOLS),
                'candleSymbols': list(DERIV_CANDLE_SYMBOLS),
                'maxCandleSymbols': DERIV_MAX_CANDLE_SYMBOLS,
                'markets': [
                    'forex', 'metals', 'non-OTC indices',
                    'selected volatility indices', 'cryptocurrency',
                ],
                'otc': False,
            },
            'ws_sniffer': {
                'backendUrl': os.environ.get('OBSERVER_BACKEND_URL', f"http://127.0.0.1:{os.environ.get('BACKEND_PORT', '7007')}"),
                'cdpUrl': os.environ.get('PLAYWRIGHT_CDP_URL', 'http://127.0.0.1:9222'),
                'targetUrl': os.environ.get('PLAYWRIGHT_TARGET_URL', 'https://market-qx.info/en/trade'),
                'autoSubscribe': os.environ.get('WS_SNIFFER_AUTO_SUBSCRIBE', 'true').lower() == 'true',
                'cdpTimeoutSeconds': float(os.environ.get('WS_SNIFFER_CDP_TIMEOUT_SECONDS', '20')),
                'idleTimeoutSeconds': float(os.environ.get('WS_SNIFFER_IDLE_TIMEOUT_SECONDS', '30')),
            },
            'playwright_observer': {
                'backendUrl': os.environ.get('OBSERVER_BACKEND_URL', f"http://127.0.0.1:{os.environ.get('BACKEND_PORT', '7007')}"),
                'cdpUrl': os.environ.get('PLAYWRIGHT_CDP_URL', 'http://127.0.0.1:9222'),
                'targetUrl': os.environ.get('PLAYWRIGHT_TARGET_URL', 'https://market-qx.info/en/trade'),
                'selectors': os.environ.get('PLAYWRIGHT_SELECTORS_JSON', '{}'),
            },
        }

    def _deriv_status(self):
        task = self.deriv_task
        if task and not task.done():
            state = 'RUNNING'
        elif not DERIV_ENABLED:
            state = 'DISABLED'
        elif task is not None:
            state = 'FAILED'
        else:
            state = 'STOPPED'
        return {**self.deriv.status(), 'state': state}

    def _process_status(self, provider):
        process = self.processes.get(provider)
        if process is None:
            return {'state': 'STOPPED', 'pid': None, 'exitCode': None, 'log': log_tail(provider)}
        exit_code = process.returncode
        if exit_code is None:
            tail = log_tail(provider)
            detail = next((line.split('Status: ', 1)[1] for line in reversed(tail) if 'Status: ' in line), 'starting browser…')
            return {'state': 'RUNNING', 'pid': process.pid, 'exitCode': None, 'detail': detail, 'log': tail}
        user_stopped = provider in getattr(self, 'user_stopped', set())
        return {'state': 'STOPPED' if exit_code == 0 or user_stopped else 'FAILED', 'pid': process.pid, 'exitCode': exit_code, 'log': log_tail(provider)}

    def status(self):
        configuration = self.configuration()
        return {
            'controlConfigured': True,
            'controlKeyRequired': bool(PROVIDER_CONTROL_KEY_SHA256),
            'providers': {
                'deriv': {**self._deriv_status(), 'configuration': configuration['deriv']},
                'ws_sniffer': {**self._process_status('ws_sniffer'), 'configuration': configuration['ws_sniffer']},
                'playwright_observer': {**self._process_status('playwright_observer'), 'configuration': configuration['playwright_observer']},
            },
        }

    async def start(self, provider):
        if provider == 'deriv':
            if not DERIV_ENABLED:
                raise RuntimeError('DERIV_DISABLED_IN_BACKEND_CONFIGURATION')
            if self.deriv_task and not self.deriv_task.done():
                return self.status()
            self.deriv_task = asyncio.create_task(self.deriv.run(), name='deriv-public-feed')
            return self.status()
        if provider not in ('ws_sniffer', 'playwright_observer'):
            raise ValueError('UNKNOWN_PROVIDER')
        if self._process_status(provider)['state'] == 'RUNNING':
            return self.status()  # already running: never orphan the old process
        other = 'playwright_observer' if provider == 'ws_sniffer' else 'ws_sniffer'
        if self._process_status(other)['state'] == 'RUNNING':
            raise RuntimeError(f'{other.upper()}_ALREADY_RUNNING')
        if OBSERVER_EXECUTABLE.is_file():
            command = [str(OBSERVER_EXECUTABLE)]
        elif OBSERVER_SCRIPT.is_file():
            command = [sys.executable, str(OBSERVER_SCRIPT)]
        else:
            raise RuntimeError('OBSERVER_SCRIPT_NOT_FOUND')
        env = os.environ.copy()
        env['OBSERVER_MODE'] = provider
        env.setdefault('OBSERVER_BACKEND_URL', f"http://127.0.0.1:{os.environ.get('BACKEND_PORT', '7007')}")
        env['PYTHONUNBUFFERED'] = '1'
        LOG_DIR.mkdir(exist_ok=True)
        log_file = open(LOG_DIR / f'{provider}.log', 'wb')
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(ROOT), env=env,
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=os.name != 'nt',  # own process group: stop() also ends Chromium/Xvfb
        )
        log_file.close()
        self.processes[provider] = process
        self.user_stopped = getattr(self, 'user_stopped', set()) - {provider}
        return self.status()

    async def stop(self, provider):
        if provider == 'deriv':
            task, self.deriv_task = self.deriv_task, None
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.deriv.state = 'STOPPED' if DERIV_ENABLED else 'DISABLED'
            return self.status()
        if provider not in ('ws_sniffer', 'playwright_observer'):
            raise ValueError('UNKNOWN_PROVIDER')
        process = self.processes.get(provider)
        if process and process.returncode is None:
            self.user_stopped = getattr(self, 'user_stopped', set()) | {provider}
            _signal_group(process, 'SIGTERM')
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                _signal_group(process, 'SIGKILL')
                await process.wait()
        return self.status()

    async def stop_all(self):
        for provider in ('ws_sniffer', 'playwright_observer', 'deriv'):
            await self.stop(provider)


def _authorize(control_key: str | None):
    # The API-key middleware already protects this router; the extra operator key is optional.
    if PROVIDER_CONTROL_KEY_SHA256:
        authorize_operator_key(control_key, PROVIDER_CONTROL_KEY_SHA256)


def provider_control_router(controller: ProviderController):
    router = APIRouter(prefix='/api/v1/providers', tags=['provider-control'])

    @router.get('/status')
    async def status():
        return controller.status()

    @router.post('/{provider}/start')
    async def start(provider: Literal['deriv', 'ws_sniffer', 'playwright_observer'], control_key: str | None = Header(default=None, alias='X-Provider-Control-Key')):
        _authorize(control_key)
        try:
            return await controller.start(provider)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/{provider}/stop')
    async def stop(provider: Literal['deriv', 'ws_sniffer', 'playwright_observer'], control_key: str | None = Header(default=None, alias='X-Provider-Control-Key')):
        _authorize(control_key)
        try:
            return await controller.stop(provider)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
