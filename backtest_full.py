import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from strategy import generate_signal, calc_position

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text())
URL = "https://api.bybit.com/v5/market/kline"
DAYS = 90
INTERVAL_MS = {"15": 15 * 60_000, "60": 60 * 60_000}


def fetch_history(interval, days):
    """Download historical candles using the public Bybit API."""
    step = INTERVAL_MS[interval]
    now = int(time.time() * 1000)
    cutoff = now - days * 86_400_000
    cursor = now
    candles = {}
    session = requests.Session()

    while cursor > cutoff:
        response = session.get(URL, params={
            "category": CFG["category"],
            "symbol": CFG["symbol"],
            "interval": interval,
            "limit": 1000,
            "end": cursor,
        }, timeout=20)
        response.raise_for_status()
        data = response.json()

        if data.get("retCode") != 0:
            raise RuntimeError(data.get("retMsg", "Bybit API error"))

        rows = data.get("result", {}).get("list", [])
        if not rows:
            break

        for row in rows:
            t = int(row[0])
            if cutoff <= t and t + step <= now:
                candles[t] = {
                    "time": t,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }

        oldest = min(int(row[0]) for row in rows)
        if oldest >= cursor or oldest <= cutoff:
            break
        cursor = oldest - 1
        time.sleep(0.12)

    return [candles[t] for t in sorted(candles)]


def run_backtest(c15, c1h):
    balance = float(CFG["virtual_balance_usdt"])
    initial = balance
    risk_pct = float(CFG["risk_per_trade"])
    fee = float(CFG["fee_rate"])
    slip = float(CFG["slippage_rate"])
    atr_mult = float(CFG["atr_stop_multiple"])
    rr = float(CFG["reward_risk"])
    max_daily_loss = float(CFG["max_daily_loss_pct"])
    max_losses = int(CFG["max_consecutive_losses"])

    trades = []
    daily_start = {}
    daily_pnl = {}
    daily_losses = {}
    consecutive_losses = 0
    trend_end = 0
    i = 0

    while i < len(c15) - 1:
        signal_candle = c15[i]
        signal_time = signal_candle["time"] + INTERVAL_MS["15"]
        day = datetime.fromtimestamp(
            signal_time / 1000, timezone.utc
        ).date().isoformat()

        while (trend_end < len(c1h) and
               c1h[trend_end]["time"] + INTERVAL_MS["60"] <= signal_time):
            trend_end += 1
        closed_trend = c1h[:trend_end]

        if day not in daily_start:
            daily_start[day] = balance
            daily_pnl[day] = 0.0
            daily_losses[day] = 0

        if (len(c15[:i+1]) < 220 or len(closed_trend) < 220 or
            daily_pnl[day] <= -daily_start[day] * max_daily_loss or
            consecutive_losses >= max_losses):
            i += 1
            continue

        signal = generate_signal(c15[:i+1], closed_trend, CFG)
        side = signal.get("signal")

        if side not in ("LONG", "SHORT"):
            i += 1
            continue

        # Signal is known only at candle close. Enter next candle's open.
        entry_bar = c15[i+1]
        entry = entry_bar["open"]
        atr_value = (signal_candle["close"] - signal["stop"])
        if side == "SHORT":
            atr_value = signal["stop"] - signal_candle["close"]

        risk_distance = abs(atr_value)
        if risk_distance <= 0 or entry <= 0:
            i += 1
            continue

        stop = entry - risk_distance if side == "LONG" else entry + risk_distance
        target = (entry + rr * risk_distance if side == "LONG"
                  else entry - rr * risk_distance)

        pos = calc_position(
            balance, entry, stop, risk_pct, fee, slip
        )
        # Enforce Bybit BTCUSDT quantity step and minimum.
        raw_qty = pos.get("qty_btc", 0)
        qty_step = 0.001
        min_qty = 0.001
        qty = int(raw_qty / qty_step) * qty_step

        # Never increase size beyond the configured risk budget.
        # If the minimum exchange quantity is too risky, skip the trade.
        if qty < min_qty:
            i += 1
            continue

        exit_price = None
        exit_reason = None
        exit_index = i + 1

        for j in range(i+1, len(c15)):
            bar = c15[j]

            # Conservative treatment of gaps and candles touching both levels.
            if side == "LONG":
                if bar["open"] <= stop:
                    exit_price, exit_reason = bar["open"], "STOP_GAP"
                elif bar["open"] >= target:
                    exit_price, exit_reason = target, "TARGET_GAP"
                elif bar["low"] <= stop:
                    exit_price, exit_reason = stop, "STOP"
                elif bar["high"] >= target:
                    exit_price, exit_reason = target, "TARGET"
            else:
                if bar["open"] >= stop:
                    exit_price, exit_reason = bar["open"], "STOP_GAP"
                elif bar["open"] <= target:
                    exit_price, exit_reason = target, "TARGET_GAP"
                elif bar["high"] >= stop:
                    exit_price, exit_reason = stop, "STOP"
                elif bar["low"] <= target:
                    exit_price, exit_reason = target, "TARGET"

            if exit_price is not None:
                exit_index = j
                break

        if exit_price is None:
            # Close any remaining position at the final available close.
            exit_index = len(c15) - 1
            exit_price = c15[exit_index]["close"]
            exit_reason = "END_OF_DATA"

        direction = 1 if side == "LONG" else -1
        gross = qty * (exit_price - entry) * direction

        # Approximate market-order slippage on both sides.
        slip_cost = qty * (entry + exit_price) * slip
        fees = qty * (entry + exit_price) * fee
        net = gross - slip_cost - fees
        balance += net
        daily_pnl[day] += net

        if net < 0:
            consecutive_losses += 1
            daily_losses[day] += 1
        else:
            consecutive_losses = 0

        trades.append({
            "side": side,
            "entry_time": datetime.fromtimestamp(
                entry_bar["time"]/1000, timezone.utc
            ).isoformat(),
            "exit_time": datetime.fromtimestamp(
                c15[exit_index]["time"]/1000, timezone.utc
            ).isoformat(),
            "entry": entry,
            "exit": exit_price,
            "reason": exit_reason,
            "qty": qty,
            "gross": gross,
            "fees": fees,
            "slippage": slip_cost,
            "net": net,
            "balance": balance,
        })

        i = exit_index + 1

    return initial, balance, trades


def main():
    print(f"Downloading {DAYS} days of BTCUSDT candles...")
    c15 = fetch_history(CFG["interval_signal"], DAYS)
    c1h = fetch_history(CFG["interval_trend"], DAYS)

    print(f"Closed candles: {len(c15)} x 15m, {len(c1h)} x 1h")
    if len(c15) < 300 or len(c1h) < 220:
        raise RuntimeError("Not enough historical candles for a meaningful test.")

    initial, final, trades = run_backtest(c15, c1h)
    wins = sum(t["net"] > 0 for t in trades)
    losses = sum(t["net"] < 0 for t in trades)
    gross_profit = sum(t["net"] for t in trades if t["net"] > 0)
    gross_loss = -sum(t["net"] for t in trades if t["net"] < 0)

    peak = initial
    max_dd = 0.0
    for t in trades:
        peak = max(peak, t["balance"])
        if peak > 0:
            max_dd = max(max_dd, (peak - t["balance"]) / peak * 100)

    print("\n=== HISTORICAL SIMULATION (NOT LIVE TRADING) ===")
    print(f"Initial balance: {initial:.4f} USDT")
    print(f"Final balance:   {final:.4f} USDT")
    print(f"Net PnL:         {final-initial:+.4f} USDT")
    print(f"Return:          {(final/initial-1)*100:+.2f}%")
    print(f"Trades:          {len(trades)} | Wins: {wins} | Losses: {losses}")
    print(f"Win rate:        {wins/len(trades)*100:.1f}%" if trades else "Win rate: N/A")
    print(f"Gross profit / gross loss: {gross_profit:.4f} / {gross_loss:.4f} USDT")
    print(f"Max drawdown:    {max_dd:.2f}%")
    print("Funding, exchange minimums, liquidation and exact intrabar paths are not modeled.")

    report = ROOT / "backtest_trades.csv"
    import csv
    fields = list(trades[0].keys()) if trades else ["side", "entry_time", "exit_time", "net"]
    with report.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(trades)
    print(f"Trade report: {report}")


if __name__ == "__main__":
    main()
