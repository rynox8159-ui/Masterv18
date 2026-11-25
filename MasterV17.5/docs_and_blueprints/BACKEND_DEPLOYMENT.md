# Local and hosted deployment

This repository's backend is a Python/FastAPI app. In Railway, create a service
from this repository and set its **Root Directory** to the repository root.
Install dependencies from `dependencies/backend/requirements.txt`, then start
Uvicorn with `--app-dir main_source_code/backend`. The backend deployment
configuration is in `main_source_code/backend/railway.json`.

The pinned backend requirements include native XGBoost, LightGBM, and CatBoost
estimators. Install or update the complete environment with
`python -m pip install -r dependencies/backend/requirements.txt`. If a required native estimator
cannot be imported, its training run reports a failure rather than silently
substituting another model.

Add these required values under the service's **Variables** in Railway:

| Variable | Value |
|---|---|
| `MONGO_URL` | MongoDB connection URI, including credentials if authentication is enabled |
| `DB_NAME` | Name of the application database |

Keep the URI in Railway's Variables (or another secret manager), not in source
code, `railway.json`, or a committed `.env` file. Railway environment variables
are provided to the app at runtime; no MongoDB credentials need to be pushed to
GitHub.

For local development, copy `main_source_code/backend/.env.example` to
`main_source_code/backend/.env` and edit the local file. Local `.env` files are
intentionally ignored by Git. Railway cannot use
`127.0.0.1` to reach MongoDB on a developer's computer; use a hosted MongoDB
service or a separately secured, stable, publicly reachable MongoDB endpoint.
Do not expose MongoDB publicly without authentication and network restrictions.

## Run locally on Windows

Install Python 3.12, Node.js 20+, and MongoDB Community Server. Start the local
MongoDB service. From the repository root, run this
once in PowerShell to create the backend virtual environment, install the
pinned backend and frontend dependencies, and create local environment files
if missing:

```powershell
.\scripts\local_pc\setup-local.ps1
```

Start the backend in one PowerShell window:

```powershell
Set-Location (Resolve-Path .)
.\dependencies\backend\.venv\Scripts\python.exe -m uvicorn --app-dir .\main_source_code\backend server:app --host 127.0.0.1 --port 7007
```

Start the frontend in a second window:

```powershell
Set-Location (Resolve-Path .)
npm --prefix .\dependencies\frontend run dev
```

Open `http://127.0.0.1:5173`. Verify the backend and training workers:

```powershell
Invoke-RestMethod http://127.0.0.1:7007/api/health
$ml = Invoke-RestMethod http://127.0.0.1:7007/api/v1/ml/status
$ml.trainingWorkers | ConvertTo-Json -Depth 6
$ml.deepModels | Select-Object readyMarketCount,paperReadyMarketCount,activelyTraining
```

The local settings keep signal evaluation to two concurrent markets and train
one deep-model market at a time. Bootstrap scans for the next eligible market
every 60 seconds. Each deep-model run trains an LSTM, GRU, Transformer Encoder,
and 1D CNN, then calibrates and promotes the probability-averaged ensemble using
the same purged chronological out-of-sample gates. This increases CPU training
time compared with the former single-LSTM run.
The scheduled model refresh remains capped at one market per six-hour cycle.
The 500 strategy profiles are deterministic parameter combinations evaluated
as a pool, not 500 parallel neural-network workers. They are never allowed to
edit source code or bypass chronological out-of-sample validation.
The seven advisory Step 8 adapters train from at least 260 closed provider
candles. If bootstrap starts them before enough candles are stored, they retry
after deep-model history backfill. They also retrain from verified stored
candles after a backend restart (their weights are process-local); their
trained status does not promote them for live signals. Untrained adapters are
excluded from ensemble predictions rather than contributing random
initialization outputs.

To inspect a market's chronological 500-profile backtest without promoting a
profile, use the backend API:

```powershell
Invoke-RestMethod 'http://127.0.0.1:7007/api/v1/signals/research?source=deriv&symbol=1HZ100V&timeframe=1m'
```

No training run, `PAPER_READY` model, or `LIVE_READY` model is guaranteed. Check
`/api/v1/ml/status` and the stored training report; do not interpret a running
worker or a high-confidence rule candidate as proof of predictive accuracy.

## Deriv pair selection and binary-option data

The backend discovers the current Deriv instrument catalog, then filters out
OTC, suspended, unsupported synthetic, and non-metal commodity instruments.
`DERIV_SYMBOLS` can be `ALL` (the default) or a comma-separated exact-symbol
allowlist. Exchange-closed pairs are catalogued but are not subscribed for live
ticks.

`GET /api/v1/instruments` returns only instruments currently marked open and
adds `isOpen: true` to each returned item. Crypto and synthetic indices are
treated as 24/7 markets; Forex follows the conventional Sunday 22:00 UTC
opening and Friday 22:00 UTC closing, further restricted by the provider's
availability flag when present. Other exchange instruments are hidden on
weekends and respect provider availability. The Forex clock is a conventional
UTC schedule; the provider's closed status also suppresses holiday closures.
The main Forex/Binary selector renders only items explicitly returned as open.

Continuous 1m/5m/15m provider OHLC streams and automatic signal evaluation
cover all active, supported, non-OTC Deriv instruments by default. Forex pairs
are subscribed first, followed by crypto, indices, and metals. Candle
subscriptions are paced and their acknowledgements/rejections are tracked
while the feed continues receiving data.

Set `DERIV_CANDLE_SYMBOLS` to a comma-separated list of eligible symbols to
choose a smaller focus, or `ALL` for all active eligible instruments.
`DERIV_MAX_CANDLE_SYMBOLS` caps the selection (default 20, maximum 50), but
the selected symbols are also reduced to keep the combined number of tick and
candle subscriptions below the provider connection limit. With 89 tick symbols,
only two symbols can receive all three continuous candle streams. If
`DERIV_SYMBOLS` is an explicit allowlist and none of the configured candle
symbols are selected, its selected pairs become the signal focus. The active
selection and candle-stream rejection counts are reported by
`/api/v1/runtime` and `/api/v1/providers/status`.

Only closed, validated provider OHLC candles are used to train/promote sequence
models. Missing market history, closed sessions, model calibration, and
out-of-sample gates can legitimately result in `NO_SIGNAL`; do not lower these
gates just to force a binary-options signal. PAPER models remain shadow-only,
and a model passing one historical test is not a guarantee of future accuracy.

## Verify runtime connections

- `GET /api/health` should return HTTP 200 with `status: "ok"` and
  `database: "connected"`.
- `GET /api/v1/runtime` reports the Deriv provider state. After startup it
  should reach `DATA_RECEIVING` and `ticksReceived` should increase.
- Redis is optional; when unavailable, the backend falls back to a
  process-local cache. That fallback is not shared between multiple service
  replicas.

Market analysis stores a latest snapshot in `market_analyses` and one
deduplicated, per-closed-candle record in `market_intelligence_history`. These
records include a bounded rolling-memory window, prior analysis summaries,
deterministic market behaviour, computed technical-indicator status, tags, and
observed price gaps. They are analytical evidence, not training labels or
predictions. A raw analysis candidate is not promoted to a signal unless the
separate validated signal pipeline qualifies it.

The Binary selector currently limits markets to regular Forex instruments in
the connected Deriv feed. Pair labels are the provider's actual instrument
labels; they do not establish that an asset is available on Quotex or another
broker. OTC/browser-observed assets remain observation-only unless their data
passes the cross-validation gate.

## Frontend integration status

The editable frontend source is in `main_source_code/frontend/src`, with its
Vite/React build configuration in `main_source_code/frontend` and its package
manifest/dependencies in `dependencies/frontend`. Production output is written
to `main_source_code/frontend/dist`. A small Node/Express service in
`main_source_code/frontend/server.cjs` serves
that build and forwards `/api/...` requests to the backend, including streaming
and WebSocket upgrades. It does not require browser CORS configuration.

To deploy the frontend separately on Railway:

1. Create a second service from this repository and set its **Root Directory**
   to the repository root. Set its Railway config file path to
   `/main_source_code/frontend/railway.json` and use the frontend package
   scripts under `dependencies/frontend`.
2. Set the frontend service variable `BACKEND_URL` to the backend service's
   public origin, for example `https://<backend-domain>` (no `/api` suffix).
   Do not point it at `localhost`; each Railway service runs in its own
   container. Keep the actual domain in Railway Variables rather than source.
3. Deploy the frontend service. Its `/healthz` endpoint should return HTTP 200;
   the site is served from the same origin and its `/api/...` calls are proxied
   to the backend.

The backend must already have a successful Railway deployment for the proxy
to work. A proxy 502 means the backend URL is unreachable or the backend is
not healthy; it does not indicate a MongoDB credential issue by itself.

If the Railway health URL returns a gateway error instead of the response
above, inspect that service's deployment/build logs and verify its runtime
`MONGO_URL` and `DB_NAME` variables. A successful local connection does not
prove Railway can reach the database.

## ML training and status

`GET /api/v1/ml/status` reports loaded and actively training models, recent
training sample counts and timestamps, the online-learning queue, and bootstrap
progress. By default, promoted or previously evaluated markets are refreshed
every six hours (up to 20 due markets per pass), while model bootstrap checks
run every 60 seconds. These intervals can be adjusted with
`DEEP_MODEL_REFRESH_INTERVAL_SECONDS`, `DEEP_MODEL_REFRESH_MAX_MARKETS`, and
`DEEP_MODEL_BOOTSTRAP_INTERVAL_SECONDS`.

PAPER models are shadow-only and cannot promote signals to LIVE or enable
execution. LIVE promotion still requires positive out-of-sample expected value,
the independent calibration check, and the live probability threshold.
`executionEnabled` remains false.
