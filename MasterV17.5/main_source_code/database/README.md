# Database assets

Place versioned database schemas, migrations, and seed data in the matching
subdirectory:

- `schemas/` — documented collection/table structures and indexes
- `migrations/` — ordered, repeatable schema/data migrations
- `seeds/` — non-secret reference or development seed data

The current application does not have checked-in database migration files:

- MongoDB collections and indexes are initialized by `main_source_code/backend/market_store.py` and related services.
- PostgreSQL tables and optional vector extensions are created by `main_source_code/backend/postgres_service.py`.
- MongoDB is the primary store; PostgreSQL is optional.

Add a migration under `migrations/` when a schema change needs an explicit,
repeatable rollout.
