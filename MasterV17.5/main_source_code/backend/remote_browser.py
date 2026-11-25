"""Authenticated frame and input relay for the backend-side Playwright observer."""
import time
import math
from collections import deque
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from market_auth import authorize_operator_key
from market_config import PROVIDER_CONTROL_KEY_SHA256

VIEWER_TTL = 8
ALLOWED = {'click', 'dblclick', 'type', 'key', 'scroll', 'reload'}
ALLOWED_KEYS = {
    'Enter', 'Backspace', 'Tab', 'Escape', 'ArrowLeft', 'ArrowRight',
    'ArrowUp', 'ArrowDown', 'Delete', 'Home', 'End',
}
MAX_FRAME_BYTES = 1_500_000


class RemoteInput(BaseModel):
    type: str = Field(max_length=16)
    x: float | None = Field(default=None, ge=0, le=4096)
    y: float | None = Field(default=None, ge=0, le=4096)
    text: str | None = Field(default=None, max_length=4096)
    key: str | None = Field(default=None, max_length=32)
    dy: float | None = Field(default=None, ge=-4096, le=4096)


def _authorize(control_key):
    # Same policy as the other operator routes: without PROVIDER_CONTROL_KEY_SHA256 the
    # X-API-Key gate protects it (so the user can log in to Quotex via "View browser");
    # set PROVIDER_CONTROL_KEY_SHA256 to require a separate operator key.
    authorize_operator_key(control_key)


class RemoteBrowser:
    def __init__(self):
        self.frame = b''
        self.frame_at = 0.0
        self.url = ''
        self.title = ''
        self.viewport = {'width': 1366, 'height': 768}
        self.viewer_at = 0.0
        self.events = deque(maxlen=200)

    def state(self):
        now = time.time()
        return {
            'available': now - self.frame_at < 15,
            'frameAgeSeconds': round(now - self.frame_at, 1) if self.frame_at else None,
            'url': self.url,
            'title': self.title,
            'viewport': self.viewport,
        }


def remote_browser_router(remote: RemoteBrowser):
    router = APIRouter(prefix='/api/v1/observer/remote', tags=['observer-remote'])

    @router.post('/frame')
    async def push_frame(
        request: Request,
        control_key: str | None = Header(default=None, alias='X-Provider-Control-Key'),
    ):
        _authorize(control_key)
        if not request.headers.get('content-type', '').startswith('image/jpeg'):
            raise HTTPException(415, 'Expected a JPEG browser frame')
        body = await request.body()
        if not body or len(body) > MAX_FRAME_BYTES:
            raise HTTPException(413, 'Browser frame is empty or too large')
        raw_url = request.headers.get('x-page-url', '')
        parsed_url = urlparse(raw_url)
        if parsed_url.scheme not in {'http', 'https'} or not parsed_url.hostname:
            raise HTTPException(400, 'Invalid browser page URL')
        remote.url = raw_url[:300]
        remote.title = unquote(request.headers.get('x-page-title', ''))[:200]
        try:
            width = int(request.headers.get('x-viewport-width', '1366'))
            height = int(request.headers.get('x-viewport-height', '768'))
        except ValueError as exc:
            raise HTTPException(400, 'Invalid browser viewport') from exc
        remote.viewport = {'width': min(max(width, 1), 4096), 'height': min(max(height, 1), 4096)}
        remote.frame = body
        remote.frame_at = time.time()
        viewer = time.time() - remote.viewer_at < VIEWER_TTL
        events = list(remote.events) if viewer else []
        remote.events.clear()
        return {'viewer': viewer, 'events': events}

    @router.get('/frame')
    async def get_frame(
        control_key: str | None = Header(default=None, alias='X-Provider-Control-Key'),
    ):
        _authorize(control_key)
        remote.viewer_at = time.time()
        if not remote.frame:
            return Response(status_code=204)
        return Response(remote.frame, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    @router.get('/state')
    async def get_state(
        control_key: str | None = Header(default=None, alias='X-Provider-Control-Key'),
    ):
        _authorize(control_key)
        remote.viewer_at = time.time()
        return remote.state()

    @router.post('/input')
    async def push_input(
        event: RemoteInput,
        control_key: str | None = Header(default=None, alias='X-Provider-Control-Key'),
    ):
        _authorize(control_key)
        if event.type not in ALLOWED:
            raise HTTPException(400, 'Unsupported remote browser input')
        if event.type in {'click', 'dblclick'} and (event.x is None or event.y is None):
            raise HTTPException(400, 'Click input requires x and y coordinates')
        if event.type in {'click', 'dblclick'} and (
            not math.isfinite(event.x) or not math.isfinite(event.y)
            or
            event.x >= remote.viewport['width'] or event.y >= remote.viewport['height']
        ):
            raise HTTPException(400, 'Click coordinates are outside the current browser viewport')
        if event.type == 'type' and not event.text:
            raise HTTPException(400, 'Text input cannot be empty')
        if event.type == 'key' and event.key not in ALLOWED_KEYS:
            raise HTTPException(400, 'Unsupported keyboard key')
        remote.viewer_at = time.time()
        remote.events.append(event.model_dump(exclude_none=True))
        return {'queued': True}

    return router
