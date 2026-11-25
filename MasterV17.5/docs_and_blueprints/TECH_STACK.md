# Technology stack

## Application

| Area | Technology | Configuration/source |
|---|---|---|
| Backend API | Python, FastAPI, Uvicorn, Pydantic | `main_source_code/backend/`, `dependencies/backend/requirements.txt` |
| Frontend | TypeScript, React, Vite | `main_source_code/frontend/`, `dependencies/frontend/package.json` |
| Backend tests | pytest | `main_source_code/backend/tests/` |
| Frontend tests | Vitest | Frontend package scripts and `*.test.ts`/`*.test.tsx` files |

## Data and analytics

- MongoDB is the primary persistence layer, accessed through Motor/PyMongo.
- PostgreSQL/asyncpg and pgvector are optional for mirroring and analytics.
- Redis is an optional cache/coordination service.
- NumPy, Pandas, scikit-learn, PyTorch, XGBoost, LightGBM, CatBoost, and `ta`
  support analytics and modeling.
- Deriv is the live market-data provider. Browser observations are separately
  provenance-tagged and subject to cross-validation rules.

## Runtime requirements

- Python 3.10–3.12
- Node.js 20 or newer
- MongoDB 6 or newer for local runtime
- Optional services: Redis and PostgreSQL with pgvector

## Dependency and environment files

- Python requirements: `dependencies/backend/requirements.txt`
- Frontend package manifest: `dependencies/frontend/package.json`
- Frontend package lock: `dependencies/frontend/yarn.lock`
- A repository-level legacy lockfile is preserved at `dependencies/yarn.lock`;
  the app-specific lockfile is the one under `dependencies/frontend/`.
- Environment templates: `main_source_code/backend/.env.example` and
  `main_source_code/frontend/.env.example`
- Private local environment files: `main_source_code/backend/.env` and
  `main_source_code/frontend/.env` (ignored by Git)

Install or update dependencies through the setup scripts so the virtualenv and
frontend module link remain in the expected locations.
