import argparse
import asyncio
import json
from pathlib import Path

from .tick_recorder import AccuTickRecorder, TickStore
from .analysis import split_and_analyse, loss_features
from .calibration import DemoTradeCalibration


def write_report(path, symbol_rows, calibration, trade_count, loss_feature_count):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mismatch_rate = calibration.get("mismatch_rate")
    calibration_status = (
        "INSUFFICIENT_DATA (<50 comparable)"
        if calibration.get("compared", 0) < calibration.get("minimum_required", 50)
        else "STOPPED (>2%)" if calibration.get("stop")
        else "PASSED (<=2%)"
    )
    lines = [
        "# ACCU Lab Research Report",
        "",
        "## Scope",
        "- Research-only demo ACCU work.",
        "- No trading behavior or UI behavior is changed.",
        "- Strict chronological 70/30 train/test split; test data is reported.",
        "- INSUFFICIENT_DATA means fewer than 1,404 held-out trades under the stated power-analysis plan; NO_EDGE means adequate sample size but no evidence of a positive edge.",
        "",
        "## Calibration",
        f"- Compared real demo trades: {calibration['compared']}",
        f"- Mismatches: {calibration['mismatches']}",
        f"- Mismatch rate: {mismatch_rate:.2%}" if mismatch_rate is not None else "- Mismatch rate: N/A",
        f"- Stop condition: {calibration_status}",
        "",
        "## Per-symbol test results",
        "",
        "| Symbol | n test trades | Win rate | EV/trade | 95% one-sided CI lower | p-value | BH-adjusted p | Edge |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in symbol_rows:
        lines.append(
            f"| {row['symbol']} | {row['n_trades']} | {row['win_rate']:.2%} | "
            f"{row['ev_per_trade']:.6f} | {row['ci95_low_one_sided']:.6f} | "
            f"{row['p_value']:.6g} | {row['bh_adjusted_p']:.6g} | {row['edge']} |"
        )
    lines.extend([
        "",
        f"- Total held-out trades: {trade_count}",
        f"- Saved simulated-loss feature rows: {loss_feature_count}",
        "",
        "## Method",
        "- Tick barriers and payouts are sourced from Deriv proposal/open-contract fields; missing provider fields fail explicitly.",
        "- Benjamini-Hochberg adjustment is applied across symbols.",
        "- P-values are one-sided tests of EV > 0.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Isolated ACCU lab")
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record")
    record.add_argument("--seconds", type=int, default=None)
    record.add_argument("--db", default=None)

    calibrate = sub.add_parser("calibrate")
    calibrate.add_argument("--minimum", type=int, default=50)
    calibrate.add_argument("--db", default=None)

    report = sub.add_parser("report")
    report.add_argument("--trades", required=True)
    report.add_argument("--losses", default=None)
    report.add_argument("--output", default="docs/ACCU_LAB_REPORT.md")

    args = parser.parse_args()
    if args.command == "record":
        store = TickStore(args.db)
        recorder = AccuTickRecorder(store)
        asyncio.run(recorder.run(args.seconds))
        store.close()
    elif args.command == "calibrate":
        store = TickStore(args.db)
        calibration = DemoTradeCalibration(tick_store=store)
        trades = asyncio.run(calibration.read_demo_accu_trades(args.minimum))
        result = calibration.compare(trades)
        print(json.dumps(result, indent=2))
        store.close()
    else:
        trades = json.loads(Path(args.trades).read_text(encoding="utf-8"))
        rows = split_and_analyse(trades)
        loss_count = 0
        if args.losses:
            loss_count = sum(1 for _ in Path(args.losses).open(encoding="utf-8"))
        write_report(
            args.output,
            rows,
            {"compared": 0, "mismatches": 0, "mismatch_rate": None, "minimum_required": 50, "stop": False},
            sum(row["n_trades"] for row in rows),
            loss_count,
        )


if __name__ == "__main__":
    main()
