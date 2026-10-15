import json
from pathlib import Path
from strategy import fetch_klines, generate_signal, calc_position

ROOT=Path(__file__).resolve().parent
CFG=json.loads((ROOT/"config.json").read_text())

def main():
    c15=fetch_klines(CFG["symbol"],CFG["category"],CFG["interval_signal"],1000)
    c1h=fetch_klines(CFG["symbol"],CFG["category"],CFG["interval_trend"],1000)
    print("Downloaded candles:",len(c15),"15m and",len(c1h),"1h")
    print("NOTE: This smoke test validates data/indicator flow; it is NOT a full historical PnL backtest.")
    print("Current signal:",generate_signal(c15,c1h,CFG))
    print("For reliable backtesting, use timestamp-aligned walk-forward data, fees, slippage, funding,")
    print("intrabar stop/target ordering, and avoid using the still-open candle.")
if __name__=="__main__": main()
