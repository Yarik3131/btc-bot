import requests, math, time

BASE_URL = "https://api.bybit.com"


def interval_to_ms(interval):
    """Convert a Bybit candle interval to milliseconds."""
    value = str(interval).strip().upper()
    if value.isdigit():
        minutes = int(value)
        if minutes <= 0:
            raise ValueError("Candle interval must be positive")
        return minutes * 60_000
    units = {"D": 86_400_000, "W": 604_800_000}
    if value in units:
        return units[value]
    raise ValueError(f"Unsupported candle interval: {interval}")


def filter_closed_candles(candles, interval_ms, now_ms=None):
    """Keep candles whose closing time has already passed."""
    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    return [
        c for c in candles
        if int(c["time"]) + interval_ms <= now_ms
    ]

def fetch_klines(symbol, category, interval, limit=300):
    url = BASE_URL + "/v5/market/kline"
    r = requests.get(url, params={
        "category": category, "symbol": symbol, "interval": interval, "limit": limit
    }, timeout=15)
    r.raise_for_status()
    payload = r.json()
    if payload.get("retCode") != 0:
        raise RuntimeError(payload.get("retMsg", "Bybit API error"))
    # Bybit returns newest first; strategy needs oldest first.
    rows = list(reversed(payload["result"]["list"]))
    candles = [{
        "time": int(x[0]), "open": float(x[1]), "high": float(x[2]),
        "low": float(x[3]), "close": float(x[4]), "volume": float(x[5])
    } for x in rows]
    return filter_closed_candles(candles, interval_to_ms(interval))

def ema(values, period):
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return [None] * (period - 1) + out

def rsi(values, period=14):
    if len(values) <= period:
        return [None] * len(values)
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    result = [None] * period
    result.append(100 if avg_l == 0 else 100 - 100/(1+avg_g/avg_l))
    for i in range(period, len(gains)):
        avg_g = (avg_g*(period-1)+gains[i])/period
        avg_l = (avg_l*(period-1)+losses[i])/period
        result.append(100 if avg_l == 0 else 100-100/(1+avg_g/avg_l))
    return result[:len(values)]

def atr(candles, period=14):
    trs = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c["high"]-c["low"])
        else:
            prev = candles[i-1]["close"]
            trs.append(max(c["high"]-c["low"], abs(c["high"]-prev), abs(c["low"]-prev)))
    if len(trs) < period:
        return [None] * len(trs)
    out = [None] * (period-1)
    avg = sum(trs[:period])/period
    out.append(avg)
    for tr in trs[period:]:
        avg = (avg*(period-1)+tr)/period
        out.append(avg)
    return out

def generate_signal(c15, c1h, cfg):
    closes = [c["close"] for c in c15]
    trend_closes = [c["close"] for c in c1h]
    e50, e200 = ema(trend_closes, 50), ema(trend_closes, 200)
    r = rsi(closes, 14)
    a = atr(c15, 14)
    i = len(c15)-1
    if len(c15) < 220 or len(c1h) < 220 or e200[-1] is None or r[-1] is None or a[-1] is None:
        return {"signal":"NONE", "reason":"Недостаточно свечей для расчёта индикаторов."}
    trend_up = e50[-1] > e200[-1] and trend_closes[-1] > e200[-1]
    trend_down = e50[-1] < e200[-1] and trend_closes[-1] < e200[-1]
    # Conservative momentum filter: avoid buying overbought / shorting oversold.
    if trend_up and closes[i] > closes[i-1] and 50 <= r[-1] <= 68:
        stop = closes[i] - cfg["atr_stop_multiple"] * a[i]
        target = closes[i] + cfg["reward_risk"] * (closes[i]-stop)
        return {"signal":"LONG","reason":f"Часовой тренд вверх; RSI={r[-1]:.1f}; ATR={a[i]:.2f}",
                "stop":stop,"target":target}
    if trend_down and closes[i] < closes[i-1] and 32 <= r[-1] <= 50:
        stop = closes[i] + cfg["atr_stop_multiple"] * a[i]
        target = closes[i] - cfg["reward_risk"] * (stop-closes[i])
        return {"signal":"SHORT","reason":f"Часовой тренд вниз; RSI={r[-1]:.1f}; ATR={a[i]:.2f}",
                "stop":stop,"target":target}
    return {"signal":"NONE","reason":f"Нет полного набора условий; RSI={r[-1]:.1f}; EMA50/200 trend filter active."}

def calc_position(balance, entry, stop, risk_pct, fee_rate, slippage_rate):
    distance = abs(entry-stop)
    if distance <= 0 or entry <= 0:
        return {"error":"Некорректная цена входа или стопа"}
    risk_budget = balance * risk_pct
    # Approximate round-trip fees/slippage as a fraction of notional.
    cost_per_btc = distance + entry * (2*fee_rate + 2*slippage_rate)
    qty = risk_budget / cost_per_btc
    notional = qty * entry
    return {
        "risk_budget_usdt": round(risk_budget, 4),
        "qty_btc": round(qty, 6),
        "notional_usdt": round(notional, 4),
        "stop_distance_usdt_per_btc": round(distance, 4),
        "estimated_round_trip_cost_usdt": round(notional*(2*fee_rate+2*slippage_rate), 4),
        "warning": "Simulation only; excludes funding, gaps, exchange minimums and exact fee tier."
    }
