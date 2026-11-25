"""Service ingestion and operator-key authentication."""
import hashlib
import hmac
from fastapi import Header, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from market_config import INGEST_HASH, PROVIDER_CONTROL_KEY_SHA256
from api_key_auth import request_is_authenticated

header = APIKeyHeader(name='X-Market-QX-Key', auto_error=False)


async def require_observer_key(request: Request, key: str | None = Security(header)):
    if request_is_authenticated(request):
        return
    if not key or not INGEST_HASH or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), INGEST_HASH):
        raise HTTPException(401, 'Invalid ingestion key')


def authorize_operator_key(control_key: str | None, expected_hash: str | None = None):
    configured_hash = PROVIDER_CONTROL_KEY_SHA256 if expected_hash is None else expected_hash
    if not configured_hash:
        return
    supplied_hash = hashlib.sha256((control_key or '').encode()).hexdigest()
    if not control_key or not hmac.compare_digest(supplied_hash, configured_hash):
        raise HTTPException(401, 'Invalid provider control key')


def require_operator_key(
    control_key: str | None = Header(default=None, alias='X-Provider-Control-Key'),
):
    authorize_operator_key(control_key)