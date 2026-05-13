# BTC/USDC BOLL(29,2) 10min Telegram alarm bot

import asyncio
import math
import logging
import os
from datetime import datetime

import aiohttp
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID   = int(os.environ["TELEGRAM_CHAT_ID"])

SYMBOL          = "BTCUSDC"
PERIOD          = 29
MULT            = 2.0
CHECK_EVERY     = 600
KLINE_INTERVAL  = "10m"
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
    url_k = (
        "https://fapi.binance.com/fapi/v1/klines"
        "?symbol=" + SYMBOL +
        "&interval=" + KLINE_INTERVAL +
        "&limit=40"
    )
    url_p = "https://fapi.binance.com/fapi/v1/ticker/price?symbol=" + SYMBOL
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url_k) as r:
            klines = await r.json()
        async with session.get(url_p) as r:
            pd = await r.json()
    closes = [float(k[4]) for k in klines]
    price = float(pd["price"])
    return price, calc_bb(closes)


async def send_alarm_msg(bot, alarm_type, price, band):
    now = datetime.now().strftime("%H:%M:%S")
    if alarm_type == "upper":
        head = "BTC/USDC 突破布林上轨"
        body = "当前价格 $" + "{:,.2f}".format(price) + " / 上轨 $" + "{:,.2f}".format(band)
    else:
        head = "BTC/USDC 跌破布林下轨"
        body = "当前价格 $" + "{:,.2f}".format(price) + " / 下轨 $" + "{:,.2f}".format(band)

    text = (
        "BOLL(29,2) 10min 警报\n\n"
        + head + "\n"
        + body + "\n\n"
        + "触发时间: " + now + "\n"
        + "前60秒每2秒提醒，之后每30秒"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("OK 关闭警报", callback_data="dismiss")
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
        await query.edit_message_text(query.message.text + "\n\n[已关闭]")
    except Exception:
        pass


async def status_cmd(update, context):
    try:
        price, bb = await fetch_data()
        upper, middle, lower = bb
    except Exception as e:
        await update.message.reply_text("查询失败: " + str(e))
        return

    if price >= upper:
        pos = "高于上轨"
    elif price <= lower:
        pos = "低于下轨"
    else:
        pct = (price - lower) / (upper - lower) * 100
        pos = "轨道内 " + str(round(pct)) + "% 位置"

    await update.message.reply_text(
        "BTC/USDC 当前状态 (10min K线)\n\n"
        + "价格:  $" + "{:,.2f}".format(price) + "\n"
        + "上轨:  $" + "{:,.2f}".format(upper) + "\n"
        + "中轨:  $" + "{:,.2f}".format(middle) + "\n"
        + "下轨:  $" + "{:,.2f}".format(lower) + "\n\n"
        + "位置: " + pos
    )


async def stop_cmd(update, context):
    global alarm_active
    alarm_active = False
    await update.message.reply_text("警报已强制停止。")


async def clear_old_connection():
    bot = Bot(token=BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.close()
    logger.info("Old connection cleared.")


def main():
    # 启动前先清除旧连接，解决 Conflict 问题
    asyncio.get_event_loop().run_until_complete(clear_old_connection())

    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=30,
        write_timeout=30,
        connect_timeout=30
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CallbackQueryHandler(dismiss_callback, pattern="^dismiss$"))

    app.job_queue.run_repeating(check_job, interval=CHECK_EVERY, first=15)

    logger.info("Bot started. BTCUSDC BOLL(29,2) 10min K线 every 10min.")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"]
    )


if __name__ == "__main__":
    main()
