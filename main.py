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
    # 取201根，最后一根是当前未收盘K线，倒数第二根才是最新收盘K线
    r = requests.get("https://fapi.binance.com/fapi/v1/klines", params={
        "symbol": symbol,
        "interval": "4h",
        "limit": 201
    }, timeout=10)
    data = r.json()
    # 去掉最后一根未收盘K线，只用已收盘的
    closed = data[:-1]
    return [float(x[4]) for x in closed]

def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k = 2/(period+1)
    ema = closes[0]
    for v in closes[1:]:
        ema = v*k + ema*(1-k)
    return ema

def monitor():
    print("币安 USDT永续 4H EMA144 监控启动（收盘价判断）")
    send_telegram("🤖 <b>监控Bot已启动</b>\n📊 币安全部USDT永续\n⏱ 4小时K线收盘价 EMA144以上提醒\n🔄 每10分钟检查一次")
    
    # 记录上一次每个币的状态（True=上方 False=下方）
    prev_state = {}
    first_run = True
    
    while True:
        try:
            now = datetime.now().strftime("%m-%d %H:%M")
            print(f"\n[{now}] 扫描中...")
            symbols = get_all_symbols()
            triggered = []
            
            for i, sym in enumerate(symbols):
                try:
                    closes = get_closed_klines(sym)
                    if len(closes) < 144:
                        continue
                    ema144 = calc_ema(closes, 144)
                    if not ema144:
                        continue
                    
                    # 用最新已收盘K线的收盘价判断
                    price = closes[-1]
                    above = price > ema144
                    prev = prev_state.get(sym)
                    
                    if first_run:
                        # 第一次运行：记录当前状态，不发提醒
                        prev_state[sym] = above
                    else:
                        # 之后：只有从下方变到上方才提醒
                        if above and prev == False:
                            prev_state[sym] = True
                            pct = (price - ema144) / ema144 * 100
                            triggered.append({"symbol": sym, "price": price, "ema": ema144, "pct": pct})
                        elif not above and prev == True:
                            prev_state[sym] = False
                        elif prev is None:
                            prev_state[sym] = above
                    
                    if i % 20 == 19:
                        time.sleep(0.5)
                        
                except Exception as e:
                    print(f"[{sym}] 错误: {e}")
            
            if first_run:
                above_count = sum(1 for v in prev_state.values() if v)
                print(f"首次扫描完成，{above_count}个币在EMA144上方，开始监控突破")
                send_telegram(f"📊 首次扫描完成\n当前 <b>{above_count}</b> 个币在EMA144上方\n⏳ 开始监控新的突破...")
                first_run = False
            elif triggered:
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
