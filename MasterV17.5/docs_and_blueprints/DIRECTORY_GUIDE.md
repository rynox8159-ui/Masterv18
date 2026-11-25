# Directory guide

This is the canonical map for navigating the repository. Source code, runtime
dependencies, local settings, and project documentation have separate roots.

```text
main_source_code/
├── backend/
│   ├── ml/                 # ML adapters and market-ML orchestration
│   ├── dl/                 # Deep-learning models and services
│   ├── ds/                 # Feature engineering, forecasting, research, calibration
│   └── tests/              # Backend test suite
├── frontend/
│   ├── src/                # React application
│   ├── public/             # Static frontend assets
│   └── plugins/            # Vite configuration (*.mts)
├── extensions/
│   └── ws-sniffer/
│       └── playwright/     # Observer source and Windows app build
└── database/               # New schema/migration/seed assets
dependencies/
├── backend/                # requirements.txt and Python virtual environment
├── frontend/               # package.json, lockfiles, node_modules
└── ws-sniffer/             # Isolated Windows executable build requirements
docs_and_blueprints/        # Architecture, plans, stack, deployment, evidence
scripts/                    # Cross-platform setup helpers
```

All local PC setup and run scripts are grouped under `scripts/local_pc/`.

## Where to make common changes

| Change | Start here |
|---|---|
| API routes, application wiring, providers, persistence | `main_source_code/backend/` |
| ML adapters or model orchestration | `main_source_code/backend/ml/` |
| Deep-learning training/inference | `main_source_code/backend/dl/` |
| Features, forecast checks, research, calibration | `main_source_code/backend/ds/` |
| UI, pages, shared components, API clients | `main_source_code/frontend/src/` |
| Mongo/PostgreSQL schema migrations or seed files | `main_source_code/database/` |
| Python packages | `dependencies/backend/requirements.txt` |
| Frontend packages and scripts | `dependencies/frontend/package.json` |
| Local backend settings | `main_source_code/backend/.env` (ignored) |
| Local frontend settings | `main_source_code/frontend/.env` (ignored) |
| Setup, launch, deployment, architecture, or roadmap | `scripts/`, root `*.sh`/`*.bat`, `docs_and_blueprints/` |

## Runtime conventions

- Run setup and launch scripts from the repository root. They invoke the source
  directories and dependency manifests using explicit paths.
- `dependencies/backend/.venv` is the Python virtual environment.
- `dependencies/frontend/node_modules` stores frontend dependencies. Setup
  creates a link at `main_source_code/frontend/node_modules` because Vite and
  TypeScript resolve packages relative to the source tree.
- Vite reads local frontend variables from `main_source_code/frontend/.env`;
  FastAPI loads `main_source_code/backend/.env`.
- The WS sniffer / Playwright observer is under
  `main_source_code/extensions/ws-sniffer/playwright/`; its browser profile is
  stored in the user's local application data when running the Windows bundle.
- Build the Windows bundle with
  `scripts/local_pc/build-ws-sniffer-exe.bat` from Windows. It includes its
  Chromium runtime and is output to the observer's `dist/` directory.

## Backend domain placement

The backend keeps the API composition and infrastructure modules at
`main_source_code/backend/`. Model and analytical implementations are grouped
by responsibility:

- `ml/`: `ml_model_adapters.py`, `market_ml_orchestrator.py`
- `dl/`: `deep_model_service.py`
- `ds/`: feature engineering, behaviour detection, ensemble fusion, signal
  research/calibration, and the `forecasting/` package

Keep future modules in the relevant domain package and import them through
their package-qualified name (for example `from ds.feature_engineering import …`).
