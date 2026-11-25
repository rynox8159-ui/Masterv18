#!/usr/bin/env bash
# Opens YOUR Google Chrome / Chromium with remote debugging on port 9222 and its own profile folder.
# Log in to Quotex manually in this window, then run start-sniffer.sh (or /sniffer -> "Start WS sniffer").
PORT="${PORT:-9222}"
PROFILE="${QX_CHROME_PROFILE_DIR:-$HOME/.master-candle/qx-chrome-profile}"
mkdir -p "$PROFILE"
for candidate in google-chrome google-chrome-stable chromium chromium-browser "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" microsoft-edge; do
  if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then CHROME="$candidate"; break; fi
done
[ -n "$CHROME" ] || { echo "[X] Google Chrome / Chromium not found. Install Chrome: https://www.google.com/chrome"; exit 1; }
echo "[OK] browser: $CHROME"
"$CHROME" --remote-debugging-port="$PORT" --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check https://market-qx.info/en/trade >/dev/null 2>&1 &
echo
echo "Chrome is open with remote debugging on port $PORT (profile: $PROFILE)."
echo " 1) Log in to Quotex in that Chrome window (manual login, captcha / 2FA is fine - the login is remembered)."
echo " 2) Then run scripts/local_pc/start-sniffer.sh or press 'Start WS sniffer' on the /sniffer page."
echo "    The sniffer only reads the WebSocket feed of that tab - it never places trades on Quotex."
