"""Server-only credential exchange for short-lived HttpOnly API sessions."""
import base64
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

SESSION_COOKIE = 'masterv17_session'
SESSION_TTL_SECONDS = 8 * 60 * 60
MIN_CREDENTIAL_LENGTH = 32
PUBLIC_METHODS = {
    ('GET', '/api/health'),
    ('GET', '/api/v1/auth/status'),
    ('GET', '/api/v1/auth/session'),
    ('POST', '/api/v1/auth/session'),
    ('DELETE', '/api/v1/auth/session'),
}

class SessionRequest(BaseModel):
    credential: str = Field(min_length=MIN_CREDENTIAL_LENGTH, max_length=512)


def configured_credential() -> str:
    value = (os.environ.get('BACKEND_API_KEY') or '').strip()
    return value if len(value) >= MIN_CREDENTIAL_LENGTH else ''


# Import-time compatibility value; request authentication reads the environment
# dynamically through configured_credential().
API_KEY = configured_credential()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def create_session_token(credential: str, now: int | None = None) -> str:
    expires_at = int(now if now is not None else time.time()) + SESSION_TTL_SECONDS
    payload = f'{expires_at}.{secrets.token_urlsafe(18)}'.encode('ascii')
    signature = hmac.new(credential.encode(), payload, hashlib.sha256).digest()
    return f'{_b64(payload)}.{_b64(signature)}'


def session_token_is_valid(token: str | None, credential: str, now: int | None = None) -> bool:
    if not token or not credential:
        return False
    try:
        payload_part, signature_part = token.split('.', 1)
        payload = base64.urlsafe_b64decode(payload_part + '=' * (-len(payload_part) % 4))
        signature = base64.urlsafe_b64decode(signature_part + '=' * (-len(signature_part) % 4))
        expires_part, nonce = payload.decode('ascii').split('.', 1)
        expires_at = int(expires_part)
    except (ValueError, UnicodeDecodeError):
        return False
    current_time = int(now if now is not None else time.time())
    if not nonce or expires_at < current_time or expires_at > current_time + SESSION_TTL_SECONDS + 30:
        return False
    expected = hmac.new(credential.encode(), payload, hashlib.sha256).digest()
    return hmac.compare_digest(signature, expected)


def request_is_authenticated(request: Request) -> bool:
    credential = configured_credential()
    if session_token_is_valid(request.cookies.get(SESSION_COOKIE), credential):
        return True
    header_credential = (request.headers.get('X-API-Key') or '').strip()
    return bool(header_credential) and hmac.compare_digest(header_credential, credential)


def _origin_is_trusted(request: Request) -> bool:
    origin = request.headers.get('origin')
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        return False
    from market_config import ORIGINS
    return origin.rstrip('/') in {item.rstrip('/') for item in ORIGINS}


async def api_key_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith('/api') or request.method == 'OPTIONS':
        return await call_next(request)
    if request.method not in {'GET', 'HEAD'} and not _origin_is_trusted(request):
        return JSONResponse({'detail': 'ORIGIN_NOT_ALLOWED'}, status_code=403)
    if (request.method, path) in PUBLIC_METHODS:
        return await call_next(request)
    if not configured_credential():
        return JSONResponse({'detail': 'AUTH_NOT_CONFIGURED'}, status_code=503)
    if not request_is_authenticated(request):
        return JSONResponse({'detail': 'SESSION_REQUIRED'}, status_code=401)
    return await call_next(request)


def _set_cookie(response: Response, request: Request, credential: str) -> None:
    origin = request.headers.get('origin', '')
    forwarded_proto = request.headers.get('x-forwarded-proto', '')
    secure = request.url.scheme == 'https' or forwarded_proto == 'https' or origin.startswith('https://')
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(credential),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite='none' if secure else 'lax',
        path='/api',
    )


def auth_router():
    router = APIRouter(prefix='/api/v1/auth', tags=['Session authentication'])

    @router.get('/status')
    async def status():
        return {'configured': bool(configured_credential()), 'sessionRequired': True}

    @router.get('/session')
    async def session_status(request: Request):
        if not configured_credential():
            return {'authenticated': False, 'configured': False}
        return {'authenticated': request_is_authenticated(request), 'configured': True}

    @router.post('/session')
    async def create_session(body: SessionRequest, request: Request, response: Response):
        expected = configured_credential()
        if not expected:
            raise HTTPException(503, 'AUTH_NOT_CONFIGURED')
        if not hmac.compare_digest(body.credential.encode(), expected.encode()):
            raise HTTPException(401, 'INVALID_CREDENTIAL')
        _set_cookie(response, request, expected)
        return {'authenticated': True, 'expiresInSeconds': SESSION_TTL_SECONDS}

    @router.delete('/session')
    async def delete_session(response: Response):
        response.delete_cookie(SESSION_COOKIE, path='/api', httponly=True, samesite='lax')
        return {'authenticated': False}

    return router
