# Frontend

The Vite/React app source and build configuration live in
`main_source_code/frontend/`; its package manifest and lockfile are in
`dependencies/frontend/`. Local configuration is loaded from
`main_source_code/frontend/.env`.

From the repository root, install dependencies with one of the setup scripts,
then start the local development server:

```sh
npm --prefix dependencies/frontend run dev
```

Open `http://localhost:5173`. When no `VITE_BACKEND_URL` is configured, Vite
proxies `/api` requests to the local backend. Set `VITE_BACKEND_URL` in
`main_source_code/frontend/.env` to use a different backend.

Run `npm --prefix dependencies/frontend run build` to type-check and create the
production build in `main_source_code/frontend/dist`. The production Node
server requires `BACKEND_URL` to proxy API requests. For Docker, build from the
repository root with:

```sh
docker build -f main_source_code/frontend/Dockerfile .
```
