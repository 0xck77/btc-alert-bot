“””
BTC/USDC 永续合约 BOLL(29,2) 警报机器人

- 每 10 分钟检查一次
- 触碰上轨/下轨：前60秒每2秒发一条消息，之后每30秒一条
- 点「关闭警报」按钮停止
  “””

import asyncio
import math
import logging
import os
from datetime import datetime

import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
Application,
CallbackQueryHandler,
CommandHandler,
ContextTypes,
)

logging.basicConfig(
format=”%(asctime)s [%(levelname)s] %(message)s”,
level=logging.INFO,
)
logger = logging.getLogger(**name**)

# ── 环境变量（与你原来的名字保持一致，不用改 Railway）─────────────────────────

BOT_TOKEN = os.environ[“TELEGRAM_TOKEN”]
CHAT_ID   = int(os.environ[“TELEGRAM_CHAT_ID”])

# ── 参数 ─────────────────────────────────────────────────────────────────────

SYMBOL          = “BTCUSDC”
PERIOD          = 29
MULT            = 2.0
CHECK_EVERY     = 600  # 秒：10 分钟检查一次
RING_FAST       = 2    # 秒：前 60 秒每 2 秒响一次
RING_FAST_LIMIT = 60   # 秒：快速响铃持续时长
RING_SLOW       = 30   # 秒：之后每 30 秒响一次

# ── 状态 ─────────────────────────────────────────────────────────────────────

alarm_active = False
ring_task: asyncio.Task | None = None

# ── 布林带计算 ────────────────────────────────────────────────────────────────

def calc_bb(closes: list[float]):
if len(closes) < PERIOD:
return None
s = closes[-PERIOD:]
mean = sum(s) / PERIOD
std = math.sqrt(sum((x - mean) ** 2 for x in s) / PERIOD)
return mean + MULT * std, mean, mean - MULT * std

# ── 拉取币安数据 ──────────────────────────────────────────────────────────────

async def fetch_data():
base = “https://fapi.binance.com/fapi/v1”
async with aiohttp.ClientSession() as session:
async with session.get(
f”{base}/klines?symbol={SYMBOL}&interval=1m&limit=60”
) as r:
klines = await r.json()
async with session.get(
f”{base}/ticker/price?symbol={SYMBOL}”
) as r:
pd = await r.json()
closes = [float(k[4]) for k in klines]
price = float(pd[“price”])
return price, calc_bb(closes)

# ── 发一条警报消息 ────────────────────────────────────────────────────────────

async def send_alarm_msg(bot, alarm_type: str, price: float, band: float):
now = datetime.now().strftime(”%H:%M:%S”)
if alarm_type == “upper”:
head = “🔺 *突破布林上轨*”
body = f”当前价格 `${price:,.2f}` 触碰上轨 `${band:,.2f}`”
else:
head = “🔻 *跌破布林下轨*”
body = f”当前价格 `${price:,.2f}` 触碰下轨 `${band:,.2f}`”

```
text = (
    f"⚠️ *BTC/USDC BOLL\(29,2\) 警报*\n\n"
    f"{head}\n{body}\n\n"
    f"_触发时间: {now}_\n"
    f"_前60秒每2秒提醒，之后每30秒，点按钮关闭_"
)
kb = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ 知道了，关闭警报", callback_data="dismiss")
]])
await bot.send_message(
    chat_id=CHAT_ID,
    text=text,
    parse_mode="MarkdownV2",
    reply_markup=kb,
)
```

# ── 持续响铃循环 ──────────────────────────────────────────────────────────────

async def ring_loop(bot, alarm_type: str, price: float, band: float):
global alarm_active
alarm_active = True
start = asyncio.get_event_loop().time()
logger.info(f”Alarm: {alarm_type} price={price:.2f} band={band:.2f}”)

```
while alarm_active:
    try:
        await send_alarm_msg(bot, alarm_type, price, band)
    except Exception as e:
        logger.error(f"Send failed: {e}")

    elapsed = asyncio.get_event_loop().time() - start
    if elapsed < RING_FAST_LIMIT:
        await asyncio.sleep(RING_FAST)   # 前 60 秒：每 2 秒
    else:
        await asyncio.sleep(RING_SLOW)   # 之后：每 30 秒

logger.info("Alarm dismissed.")
```

# ── 定时检查 ──────────────────────────────────────────────────────────────────

async def check_job(context: ContextTypes.DEFAULT_TYPE):
global alarm_active, ring_task

```
if alarm_active:
    logger.info("Alarm active, skip check.")
    return

try:
    price, bb = await fetch_data()
except Exception as e:
    logger.error(f"Fetch failed: {e}")
    return

if bb is None:
    return

upper, middle, lower = bb
logger.info(f"Price=${price:.2f} BB=[{lower:.2f} ~ {upper:.2f}]")

if price >= upper:
    ring_task = asyncio.create_task(
        ring_loop(context.bot, "upper", price, upper)
    )
elif price <= lower:
    ring_task = asyncio.create_task(
        ring_loop(context.bot, "lower", price, lower)
    )
```

# ── 关闭警报按钮回调 ───────────────────────────────────────────────────────────

async def dismiss_callback(update, context: ContextTypes.DEFAULT_TYPE):
global alarm_active
alarm_active = False
query = update.callback_query
await query.answer(“警报已关闭 ✅”)
try:
await query.edit_message_text(
query.message.text + “\n\n✅ 警报已手动关闭”,
)
except Exception:
pass

# ── /status 命令 ──────────────────────────────────────────────────────────────

async def status_cmd(update, context: ContextTypes.DEFAULT_TYPE):
try:
price, bb = await fetch_data()
upper, middle, lower = bb
except Exception as e:
await update.message.reply_text(f”❌ 查询失败: {e}”)
return

```
if price >= upper:
    pos = "🔴 高于上轨"
elif price <= lower:
    pos = "🟢 低于下轨"
else:
    pct = (price - lower) / (upper - lower) * 100
    pos = f"🟡 轨道内 {pct:.0f}% 位置"

await update.message.reply_text(
    f"📊 BTC/USDC 当前状态\n\n"
    f"💰 价格:  ${price:,.2f}\n"
    f"▲ 上轨:  ${upper:,.2f}\n"
    f"─ 中轨:  ${middle:,.2f}\n"
    f"▼ 下轨:  ${lower:,.2f}\n\n"
    f"📍 {pos}\n"
    f"⏱ 每 {CHECK_EVERY//60} 分钟检查一次"
)
```

# ── /stop 强制停止 ─────────────────────────────────────────────────────────────

async def stop_cmd(update, context: ContextTypes.DEFAULT_TYPE):
global alarm_active
alarm_active = False
await update.message.reply_text(“✅ 警报已强制停止。”)

# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler(“status”, status_cmd))
app.add_handler(CommandHandler(“stop”, stop_cmd))
app.add_handler(CallbackQueryHandler(dismiss_callback, pattern=”^dismiss$”))

```
jq = app.job_queue
jq.run_repeating(check_job, interval=CHECK_EVERY, first=15)

logger.info(
    f"Bot started | {SYMBOL} BOLL({PERIOD},{MULT}) "
    f"| 每{CHECK_EVERY//60}分钟检查 | 响铃: {RING_FAST}s→{RING_SLOW}s"
)
app.run_polling(drop_pending_updates=True)
```

if **name** == “**main**”:
main()
