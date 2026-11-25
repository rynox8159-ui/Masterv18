import asyncio
from concurrent.futures import ProcessPoolExecutor
import json
import math
import os
from pathlib import Path

import httpx
import numpy as np
import websockets
from dotenv import dotenv_values
from pymongo import MongoClient
from scipy.stats import norm
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from lab.binary_lab import (
    _hard_predictions,
    _new_model,
    benjamini_hochberg,
    locked_walk_forward,
    wilson_interval,
)

REST_BASE = "https://api.derivws.com/trading/v1/options"
PERMUTATIONS = 1000
PERMUTATION_WORKERS = 8


def env_config():
    env_path = Path(__file__).resolve().parents[1] / ".env"
    env = dotenv_values(env_path)
    return env, (
        os.environ.get("MONGO_URL") or env.get("MONGO_URL") or "mongodb://127.0.0.1:27017",
        os.environ.get("DB_NAME") or env.get("DB_NAME") or "master_candel",
    )


async def _demo_proposal_payouts(symbols):
    env, _ = env_config()
    app_id = os.environ.get("DERIV_APP_ID") or env.get("DERIV_APP_ID")
    token = os.environ.get("DERIV_DEMO_TOKEN") or os.environ.get("DERIV_API_TOKEN_1") or env.get("DERIV_DEMO_TOKEN") or env.get("DERIV_API_TOKEN_1")
    if not app_id or not token:
        raise RuntimeError("DEMO_APP_ID_AND_TOKEN_REQUIRED")
    headers = {
        "Authorization": f"Bearer {token}",
        "Deriv-App-ID": app_id,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{REST_BASE}/accounts", headers=headers)
        response.raise_for_status()
        accounts = response.json().get("data") or []
        account = next(
            (row for row in accounts
             if row.get("account_type") == "demo" and row.get("status") == "active"),
            None,
        )
        if not account:
            raise RuntimeError("NO_ACTIVE_DEMO_ACCOUNT")
        otp = await client.post(
            f"{REST_BASE}/accounts/{account['account_id']}/otp",
            headers=headers,
        )
        otp.raise_for_status()
        url = (otp.json().get("data") or {}).get("url")
    if not url:
        raise RuntimeError("DEMO_OTP_URL_MISSING")

    async with websockets.connect(url, open_timeout=15, close_timeout=5) as ws:
        request_id = 0

        async def request(payload):
            nonlocal request_id
            request_id += 1
            rid = request_id
            await ws.send(json.dumps({**payload, "req_id": rid}))
            while True:
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if message.get("req_id") == rid:
                    return message

        payouts = {}
        for symbol in symbols:
            payouts[symbol] = {}
            for direction in ("CALL", "PUT"):
                message = await request({
                    "proposal": 1,
                    "amount": 1,
                    "basis": "stake",
                    "contract_type": direction,
                    "currency": account.get("currency") or "USD",
                    "underlying_symbol": symbol,
                    "duration": 15,
                    "duration_unit": "m",
                })
                proposal = message.get("proposal") or {}
                error = message.get("error") or {}
                ask = proposal.get("ask_price")
                payout = proposal.get("payout")
                payouts[symbol][direction] = {
                    "ask": float(ask) if ask is not None else None,
                    "payout": float(payout) if payout is not None else None,
                    "breakeven": float(ask) / float(payout) if ask and payout else None,
                    "error": error.get("code"),
                    "error_text": error.get("message"),
                }
                await asyncio.sleep(0.15)
    return payouts


def _conditional_pvalue(hits, probabilities):
    probabilities = np.asarray(probabilities, dtype=float)
    if probabilities.size == 0:
        return 1.0
    expected = float(probabilities.sum())
    variance = float(np.sum(probabilities * (1-probabilities)))
    if variance <= 0:
        return 0.0 if hits > expected else 1.0
    return float(norm.sf((hits - 0.5 - expected) / math.sqrt(variance)))


def _row_metrics(predictions, truth, payout):
    predictions = np.asarray(predictions, dtype=int)
    truth = np.asarray(truth, dtype=int)
    if len(predictions) != len(truth) or not len(truth):
        return None
    directions = np.where(predictions == 1, "CALL", "PUT")
    probabilities = np.asarray([
        payout.get(direction, {}).get("breakeven")
        for direction in directions
    ], dtype=object)
    valid = np.asarray([value is not None for value in probabilities], dtype=bool)
    if not valid.all():
        return None
    be = probabilities.astype(float)
    wins = predictions == truth
    hits = int(wins.sum())
    lower, upper = wilson_interval(hits, len(truth))
    return {
        "samples": len(truth),
        "hits": hits,
        "win_rate": hits/len(truth),
        "wilson_low": lower,
        "wilson_high": upper,
        "breakeven_mean": float(be.mean()),
        "p_value": _conditional_pvalue(hits, be),
        "directional_breakeven": be.tolist(),
        "predictions": predictions.tolist(),
        "truth": truth.tolist(),
    }


def _permutation_row_control(args):
    row_index, model_name, data, payout, permutations, seed = args
    rng = np.random.default_rng(seed)
    if model_name == "logistic":
        scaler = StandardScaler().fit(data["X_train"])
        X_train = scaler.transform(data["X_train"])
        X_test = scaler.transform(data["X_test"])
    else:
        X_train = data["X_train"]
        X_test = data["X_test"]

    pvalues = np.ones(permutations, dtype=float)
    provisional = np.zeros(permutations, dtype=bool)
    with threadpool_limits(limits=1):
        for iteration in range(permutations):
            if model_name == "logistic":
                model = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
            else:
                model = _new_model(model_name)
            model.fit(X_train, rng.permutation(data["y_train"]))
            predictions = _hard_predictions(model, X_test)
            truth = data["y_test"]
            hit_count = int(np.sum(predictions == truth))
            directions = np.where(predictions == 1, "CALL", "PUT")
            probabilities = np.asarray([
                payout[direction]["breakeven"] for direction in directions
            ], dtype=float)
            pvalues[iteration] = _conditional_pvalue(hit_count, probabilities)
            low, _ = wilson_interval(hit_count, len(truth))
            provisional[iteration] = (
                len(truth) >= 300
                and hit_count/len(truth) > float(probabilities.mean())
                and low > float(probabilities.mean())
            )
    return row_index, pvalues, provisional


def _permutation_control(rows, permutations=PERMUTATIONS, seed=7):
    """Refit each selected model on shuffled training labels and re-test the lockbox."""
    rng = np.random.default_rng(seed)
    counts = np.zeros(len(rows), dtype=int)
    total_passes = 0
    max_passes = 0
    eligible = [
        row for row in rows
        if row.get("permutation_data")
        and row.get("lockedMetrics")
        and row["lockedMetrics"]["samples"] >= 300
        and all(row["payout"].get(side, {}).get("breakeven") is not None
                for side in ("CALL", "PUT"))
    ]
    eligible_ids = {id(row) for row in eligible}
    permutation_pvalues = np.ones((len(rows), permutations), dtype=float)
    permutation_provisional = np.zeros((len(rows), permutations), dtype=bool)
    tasks = [
        (
            index,
            row["model"],
            row["permutation_data"],
            row["payout"],
            permutations,
            seed + index,
        )
        for index, row in enumerate(rows)
        if id(row) in eligible_ids
    ]
    if len(tasks) > 1 and permutations >= 100:
        with ProcessPoolExecutor(
            max_workers=min(PERMUTATION_WORKERS, len(tasks))
        ) as executor:
            results = executor.map(_permutation_row_control, tasks)
            for index, pvalues, provisional in results:
                permutation_pvalues[index] = pvalues
                permutation_provisional[index] = provisional
    else:
        for task in tasks:
            index, pvalues, provisional = _permutation_row_control(task)
            permutation_pvalues[index] = pvalues
            permutation_provisional[index] = provisional

    for iteration in range(permutations):
        adjusted = benjamini_hochberg(permutation_pvalues[:, iteration].tolist())
        passing = [
            bool(
                permutation_provisional[index, iteration]
                and adjusted[index] < 0.05
            )
            for index in range(len(rows))
        ]
        counts += np.asarray(passing, dtype=int)
        pass_count = int(sum(passing))
        total_passes += pass_count
        max_passes = max(max_passes, pass_count)
    return {
        "permutations": permutations,
        "eligible_records": len(eligible),
        "mean_pairs_passing_by_chance": total_passes/permutations,
        "max_pairs_passing_in_one_shuffle": max_passes,
        "pairs_with_at_least_one_null_pass": int(np.sum(counts > 0)),
        "per_pair_null_passes": {
            f"{row['symbol']} {row['timeframe']}": int(counts[index])
            for index, row in enumerate(rows)
        },
    }


def _get_candles(db, symbol, timeframe):
    newest = list(db.market_candles.find(
        {
            "source": "deriv",
            "symbol": symbol,
            "timeframe": timeframe,
            "completeness": "PROVIDER_OHLC",
        },
        {"_id": 0, "epoch": 1, "open": 1, "high": 1, "low": 1, "close": 1},
    ).sort("epoch", -1).limit(12000))
    return sorted(newest, key=lambda row: int(row["epoch"]))


def _analyze_candle_record(task):
    symbol, timeframe, seconds, candles = task
    horizon = 900 // seconds
    result = locked_walk_forward(
        candles,
        horizon=horizon,
        seconds=seconds,
        folds=4,
        embargo=horizon,
    )
    row = {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": len(candles),
        "horizon": f"15m/{horizon} candles",
        "model": result.get("model"),
        "oos": result.get("oos"),
        "locked": result.get("locked"),
        "cutoff": result.get("trainingCutoffEpoch"),
        "locked_start": result.get("lockedStartEpoch"),
        "feature_before_entry": result.get("featureBeforeEntry"),
        "label_overlap": result.get("labelOverlap"),
        "permutation_data": result.get("permutationData"),
    }
    return row


def generate(output="docs/BINARY_LAB_REPORT.md"):
    _, (mongo_url, db_name) = env_config()
    db = MongoClient(mongo_url, serverSelectionTimeoutMS=3000)[db_name]
    symbols = sorted({
        str(row["symbol"])
        for row in db.pair_promotions.find(
            {"symbol": {"$regex": "^frx[A-Z]{6}$", "$options": "i"}},
            {"_id": 0, "symbol": 1},
        )
    })
    payouts = asyncio.run(_demo_proposal_payouts(symbols))
    tasks = []
    for symbol in symbols:
        for timeframe, seconds in (("1m", 60), ("5m", 300)):
            candles = _get_candles(db, symbol, timeframe)
            tasks.append((symbol, timeframe, seconds, candles))
    with ProcessPoolExecutor(
        max_workers=min(PERMUTATION_WORKERS, len(tasks))
    ) as executor:
        rows = list(executor.map(_analyze_candle_record, tasks))

    for row in rows:
        row["payout"] = payouts.get(row["symbol"], {})
        row["lockedMetrics"] = (
            _row_metrics(
                row["locked"]["predictions"],
                row["locked"]["truth"],
                row["payout"],
            )
            if row["locked"] else None
        )
        row["raw_p"] = (
            row["lockedMetrics"]["p_value"] if row["lockedMetrics"] else 1.0
        )

    adjusted = benjamini_hochberg([row["raw_p"] for row in rows])
    for row, q in zip(rows, adjusted):
        row["bh_q"] = q
        metric = row["lockedMetrics"]
        row["statistical_screen"] = (
            "PASS" if metric
            and metric["samples"] >= 300
            and metric["win_rate"] > metric["breakeven_mean"]
            and metric["wilson_low"] > metric["breakeven_mean"]
            and q < 0.05
            else "FAIL"
        )
        row["status"] = "CANDIDATE_UNCONFIRMED"
    permutation = _permutation_control(rows)

    lines = [
        "# Binary Lab Report",
        "",
        "## Method and limitations",
        "- Demo-only research; report generation made no trades and changed no UI/trading behavior.",
        "- All records use Deriv demo `proposal` responses at $1 stake for the shortest available intraday contract (15 minutes). CALL and PUT payouts are separate and actual; unavailable proposals are reported N/A.",
        "- The listed 1m/5m rows use an optimized subset of existing indicator-bundle features (ATR, Bollinger, EMA, MACD, RSI, stochastic, ROC, and candle body/side) and direction labels, but evaluate a 15-minute outcome horizon (15 x 1m bars or 3 x 5m bars). Selected feature values match the existing indicator bundle. Provider contract barriers are not historically replayed; win rate is directional accuracy, not a fully simulated contract settlement. Treat all resulting statuses as candidates, not trading authorization.",
        "- The newest 12,000 chronologically ordered candles are used. The final 20% is a locked, never-seen test window. Model selection uses only purged/embargoed folds in the earlier 80%; training labels must end before the cutoff.",
        "- Test entries are spaced by the 15-minute label horizon, so test labels do not overlap one another. Features use the completed candle i and entry is one second after its close; a sample is excluded if its 200-candle feature history or 15-minute label interval contains a timestamp gap.",
        "- Raw p-values are one-sided normal-approximation Poisson-binomial tests against direction-specific proposal breakeven probabilities; BH is applied across all 58 locked pair/timeframe tests.",
        "",
        "## Results",
        "",
        "| Pair | TF | Candles | CV OOS n / win | Locked n / win | Wilson 95% CI | CALL payout / BE | PUT payout / BE | Raw p | BH q | Model | Screen | Status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        metric = row["lockedMetrics"]
        oos = row["oos"]
        call = row["payout"].get("CALL", {})
        put = row["payout"].get("PUT", {})
        if metric:
            win = f"{metric['win_rate']:.2%}"
            ci = f"{metric['wilson_low']:.2%}–{metric['wilson_high']:.2%}"
            locked_count = metric["samples"]
        else:
            win, ci, locked_count = "N/A", "N/A", 0
        cv = f"{oos['samples']} / {oos['winRate']:.2%}" if oos else "0 / N/A"
        call_s = f"{call['payout']:.2f} / {call['breakeven']:.2%}" if call.get("payout") else f"N/A ({call.get('error') or 'no proposal'})"
        put_s = f"{put['payout']:.2f} / {put['breakeven']:.2%}" if put.get("payout") else f"N/A ({put.get('error') or 'no proposal'})"
        lines.append(
            f"| {row['symbol']} | {row['timeframe']} | {row['candles']} | {cv} | "
            f"{locked_count} / {win} | {ci} | {call_s} | {put_s} | "
            f"{row['raw_p']:.6g} | {row['bh_q']:.6g} | {row['model'] or 'none'} | "
            f"{row['statistical_screen']} | {row['status']} |"
        )

    locked_rows = [row for row in rows if row["locked"] is not None]
    leakage_pass = bool(locked_rows) and all(
        row["feature_before_entry"] is True and row["label_overlap"] is False
        for row in locked_rows
    )
    lines.extend([
        "",
        "## Controls",
        f"- **Shuffled-label control:** {permutation['permutations']} refit permutations per eligible record; mean passing records by chance per shuffle: {permutation['mean_pairs_passing_by_chance']:.3f}; maximum in one shuffle: {permutation['max_pairs_passing_in_one_shuffle']}; records passing at least once: {permutation['pairs_with_at_least_one_null_pass']} of 58 (eligible records: {permutation['eligible_records']}).",
        "- Each null iteration refits the record's CV-selected model on shuffled training labels and tests the untouched lockbox with the same breakeven, Wilson, one-sided p-value, and BH gates. The model family selection is frozen from development folds; the lockbox is never used for selection.",
        f"- **Leakage checks:** all {len(locked_rows)} locked rows have each feature timestamp strictly before entry and no training-label horizon overlaps the cutoff: {leakage_pass}; the test begins after the purged cutoff and embargo.",
        "- Contract payout and horizon mismatch: the provider rejected 1m/5m CALL durations for forex. Reported proposals use the shortest available intraday duration, 15m; they do not prove profitability for a hypothetical 1m/5m contract.",
        f"- **EDGE status:** {sum(row['status']=='EDGE' for row in rows)}; all records remain `CANDIDATE_UNCONFIRMED` because their labels are directional candle outcomes, not replayed Deriv contract settlements.",
        "",
    ])
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    for row in rows:
        row["permutation_data"] = None
    return rows, permutation
