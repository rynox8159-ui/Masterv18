"""Multi-layer accuracy filter: indicator confluence voting, multi-timeframe confirmation,
volatility/trend regime filter, pair win-rate filter and optional LLM second opinion."""
import logging

import numpy as np

logger = logging.getLogger(__name__)


def ema(values, period):
    out = np.empty_like(values)
    alpha = 2 / (period + 1)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def wilder(values, period):
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain = wilder(np.clip(delta, 0, None), period)
    loss = wilder(np.clip(-delta, 0, None), period)
    rs = gain / np.where(loss == 0, 1e-12, loss)
    return 100 - 100 / (1 + rs)


def atr(high, low, close, period=14):
    prev = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low, np.maximum(abs(high - prev), abs(low - prev)))
    return wilder(tr, period)


def adx(high, low, close, period=14):
    up = np.diff(high, prepend=high[0])
    down = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    rng = np.where(atr(high, low, close, period) == 0, 1e-12, atr(high, low, close, period))
    plus_di = 100 * wilder(plus_dm, period) / rng
    minus_di = 100 * wilder(minus_dm, period) / rng
    dx = 100 * abs(plus_di - minus_di) / np.where(plus_di + minus_di == 0, 1e-12, plus_di + minus_di)
    return wilder(dx, period), plus_di, minus_di


def _arrays(candles):
    return tuple(np.array([c[k] for c in candles], dtype=float) for k in ("open", "high", "low", "close"))


def trend_direction(candles):
    _, _, _, close = _arrays(candles)
    fast, slow = ema(close, 9), ema(close, 21)
    if fast[-1] > slow[-1] and close[-1] > slow[-1]:
        return "CALL"
    if fast[-1] < slow[-1] and close[-1] < slow[-1]:
        return "PUT"
    return "NEUTRAL"


def confluence_votes(candles):
    o, h, l, c = _arrays(candles)
    votes = {}
    e9, e21, e50 = ema(c, 9), ema(c, 21), ema(c, 50)
    votes["ema_cross"] = "CALL" if e9[-1] > e21[-1] else "PUT"
    votes["ema50_side"] = "CALL" if c[-1] > e50[-1] else "PUT"
    r = rsi(c)[-1]
    votes["rsi"] = "CALL" if 52 <= r <= 75 else "PUT" if 25 <= r <= 48 else "NEUTRAL"
    macd = ema(c, 12) - ema(c, 26)
    hist = macd - ema(macd, 9)
    votes["macd"] = "CALL" if hist[-1] > 0 and hist[-1] >= hist[-2] else "PUT" if hist[-1] < 0 and hist[-1] <= hist[-2] else "NEUTRAL"
    lo14 = np.array([l[max(0, i - 13):i + 1].min() for i in range(len(c))])
    hi14 = np.array([h[max(0, i - 13):i + 1].max() for i in range(len(c))])
    k = 100 * (c - lo14) / np.where(hi14 - lo14 == 0, 1e-12, hi14 - lo14)
    d = np.convolve(k, np.ones(3) / 3, mode="same")
    votes["stochastic"] = "CALL" if k[-1] > d[-2] and k[-1] < 85 else "PUT" if k[-1] < d[-2] and k[-1] > 15 else "NEUTRAL"
    mid = c[-20:].mean()
    votes["bollinger_mid"] = "CALL" if c[-1] > mid else "PUT"
    bodies = c[-3:] - o[-3:]
    votes["candle_momentum"] = "CALL" if (bodies > 0).sum() >= 2 and bodies[-1] > 0 else "PUT" if (bodies < 0).sum() >= 2 and bodies[-1] < 0 else "NEUTRAL"
    indicators = {"rsi": round(float(r), 2), "macdHist": float(hist[-1]), "stochK": round(float(k[-1]), 2), "ema9": float(e9[-1]), "ema21": float(e21[-1]), "ema50": float(e50[-1]), "close": float(c[-1])}
    return votes, indicators


def regime(candles):
    _, h, l, c = _arrays(candles)
    a = atr(h, l, c)
    norm = a / c
    pct = float((norm[-100:] < norm[-1]).mean() * 100)
    adx_v, plus_di, minus_di = adx(h, l, c)
    last_range = h[-1] - l[-1]
    return {"atrPercentile": round(pct, 1), "adx": round(float(adx_v[-1]), 2), "plusDI": round(float(plus_di[-1]), 2), "minusDI": round(float(minus_di[-1]), 2), "spike": bool(last_range > 3 * a[-2])}


def evaluate(symbol, tf_candles, settings, pair_stats=None):
    """Return a layered verdict for one symbol. tf_candles = {'1m': [...], '5m': [...], '15m': [...]}."""
    layers = []
    base = tf_candles.get("1m") or []
    if len(base) < 60:
        return {"symbol": symbol, "approved": False, "direction": None, "score": 0, "layers": [{"name": "data", "pass": False, "detail": f"only {len(base)} candles"}]}
    votes, indicators = confluence_votes(base)
    calls = sum(v == "CALL" for v in votes.values())
    puts = sum(v == "PUT" for v in votes.values())
    direction = "CALL" if calls > puts else "PUT" if puts > calls else None
    agree, oppose = max(calls, puts), min(calls, puts)
    min_votes = int(settings.get("minVotes", 5))
    layers.append({"name": "confluence", "pass": bool(direction) and agree >= min_votes and oppose <= 1, "detail": f"{agree}/{len(votes)} agree {direction or '-'} · {oppose} oppose", "votes": votes})

    mtf = {tf: trend_direction(candles) for tf, candles in tf_candles.items() if tf != "1m" and len(candles) >= 30}
    mtf_ok = bool(direction) and bool(mtf) and all(v == direction for v in mtf.values())
    layers.append({"name": "multi_timeframe", "pass": mtf_ok, "detail": ", ".join(f"{tf}:{v}" for tf, v in mtf.items()) or "no higher timeframe data"})

    reg = regime(base)
    di_ok = (direction == "CALL" and reg["plusDI"] > reg["minusDI"]) or (direction == "PUT" and reg["minusDI"] > reg["plusDI"])
    reg_ok = settings.get("minAtrPercentile", 20) <= reg["atrPercentile"] <= settings.get("maxAtrPercentile", 95) and reg["adx"] >= settings.get("minAdx", 20) and not reg["spike"] and di_ok
    layers.append({"name": "regime", "pass": reg_ok, "detail": f"ADX {reg['adx']} · ATR pct {reg['atrPercentile']} · DI {'ok' if di_ok else 'against'}{' · spike' if reg['spike'] else ''}"})

    stats = pair_stats or {"wins": 0, "losses": 0}
    samples = stats["wins"] + stats["losses"]
    win_rate = round(100 * stats["wins"] / samples, 1) if samples else None
    pair_ok = samples < settings.get("pairMinSamples", 5) or win_rate >= settings.get("pairMinWinRate", 50)
    layers.append({"name": "pair_win_rate", "pass": pair_ok, "detail": f"{win_rate}% over {samples} trades" if samples else "no history yet (allowed)"})

    score = round(agree / len(votes) * 50 + (20 if mtf_ok else 0) + min(reg["adx"], 40) / 40 * 15 + (15 if reg_ok else 0), 1)
    approved = all(layer["pass"] for layer in layers) and score >= settings.get("minScore", 70)
    return {"symbol": symbol, "direction": direction, "score": score, "approved": approved, "layers": layers, "indicators": {**indicators, **reg}, "mtf": mtf}
