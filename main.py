# BTC/USDC BOLL(29,2) 10min Telegram alarm bot (5m×2合并)

import asyncio
import math
import logging
import os
from datetime import datetime

import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from telegram.request import HTTPXRequest

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID   = int(os.environ["TELEGRAM_CHAT_ID"])

SYMBOL          = "BTCUSDC"
PERIOD          = 29
MULT            = 2.0
CHECK_EVERY     = 600   # 10分钟检查一次
RING_FAST       = 2
RING_FAST_LIMIT = 60
RING_SLOW       = 30

alarm_active = False


def calc_bb(closes):
    if len(closes) < PERIOD:
        return None
    s = closes[-PERIOD:]
    mean = sum(s) / PERIOD
    std = math.sqrt(sum((x - mean) ** 2 for x in s) / PERIOD)
    return mean + MULT * std, mean, mean - MULT * std


async def fetch_data():
    # 5分钟K线80根，每2根合并成1根10分钟K线
    url_k = (
        "https://api.binance.com/api/v3/klines"
        "?symbol=" + SYMBOL +
        "&interval=5m"
        "&limit=80"
    )
    url_p = "https://api.binance.com/api/v3/ticker/price?symbol=" + SYMBOL
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url_k) as r:
            klines = await r.json()
        if not isinstance(klines, list) or len(klines) == 0:
            raise ValueError("Invalid klines: " + str(klines)[:100])
        async with session.get(url_p) as r:
            pd = await r.json()
        if "price" not in pd:
            raise ValueError("Invalid price: " + str(pd)[:100])

    # 去掉最后一根未完成K线
    complete = klines[:-1]
    # 每2根5m合并为1根10m，取第2根收盘价
    closes_10m = []
    for i in range(0, len(complete) - len(complete) % 2, 2):
        group = complete[i:i+2]
        if len(group) == 2:
            closes_10m.append(float(group[-1][4]))

    price = float(pd["price"])
    return price, calc_bb(closes_10m)


async def send_alarm_msg(bot, alarm_type, price, band):
    now = datetime.now().strftime("%H:%M:%S")
    if alarm_type == "upper":
        head = "BTC/USDC 突破布林上轨 🔴"
        body = "当前价格 $" + "{:,.2f}".format(price) + " / 上轨 $" + "{:,.2f}".format(band)
    else:
        head = "BTC/USDC 跌破布林下轨 🟢"
        body = "当前价格 $" + "{:,.2f}".format(price) + " / 下轨 $" + "{:,.2f}".format(band)

    text = (
        "⚠️ BOLL(29,2) 10min 警报\n\n"
        + head + "\n"
        + body + "\n\n"
        + "触发时间: " + now + "\n"
        + "前60秒每2秒提醒，之后每30秒"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ OK 关闭警报", callback_data="dismiss")
    ]])
    await bot.send_message(chat_id=CHAT_ID, text=text, reply_markup=kb)


async def ring_loop(bot, alarm_type, price, band):
    global alarm_active
    alarm_active = True
    start = asyncio.get_event_loop().time()
    logger.info("Alarm: " + alarm_type + " price=" + str(round(price, 2)))

    while alarm_active:
        try:
            await send_alarm_msg(bot, alarm_type, price, band)
        except Exception as e:
            logger.error("Send failed: " + str(e))
            await asyncio.sleep(5)
            continue

        elapsed = asyncio.get_event_loop().time() - start
        if elapsed < RING_FAST_LIMIT:
            await asyncio.sleep(RING_FAST)
        else:
            await asyncio.sleep(RING_SLOW)

    logger.info("Alarm dismissed.")


async def check_job(context):
    global alarm_active

    if alarm_active:
        logger.info("Alarm active, skip check.")
        return

    try:
        price, bb = await fetch_data()
    except Exception as e:
        logger.error("Fetch failed: " + str(e))
        return

    if bb is None:
        logger.warning("Not enough candles.")
        return

    upper, middle, lower = bb
    logger.info(
        "Price=" + str(round(price, 2)) +
        " UP=" + str(round(upper, 2)) +
        " DN=" + str(round(lower, 2))
    )

    if price >= upper:
        asyncio.create_task(ring_loop(context.bot, "upper", price, upper))
    elif price <= lower:
        asyncio.create_task(ring_loop(context.bot, "lower", price, lower))


async def dismiss_callback(update, context):
    global alarm_active
    alarm_active = False
    query = update.callback_query
    await query.answer("警报已关闭")
    try:
        await query.edit_message_text(query.message.text + "\n\n✅ [已关闭]")
    except Exception:
        pass


async def start_cmd(update, context):
    await update.message.reply_text(
        "✅ BTC/USDC 布林带警报Bot运行中！\n\n"
        "监控: BOLL(29,2) 10分钟K线\n"
        "发送 /status 查看当前上下轨数值\n"
        "发送 /stop 强制停止警报"
    )


async def status_cmd(update, context):
    try:
        price, bb = await fetch_data()
        if bb is None:
            await update.message.reply_text("K线数据不足，请稍后再试。")
            return
        upper, middle, lower = bb
    except Exception as e:
        await update.message.reply_text("查询失败: " + str(e))
        return

    if price >= upper:
        pos = "⚠️ 高于上轨！"
    elif price <= lower:
        pos = "⚠️ 低于下轨！"
    else:
        pct = (price - lower) / (upper - lower) * 100
        pos = "轨道内 " + str(round(pct)) + "% 位置"

    await update.message.reply_text(
        "📊 BTC/USDC 当前状态 (10min K线)\n\n"
        + "价格:  $" + "{:,.2f}".format(price) + "\n"
        + "上轨:  $" + "{:,.2f}".format(upper) + "\n"
        + "中轨:  $" + "{:,.2f}".format(middle) + "\n"
        + "下轨:  $" + "{:,.2f}".format(lower) + "\n\n"
        + "位置: " + pos
    )


async def stop_cmd(update, context):
    global alarm_active
    alarm_active = False
    await update.message.reply_text("🛑 警报已强制停止。")


def main():
    request = HTTPXRequest(connection_pool_size=8, read_timeout=30, write_timeout=30, connect_timeout=30)
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CallbackQueryHandler(dismiss_callback, pattern="^dismiss$"))

    app.job_queue.run_repeating(check_job, interval=CHECK_EVERY, first=15)

    logger.info("Bot started. BTCUSDC BOLL(29,2) 10min K线 every 10min.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
