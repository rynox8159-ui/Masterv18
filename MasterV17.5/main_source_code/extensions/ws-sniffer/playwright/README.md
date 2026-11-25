# WS Sniffer Windows app

The Windows bundle starts the Playwright observer without requiring Python on
the computer that runs it. Its Chromium browser is included in the bundle; the
first build therefore downloads a large browser runtime.

## Build on Windows

From the repository root, run:

```bat
scripts\local_pc\build-ws-sniffer-exe.bat
```

The build requires Python 3.10–3.12 and an internet connection. It creates:

- `dist\ws-sniffer\ws-sniffer.exe` and its private runtime files
- `dist\ws-sniffer\ms-playwright\` with Chromium
- `dist\ws-sniffer-windows.zip` as a portable bundle

Copy the whole `ws-sniffer` folder (or extract the ZIP) together; the executable
is not standalone from its bundled runtime and browser directory.

## Run and configure

Copy `.env.example` next to `ws-sniffer.exe` as `.env`. The default backend is
`http://127.0.0.1:7007`, so the backend must be running on the same PC. For a
backend on another machine, set `OBSERVER_BACKEND_URL` to its reachable URL.
When using a custom backend API key, set matching `BACKEND_API_KEY` values in
the observer `.env` and backend `.env` files.

Double-click `ws-sniffer.exe` to start the observer. Its console shows connection
and data status. Log in to Quotex in the opened Chromium window if prompted.
The persistent login profile is stored under
`%LOCALAPPDATA%\MasterV12\ws-sniffer\profile`; it is separate from the app and
should not be shared because it can contain session credentials.

When this bundle is built in the project at the documented `dist` path, the
backend provider controls use the executable automatically. Otherwise they
continue to launch the Python script in the repository's configured Python
environment.
