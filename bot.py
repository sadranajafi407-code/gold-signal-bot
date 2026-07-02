import os
import json
import requests
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8627716984:AAFipcvX107J2hAXjVzx3Qf2_a09kHpN1pI"

RISK_PERCENT = 5.0
CAPITAL = 100.0
HISTORY_FILE = "history.json"

def get_kucoin_klines(symbol="XAU-USDT", timeframe="1hour", limit=200):
    url = "https://api.kucoin.com/api/v1/market/candles"
    params = {"symbol": symbol, "type": timeframe, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("code") == "200000":
            candles = data["data"]
            result = []
            for c in candles:
                result.append({
                    "time": int(c[0]) // 1000,
                    "open": float(c[1]),
                    "close": float(c[2]),
                    "high": float(c[3]),
                    "low": float(c[4]),
                    "volume": float(c[5])
                })
            return result
        return None
    except:
        return None

def get_binance_klines(symbol="XAUUSDT", interval="1h", limit=200):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if isinstance(data, list):
            result = []
            for c in data:
                result.append({
                    "time": int(c[0]) // 1000,
                    "open": float(c[1]),
                    "close": float(c[4]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "volume": float(c[5])
                })
            return result
        return None
    except:
        return None

def get_current_price():
    try:
        resp = requests.get("https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=XAU-USDT", timeout=10)
        data = resp.json()
        if data.get("code") == "200000":
            return float(data["data"]["price"])
    except:
        pass
    try:
        resp = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=XAUUSDT", timeout=10)
        data = resp.json()
        if "price" in data:
            return float(data["price"])
    except:
        pass
    return None

def get_fear_greed_index():
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = resp.json()
        if data.get("data"):
            return int(data["data"][0]["value"])
    except:
        return 50
    return 50

def resample_candles(candles_1h, target_minutes):
    if not candles_1h:
        return []
    target_seconds = target_minutes * 60
    result = []
    group = []
    start_time = None
    for c in candles_1h:
        if start_time is None:
            start_time = c["time"]
        if c["time"] - start_time >= target_seconds:
            if group:
                result.append({
                    "time": start_time,
                    "open": group[0]["open"],
                    "high": max(x["high"] for x in group),
                    "low": min(x["low"] for x in group),
                    "close": group[-1]["close"],
                    "volume": sum(x["volume"] for x in group)
                })
            start_time = c["time"]
            group = [c]
        else:
            group.append(c)
    if group:
        result.append({
            "time": start_time,
            "open": group[0]["open"],
            "high": max(x["high"] for x in group),
            "low": min(x["low"] for x in group),
            "close": group[-1]["close"],
            "volume": sum(x["volume"] for x in group)
        })
    return result

def calc_ema(candles, period=20):
    if len(candles) < period:
        return None
    sma = sum(c["close"] for c in candles[:period]) / period
    ema = sma
    multiplier = 2 / (period + 1)
    for c in candles[period:]:
        ema = (c["close"] - ema) * multiplier + ema
    return ema

def calc_rsi(candles, period=14):
    if len(candles) < period + 1:
        return 50
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = candles[i]["close"] - candles[i-1]["close"]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-diff)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    tr_list = []
    for i in range(1, len(candles)):
        high_low = candles[i]["high"] - candles[i]["low"]
        high_close = abs(candles[i]["high"] - candles[i-1]["close"])
        low_close = abs(candles[i]["low"] - candles[i-1]["close"])
        tr = max(high_low, high_close, low_close)
        tr_list.append(tr)
    if len(tr_list) < period:
        return None
    atr = sum(tr_list[-period:]) / period
    return atr

def calc_fibonacci(candles_daily):
    if not candles_daily or len(candles_daily) < 2:
        return None
    high = max(c["high"] for c in candles_daily[-2:])
    low = min(c["low"] for c in candles_daily[-2:])
    diff = high - low
    return {
        "level_0_382": high - diff * 0.382,
        "level_0_618": high - diff * 0.618
    }

def analyze_timeframes(candles_1h):
    tf_15m = resample_candles(candles_1h, 15)
    tf_1h = candles_1h
    tf_4h = resample_candles(candles_1h, 240)
    tf_daily = resample_candles(candles_1h, 1440)
    results = {}
    for name, tf in [("15m", tf_15m), ("1h", tf_1h), ("4h", tf_4h), ("daily", tf_daily)]:
        if len(tf) < 20:
            results[name] = {"direction": "neutral", "score": 50}
            continue
        ema20 = calc_ema(tf, 20)
        price = tf[-1]["close"]
        ema_score = 50
        if ema20:
            diff_pct = ((price - ema20) / ema20) * 100
            ema_score = 80 if diff_pct > 0.5 else 20 if diff_pct < -0.5 else 50
        rsi = calc_rsi(tf, 14)
        rsi_score = 80 if rsi > 60 else 20 if rsi < 40 else 50
        pattern_score = 50
        if len(tf) >= 2:
            last = tf[-1]
            prev = tf[-2]
            body = abs(last["close"] - last["open"])
            lower_shadow = min(last["open"], last["close"]) - last["low"]
            if lower_shadow > body * 2 and last["close"] > prev["close"]:
                pattern_score = 80
            upper_shadow = last["high"] - max(last["open"], last["close"])
            if upper_shadow > body * 2 and last["close"] < prev["close"]:
                pattern_score = 20
        vol_avg = sum(c["volume"] for c in tf[-10:]) / 10 if len(tf) >= 10 else 1
        vol_score = 80 if tf[-1]["volume"] > vol_avg * 1.5 else 20 if tf[-1]["volume"] < vol_avg * 0.5 else 50
        final_score = (ema_score * 0.3) + (rsi_score * 0.2) + (pattern_score * 0.25) + (vol_score * 0.25)
        direction = "bullish" if final_score >= 60 else "bearish" if final_score <= 40 else "neutral"
        results[name] = {"direction": direction, "score": round(final_score, 1)}
    return results

def calculate_final_score(tf_results, fib_levels, fear_greed, news_risk):
    bullish_count = sum(1 for t in tf_results.values() if t["direction"] == "bullish")
    if bullish_count >= 3:
        alignment_score = 100
    elif bullish_count == 2:
        alignment_score = 70
    else:
        alignment_score = 40
    tf_4h = tf_results.get("4h", {})
    trend_score = tf_4h.get("score", 50)
    fib_score = 50
    if fib_levels:
        current_price = tf_results.get("1h", {}).get("price", 0)
        if current_price:
            dist_382 = abs(current_price - fib_levels["level_0_382"])
            dist_618 = abs(current_price - fib_levels["level_0_618"])
            fib_score = 90 if min(dist_382, dist_618) < 5 else 70 if min(dist_382, dist_618) < 15 else 50
    fear_score = 100 if 30 <= fear_greed <= 70 else 40
    news_score = 100 if not news_risk else 0
    final_score = (
        (alignment_score * 0.35) +
        (trend_score * 0.20) +
        (fib_score * 0.15) +
        (50 * 0.10) +
        (fear_score * 0.10) +
        (news_score * 0.05) +
        (50 * 0.05)
    )
    return round(final_score, 1)

def calculate_risk_management(entry_price, score):
    base_sl_pips = 25
    base_tp_pips = 30
    sl_dollar = CAPITAL * (RISK_PERCENT / 100)
    lot_size = sl_dollar / (base_sl_pips * 0.1)
    lot_size = round(lot_size, 3)
    if lot_size < 0.001:
        lot_size = 0.001
    sl1_price = entry_price - (base_sl_pips * 0.1)
    sl2_price = entry_price - (base_sl_pips * 0.1 * 2)
    tp1_price = entry_price + (base_tp_pips * 0.1 * 0.6)
    tp2_price = entry_price + (base_tp_pips * 0.1)
    pyramid_active = score >= 70
    return {
        "lot_size": lot_size,
        "sl1": round(sl1_price, 2),
        "sl2": round(sl2_price, 2),
        "tp1": round(tp1_price, 2),
        "tp2": round(tp2_price, 2),
        "pyramid_active": pyramid_active
    }

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return {"signals": [], "total_profit": 0.0, "win_count": 0, "loss_count": 0}

def save_history(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)

def check_previous_signal(current_price):
    data = load_history()
    if not data["signals"]:
        return data
    pending_idx = -1
    for i in range(len(data["signals"]) - 1, -1, -1):
        if data["signals"][i]["status"] == "pending":
            pending_idx = i
            break
    if pending_idx == -1:
        return data
    entry = data["signals"][pending_idx]["entry"]
    sl = data["signals"][pending_idx]["stop_loss"]
    tp = data["signals"][pending_idx]["take_profit"]
    if data["signals"][pending_idx]["type"] == "buy":
        if current_price >= tp:
            profit = (tp - entry) * 0.01
            data["signals"][pending_idx]["status"] = "win"
            data["signals"][pending_idx]["profit_loss"] = round(profit, 2)
            data["total_profit"] += profit
            data["win_count"] += 1
        elif current_price <= sl:
            loss = (sl - entry) * 0.01
            data["signals"][pending_idx]["status"] = "loss"
            data["signals"][pending_idx]["profit_loss"] = round(loss, 2)
            data["total_profit"] += loss
            data["loss_count"] += 1
    elif data["signals"][pending_idx]["type"] == "sell":
        if current_price <= tp:
            profit = (entry - tp) * 0.01
            data["signals"][pending_idx]["status"] = "win"
            data["signals"][pending_idx]["profit_loss"] = round(profit, 2)
            data["total_profit"] += profit
            data["win_count"] += 1
        elif current_price >= sl:
            loss = (entry - sl) * 0.01
            data["signals"][pending_idx]["status"] = "loss"
            data["signals"][pending_idx]["profit_loss"] = round(loss, 2)
            data["total_profit"] += loss
            data["loss_count"] += 1
    save_history(data)
    return data

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 ربات سیگنال‌دهنده طلا\n\n"
        "دستورات:\n"
        "/signal - دریافت سیگنال جدید\n"
        "/set_risk X - تنظیم درصد ریسک\n"
        "/status - وضعیت آخرین سیگنال\n"
        "/help - راهنما"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 راهنمای ربات\n\n"
        "این ربات با تحلیل ۴ تایم‌فریم سیگنال می‌دهد.\n"
        "/signal : دریافت سیگنال\n"
        "/set_risk 3 : تنظیم ریسک به ۳٪\n"
        "/status : نمایش کارنامه\n"
        "/help : راهنما"
    )

async def set_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global RISK_PERCENT
    try:
        new_risk = float(context.args[0])
        if 1 <= new_risk <= 20:
            RISK_PERCENT = new_risk
            await update.message.reply_text(f"✅ ریسک به {new_risk}٪ تغییر کرد.")
        else:
            await update.message.reply_text("❌ ریسک باید بین ۱ تا ۲۰ درصد باشد.")
    except:
        await update.message.reply_text("❌ دستور صحیح: /set_risk 5")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_history()
    if not data["signals"]:
        await update.message.reply_text("📭 هنوز سیگنالی صادر نشده.")
        return
    last = data["signals"][-1]
    msg = f"📊 آخرین سیگنال:\n"
    msg += f"تاریخ: {last['timestamp']}\n"
    msg += f"نوع: {'خرید' if last['type']=='buy' else 'فروش'}\n"
    msg += f"ورود: {last['entry']}\n"
    msg += f"حد ضرر: {last['stop_loss']}\n"
    msg += f"حد سود: {last['take_profit']}\n"
    msg += f"وضعیت: {last['status']}\n"
    if last.get("profit_loss") is not None:
        msg += f"سود/ضرر: {last['profit_loss']} دلار\n"
    msg += f"\nکارنامه کل:\n"
    msg += f"تعداد سیگنال‌ها: {len(data['signals'])}\n"
    msg += f"برد: {data['win_count']} | باخت: {data['loss_count']}\n"
    msg += f"سود خالص: {round(data['total_profit'], 2)} دلار"
    await update.message.reply_text(msg)

async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = get_current_price()
    if not price:
        await update.message.reply_text("❌ خطا در دریافت قیمت.")
        return
    candles = get_kucoin_klines(limit=200)
    if not candles:
        candles = get_binance_klines(limit=200)
    if not candles:
        await update.message.reply_text("❌ خطا در دریافت داده.")
        return
    data = check_previous_signal(price)
    last_signal = data["signals"][-1] if data["signals"] else None
    tf_results = analyze_timeframes(candles)
    daily = resample_candles(candles, 1440)
    fib = calc_fibonacci(daily)
    fear_greed = get_fear_greed_index()
    news_risk = os.environ.get("NEWS_RISK", "no").lower() == "yes"
    final_score = calculate_final_score(tf_results, fib, fear_greed, news_risk)
    if final_score < 45:
        await update.message.reply_text("⚠️ شرایط بازار مبهم است. هیچ سیگنالی صادر نمی‌شود.")
        return
    signal_type = "buy" if final_score >= 55 else "sell"
    risk = calculate_risk_management(price, final_score)
    new_signal = {
        "id": len(data["signals"]) + 1,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": signal_type,
        "entry": price,
        "stop_loss": risk["sl2"],
        "take_profit": risk["tp2"],
        "status": "pending",
        "exit_price": None,
        "profit_loss": None
    }
    data["signals"].append(new_signal)
    save_history(data)
    dir_emoji = {"bullish": "⬆️", "bearish": "⬇️", "neutral": "➖"}
    tf_lines = [f"- {name}: {dir_emoji[res['direction']]} (امتیاز {res['score']})" for name, res in tf_results.items()]
    prev_line = ""
    if last_signal:
        status_text = "✅ موفق" if last_signal["status"] == "win" else "❌ ناموفق" if last_signal["status"] == "loss" else "⏳ در انتظار"
        profit_text = f"سود {last_signal['profit_loss']} دلار" if last_signal.get("profit_loss") is not None else ""
        prev_line = f"📈 کارنامه سیگنال قبلی:\n- تاریخ: {last_signal['timestamp']}\n- نتیجه: {status_text} {profit_text}\n- مجموع سود/ضرر: {round(data['total_profit'], 2)} دلار\n"
    fib_line = ""
    if fib:
        fib_line = f"📐 فیبوناچی:\n- ۰.۳۸۲: {fib['level_0_382']}\n- ۰.۶۱۸: {fib['level_0_618']}\n"
    if risk["pyramid_active"]:
        risk_line = f"🎯 سود هرمی (فعال):\n- هدف اول: {risk['tp1']} ← ۵۰٪\n- هدف دوم: {risk['tp2']} ← ۵۰٪ باقی\n\n🛡️ ضرر پله‌ای (فعال):\n- SL1: {risk['sl1']} ← ۴۰٪\n- SL2: {risk['sl2']} ← ۶۰٪ باقی\n"
    else:
        risk_line = f"🎯 سود ساده:\n- حد سود: {risk['tp2']}\n\n🛡️ ضرر ساده:\n- حد ضرر: {risk['sl2']}\n📌 دلیل: امتیاز {final_score} زیر ۷۰\n"
    friday_warning = "⚠️ امروز جمعه است، ریسک را مدیریت کن.\n" if datetime.now().weekday() == 4 else ""
    msg = (
        f"📊 سیگنال {'خرید' if signal_type=='buy' else 'فروش'} طلا\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"🔍 امتیاز نهایی: {final_score} از ۱۰۰ ({'🔥 طلایی' if final_score>=80 else '✅ خوب' if final_score>=60 else '⚠️ متوسط'})\n\n"
        f"📊 تایم‌فریم‌ها:\n" + "\n".join(tf_lines) + "\n\n"
        f"💰 قیمت: {price}\n"
        f"📦 حجم: {risk['lot_size']} لات\n"
        f"🎯 ورود: {price}\n\n"
        + risk_line + "\n"
        + fib_line
        + prev_line
        + friday_warning
        + "📌 /set_risk X"
    )
    await update.message.reply_text(msg)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("set_risk", set_risk))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("signal", signal))
    print("🤖 ربات روشن شد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()