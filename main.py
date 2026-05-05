import os
import requests
import time
import threading
from datetime import datetime
from flask import Flask

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHECK_INTERVAL = 600

app = Flask(__name__)

@app.route("/")
def ping():
    return "OK", 200

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"[Telegram失败] {e}")

def get_all_symbols():
    r = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=15)
    return sorted([s["symbol"] for s in r.json()["symbols"] if s["quoteAsset"]=="USDT" and s["contractType"]=="PERPETUAL" and s["status"]=="TRADING"])

def get_closed_klines(symbol):
    r = requests.get("https://fapi.binance.com/fapi/v1/klines", params={
        "symbol": symbol, "interval": "4h", "limit": 202
    }, timeout=10)
    data = r.json()
    closed = data[:-1]  # 去掉未收盘的最后一根
    return [float(x[4]) for x in closed]

def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k = 2/(period+1)
    ema = closes[0]
    for v in closes[1:]:
        ema = v*k + ema*(1-k)
    return ema

def calc_ema_series(closes, period):
    # 计算最近两根K线的EMA值
    k = 2/(period+1)
    ema = closes[0]
    for v in closes[1:]:
        ema = v*k + ema*(1-k)
    return ema

def get_last_two_ema(closes, period):
    # 分别计算倒数第二根和最后一根K线时的EMA
    k = 2/(period+1)
    ema = closes[0]
    ema_prev = None
    for i, v in enumerate(closes):
        if i == len(closes) - 1:
            ema_last = v*k + ema*(1-k)
        elif i == len(closes) - 2:
            ema_prev = ema
        ema = v*k + ema*(1-k)
    return ema_prev, ema

def monitor():
    print("币安 USDT永续 4H EMA144 监控启动")
    send_telegram("🤖 <b>监控Bot已启动</b>\n📊 币安全部USDT永续\n⏱ 4H收盘价突破EMA144提醒\n🔄 每10分钟检查一次")
    alerted = set()  # 已提醒的币，等跌破后重置

    while True:
        try:
            now = datetime.now().strftime("%m-%d %H:%M")
            print(f"\n[{now}] 扫描中...")
            symbols = get_all_symbols()
            triggered = []

            for i, sym in enumerate(symbols):
                try:
                    closes = get_closed_klines(sym)
                    if len(closes) < 145:
                        continue

                    # 用倒数第二根K线算EMA（前一根收盘时的状态）
                    ema_prev = calc_ema_series(closes[:-1], 144)
                    # 用最后一根K线算EMA（最新收盘时的状态）
                    ema_last = calc_ema_series(closes, 144)

                    price_prev = closes[-2]  # 前一根收盘价
                    price_last = closes[-1]  # 最新收盘价

                    prev_above = price_prev > ema_prev
                    last_above = price_last > ema_last

                    # 从下方突破上方
                    if not prev_above and last_above:
                        if sym not in alerted:
                            alerted.add(sym)
                            pct = (price_last - ema_last) / ema_last * 100
                            triggered.append({"symbol": sym, "price": price_last, "ema": ema_last, "pct": pct})

                    # 跌回下方，重置
                    if not last_above and sym in alerted:
                        alerted.discard(sym)

                    if i % 20 == 19:
                        time.sleep(0.5)

                except Exception as e:
                    print(f"[{sym}] 错误: {e}")

            if triggered:
                triggered.sort(key=lambda x: x["pct"], reverse=True)
                for b in range(0, len(triggered), 30):
                    batch = triggered[b:b+30]
                    lines = [f"🟢 <b>突破EMA144提醒</b> ({now})\n共{len(triggered)}个币种\n"]
                    for t in batch:
                        lines.append(f"<b>{t['symbol'].replace('USDT','/USDT')}</b>  ${t['price']:.4f}  +{t['pct']:.2f}%")
                    send_telegram("\n".join(lines))
                    time.sleep(1)
                print(f"发送{len(triggered)}个提醒")
            else:
                print("无新突破")

        except Exception as e:
            print(f"错误: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    t = threading.Thread(target=monitor, daemon=True)
    t.start()
    print("Flask server 已启动")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False, use_reloader=False)
