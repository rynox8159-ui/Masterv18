"""MasterV17.3 UI smoke test (Playwright).

Usage:  python tests/ui_smoke.py [http://localhost:5173]
Needs the backend + frontend running and `playwright install chromium` done once (setup scripts do it).
Checks: terminal loads, DERIV tab bar renders with live prices, the "+" market picker opens, the live chart
receives candles, the Decision output shows a brain signal or NO_SIGNAL card, and the Auto Trade deck renders.
"""
from __future__ import annotations

import sys
import time
import os
from pathlib import Path

from dotenv import dotenv_values
from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:5173"
BACKEND_DIR = Path(__file__).resolve().parents[1] / "main_source_code" / "backend"
API_KEY = os.environ.get("BACKEND_API_KEY", "").strip() or str(
    dotenv_values(BACKEND_DIR / ".env").get("BACKEND_API_KEY") or ""
).strip()
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            extra_http_headers={"X-API-Key": API_KEY} if API_KEY else {},
        )
        page = context.new_page()
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_timeout(9000)
        check("terminal loaded", page.locator('[data-testid="brand-name"]').first.is_visible())
        check("market mode uses neutral header label", page.locator('[data-testid="market-mode-forex-button"]').inner_text().strip() == "Markets")
        check("signals mode uses neutral header label", page.locator('[data-testid="market-mode-binary-button"]').inner_text().strip() == "Signals")
        check("legacy market names are absent from header", "ACCU" not in page.locator('[data-testid="terminal-header"]').inner_text().upper() and "BINARY" not in page.locator('[data-testid="terminal-header"]').inner_text().upper())
        check("project ZIP button removed from navigation", page.locator('[data-testid="nav-project-zip-button"]').count() == 0)
        page.click('[data-testid="market-mode-binary-button"]')
        page.wait_for_timeout(1000)
        check("signals mode remains selectable", page.locator('[data-testid="market-mode-binary-button"]').get_attribute("aria-pressed") == "true")
        check("signal source is Deriv only", page.locator('[data-testid="signal-source-deriv"]').first.is_visible())
        page.click('[data-testid="add-pair-button"]')
        page.wait_for_timeout(500)
        check("signals picker is 1m Rise/Fall", "1M RISE/FALL" in page.locator('[data-testid="pair-picker-description"]').inner_text().upper())
        check("signals mode excludes synthetic markets", page.locator('[data-testid="pair-picker-r-100"]').count() == 0)
        check("signals timeframe is 1m", "1M" in page.locator('[data-testid="future-signals-meta"]').inner_text().upper())
        check("signal cards usable as fixed-time signals", page.locator('[data-testid="binary-signal-usage-note"]').is_visible())
        page.click('[data-testid="pair-picker-close-button"]')
        page.click('[data-testid="market-mode-forex-button"]')
        page.wait_for_timeout(500)
        page.goto(f"{BASE}/settings", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        check("project ZIP card removed from settings", page.locator('[data-testid="workspace-project-zip"]').count() == 0)
        page.goto(f"{BASE}/trade", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        check("trade header omits legacy market names", "ACCU" not in page.locator('[data-testid="terminal-header"]').inner_text().upper() and "BINARY" not in page.locator('[data-testid="terminal-header"]').inner_text().upper())
        check("trade signals mode remains selectable", page.locator('[data-testid="trade-market-mode-binary-button"]').inner_text().strip() == "Signals")
        page.goto(f"{BASE}/", wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        tabs = page.locator('[data-testid^="pair-tab-"]').count()
        check("multi-pair tab bar", tabs >= 1, f"{tabs} tabs")
        check("engine scope note removed", page.locator('[data-testid="engine-scope-note"]').count() == 0)
        prices = [t for t in page.locator('[data-testid^="pair-price-"]').all_inner_texts() if t.strip() and t.strip() != "—"]
        check("live prices in tabs", len(prices) >= 1, f"{len(prices)} priced")
        page.click('[data-testid="add-pair-button"]')
        page.wait_for_timeout(1500)
        check("DERIV market picker opens", page.locator('[data-testid="pair-picker-dialog"]').is_visible())
        check("all market category is available", page.locator('[data-testid="catalog-category-all"]').is_visible())
        rows = page.locator('[data-testid^="pair-picker-"]:not([data-testid="pair-picker-dialog"]):not([data-testid="pair-picker-title"]):not([data-testid="pair-picker-description"]):not([data-testid="pair-picker-close-button"]):not([data-testid="pair-picker-list"])').count()
        check("picker lists Deriv instruments", rows >= 1, f"{rows} rows")
        page.click('[data-testid="pair-picker-close-button"]')
        page.wait_for_timeout(500)
        legend = page.locator('[data-testid="chart-live-price"], .chart-price, [data-testid="live-chart"]').count()
        check("live chart mounted", page.locator("canvas").count() >= 1, f"{page.locator('canvas').count()} canvases, {legend} legend nodes")
        card = page.locator('[data-testid="signal-output-card"]').first
        check("decision output card", card.count() >= 1, card.inner_text()[:60].replace("\n", " ") if card.count() else "")
        t0 = time.time()
        page.goto(f"{BASE}/autotrade", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        check("auto trade deck", page.locator('[data-testid="autotrade-frame"]').is_visible(), f"{time.time() - t0:.1f}s")
        check("trade type deck", page.locator('[data-testid="trade-type-deck"]').is_visible())
        check("memory-only Deriv credentials", page.locator('[data-testid="deriv-credentials"]').is_visible())
        check("stake deck", page.locator('[data-testid="stake-deck"]').is_visible())
        check("signal source switch", page.locator('[data-testid="source-stream"]').count() == 1)
        check("auto trade covers eligible Deriv signals", "ACTIVE DERIV MARKETS" in page.locator('[data-testid="stream-note"]').inner_text().upper())
        if page.locator('[data-testid="source-brain"]').is_enabled():
            page.click('[data-testid="source-brain"]')
            page.wait_for_timeout(1200)
            check("phase-2 types disabled", page.locator('[data-testid="type-mult"]').is_disabled())
            page.click('[data-testid="source-stream"]')
            page.wait_for_timeout(800)
        browser.close()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
