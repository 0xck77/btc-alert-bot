import os
import requests
import time
import threading
from datetime import datetime
from flask import Flask

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
CHECK_INTERVAL = 3600

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

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
    r = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo",
                     headers=HEADERS, timeout=15)
    data = r.json()
    print(f"API响应keys: {list(data.keys())}")
    symbols = []
    for s in data.get("symbols", []):
        try:
            if s.get("quoteAsset")=="USDT" and s.get("contractType")=="PERPETUAL" and s.get("status")=="TRADING":
                symbols.append(s["symbol"])
        except:
            pass
    print(f"找到{len(symbols)}个币种")
    return sorted(symbols)

def get_data(symbol):
    r = requests.get("https://fapi.binance.com/fapi/v1/klines",
                     headers=HEADERS,
                     params={"symbol": symbol, "interval": "4h", "limit": 202},
                     timeout=10)
    data = r.json()
    if not isinstance(data, list):
        raise Exception(f"K线数据异常: {data}")
    closed = data[:-1]
    return [float(x[4]) for x in closed]

def calc_ema_list(closes, period):
    k = 2/(period+1)
    ema_list = [closes[0]]
    for v in closes[1:]:
        ema_list.append(v * k + ema_list[-1] * (1-k))
    return ema_list

def monitor():
    print("币安 USDT永续 4H EMA144 监控启动")
    send_telegram("🤖 <b>监控Bot已启动</b>\n📊 币安全部USDT永续\n⏱ 4H收盘价突破EMA144提醒\n🔄 每小时检查一次")
    alerted = set()

    while True:
        try:
            now = datetime.now().strftime("%m-%d %H:%M")
            print(f"\n[{now}] 扫描中...")
            symbols = get_all_symbols()
            if not symbols:
                print("获取币种列表失败，等待下次")
                time.sleep(CHECK_INTERVAL)
                continue

            triggered = []
            for i, sym in enumerate(symbols):
                try:
                    closes = get_data(sym)
                    if len(closes) < 150:
                        continue
                    ema_list = calc_ema_list(closes, 144)
                    price_now  = closes[-1]
                    ema_now    = ema_list[-1]
                    price_prev = closes[-2]
                    ema_prev   = ema_list[-2]
                    above_now  = price_now  > ema_now
                    above_prev = price_prev > ema_prev

                    if above_now and not above_prev:
                        if sym not in alerted:
                            alerted.add(sym)
                            pct = (price_now - ema_now) / ema_now * 100
                            triggered.append({"symbol": sym, "price": price_now, "ema": ema_now, "pct": pct})
                    elif not above_now:
                        alerted.discard(sym)

                    time.sleep(1)

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
            print(f"主循环错误: {e}")

        print(f"等待1小时后再次扫描...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    t = threading.Thread(target=monitor, daemon=True)
    t.start()
    print("Flask server 已启动")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False, use_reloader=False)
