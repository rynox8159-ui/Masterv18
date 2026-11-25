"""Precision gate: final high-accuracy filter applied to every Deriv signal before it is shown.

Signals that fail are stored as PRECISION_SHADOW (hidden, still settled) so the gate's own
effect on accuracy can be measured from history.
"""
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_trade.accuracy import evaluate, rsi, atr, ema, _arrays  # noqa: E402
from market_config import TIMEFRAMES  # noqa: E402

HIGHER = {'1m': ('5m', '15m'), '5m': ('15m', '1h'), '15m': ('1h',), '30m': ('1h',)}
FOREX_MIN_TIMEFRAME_SECONDS = int(os.environ.get('PRECISION_FOREX_MIN_TF_SECONDS', '300'))
FOREX_SESSION_UTC = (int(os.environ.get('PRECISION_FOREX_SESSION_START', '7')), int(os.environ.get('PRECISION_FOREX_SESSION_END', '20')))
PAIR_MIN_SAMPLES = int(os.environ.get('PRECISION_PAIR_MIN_SAMPLES', '8'))
PAIR_MIN_WIN_RATE = float(os.environ.get('PRECISION_PAIR_MIN_WIN_RATE', '55'))
GATE_SETTINGS = {'minVotes': 5, 'minScore': 72, 'minAdx': 20, 'minAtrPercentile': 20, 'maxAtrPercentile': 95, 'pairMinSamples': PAIR_MIN_SAMPLES, 'pairMinWinRate': PAIR_MIN_WIN_RATE}


def currencies(symbol):
    if symbol.startswith('frx') and len(symbol) == 9:
        return {symbol[3:6], symbol[6:9]}
    if symbol.startswith('cry'):
        return {symbol[3:6]}
    return {symbol}


def exhaustion_reason(candles, direction):
    _, h, l, c = _arrays(candles)
    r = rsi(c)[-1]
    if direction == 'CALL' and r >= 70 or direction == 'PUT' and r <= 30:
        return f'RSI {r:.0f} exhausted'
    a = atr(h, l, c)[-1]
    stretch = (c[-1] - ema(c, 21)[-1]) / a if a else 0
    if abs(stretch) > 2.5:
        return f'price {stretch:+.1f} ATR from EMA21'
    return None


async def precision_check(db, store, instrument, timeframe, seconds, direction, entry, track_record, now=None):
    """Return (passed, report). report lists each layer for transparency in signal history."""
    now = time.time() if now is None else now
    source, symbol = instrument['source'], instrument['symbol']
    checks = []
    forex = symbol.startswith('frx')
    if forex:
        hour = time.gmtime(entry).tm_hour
        checks.append({'name': 'session', 'pass': FOREX_SESSION_UTC[0] <= hour < FOREX_SESSION_UTC[1], 'detail': f'{hour:02d}:00 UTC'})
        checks.append({'name': 'forex_timeframe', 'pass': seconds >= FOREX_MIN_TIMEFRAME_SECONDS, 'detail': f'{timeframe} (min {FOREX_MIN_TIMEFRAME_SECONDS // 60}m for forex)'})

    frames = {timeframe: await store.candles(source, symbol, timeframe, limit=200)}
    for tf in HIGHER.get(timeframe, ()):
        if tf in TIMEFRAMES:
            frames[tf] = await store.candles(source, symbol, tf, limit=120)
    closed = {
        tf: [c for c in rows if float(c['epoch']) + TIMEFRAMES[tf] <= now]
        for tf, rows in frames.items()
    }
    base = closed[timeframe]
    verdict = evaluate(symbol, {'1m': base, **{k: v for k, v in closed.items() if k != timeframe}}, GATE_SETTINGS,
                       {'wins': track_record.get('wins', 0), 'losses': track_record.get('losses', 0)})
    checks.extend(verdict['layers'])
    checks.append({'name': 'direction_match', 'pass': verdict.get('direction') == direction, 'detail': f"layers {verdict.get('direction')} vs signal {direction}"})
    if len(base) >= 60:
        reason = exhaustion_reason(base, direction)
        checks.append({'name': 'exhaustion', 'pass': reason is None, 'detail': reason or 'not overextended'})

    shared = await db.live_signals.count_documents({
        'status': 'PENDING', 'validationTier': {'$in': ['LIVE_VALIDATED', 'PAPER_SHADOW']},
        'entryEpoch': {'$gte': entry - seconds, '$lte': entry + seconds},
        'symbol': {'$in': [s for s in await _pending_symbols(db, entry, seconds) if currencies(s) & currencies(symbol)]},
    })
    checks.append({'name': 'correlation_cap', 'pass': shared == 0, 'detail': f'{shared} open signal(s) share a currency'})
    passed = all(check['pass'] for check in checks)
    return passed, {'passed': passed, 'score': verdict.get('score'), 'checks': checks}


async def _pending_symbols(db, entry, seconds):
    return await db.live_signals.distinct('symbol', {
        'status': 'PENDING', 'validationTier': {'$in': ['LIVE_VALIDATED', 'PAPER_SHADOW']},
        'entryEpoch': {'$gte': entry - seconds, '$lte': entry + seconds},
    })
