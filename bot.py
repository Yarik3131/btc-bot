import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
import requests
from strategy import fetch_klines, generate_signal, calc_position

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text())

def run_once():
    if CFG.get("paper_mode") is not True:
        raise RuntimeError(
            "Safety stop: this project supports signal/paper mode only; "
            "set paper_mode=true in config.json."
        )
    candles15 = fetch_klines(CFG["symbol"], CFG["category"], CFG["interval_signal"], 300)
    candles1h = fetch_klines(CFG["symbol"], CFG["category"], CFG["interval_trend"], 300)
    sig = generate_signal(candles15, candles1h, CFG)
    price = candles15[-1]["close"]
    print(f'[{datetime.now(timezone.utc).isoformat()}] {CFG["symbol"]} price={price:.2f} signal={sig["signal"]}')
    print(sig["reason"])
    if sig["signal"] in ("LONG", "SHORT"):
        sizing = calc_position(
            balance=CFG["virtual_balance_usdt"],
            entry=price,
            stop=sig["stop"],
            risk_pct=CFG["risk_per_trade"],
            fee_rate=CFG["fee_rate"],
            slippage_rate=CFG["slippage_rate"]
        )
        print("PAPER sizing:", json.dumps(sizing, indent=2))
        print(f'Paper TP: {sig["target"]:.2f}; stop: {sig["stop"]:.2f}')
    else:
        print("No trade. Waiting for a better setup.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        run_once()
        return
    while True:
        try:
            run_once()
        except Exception as e:
            print("ERROR:", repr(e))
        time.sleep(CFG["poll_seconds"])

if __name__ == "__main__":
    main()
