import os
import json
import requests
import math
import time
import threading
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask
from threading import Thread

TOKEN = "8627716984:AAFipcvX107J2hAXjVzx3Qf2_a09kHpN1pI"
TWELVEDATA_API_KEY = "3606e4cd64be4d2e935a299af6fce343"

RISK_PERCENT = 2.0
CAPITAL = 100.0
HISTORY_FILE = "history.json"
RENDER_URL = "https://gold-signal-bot.onrender.com"

app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "ربات سیگنال‌دهنده طلا روشن است ✅"

def run_flask():
    app_flask.run(host='0.0.0.0', port=10000)

def keep_alive():
    url = RENDER_URL
    while True:
        try:
            requests.get(url, timeout=10)
            print("✅ Keep-alive ping sent")
        except:
            print("❌ Keep-alive failed")
        time.sleep(300)

def get_current_price():
    try:
        resp = requests.get("https://api.gold-api.com/price/XAU", timeout=10)
        if resp.status_code == 200:
            return float(resp.json()["price"])
    except:
        pass
    try:
        resp = requests.get("https://api.exchangerate.host/convert?from=XAU&to=USD", timeout=10)
        if resp.status_code == 200:
            return float(resp.json()["result"])
    except:
        pass
    return None

def get_data_from_twelvedata(symbol="XAU/USD", interval="15min", limit=5000):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": limit,
        "apikey": TWELVEDATA_API_KEY
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if "values" in data:
                candles = []
                for item in data["values"]:
                    dt = datetime.fromisoformat(item["datetime"])
                    candles.append({
                        "time": int(dt.timestamp()),
                        "open": float(item["open"]),
                        "high": float(item["high"]),
                        "low": float(item["low"]),
                        "close": float(item["close"]),
                        "volume": float(item["volume"]) if "volume" in item else 1000
                    })
                if candles:
                    return candles
        return None
    except Exception as e:
        print(f"TwelveData error: {e}")
        return None

def get_data_from_gold_api(limit=1000):
    try:
        resp = requests.get("https://api.gold-api.com/price/XAU/history?limit=" + str(limit), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if "prices" in data and data["prices"]:
                candles = []
                for item in data["prices"]:
                    try:
                        dt = datetime.fromisoformat(item["date"].replace("Z", ""))
                        candles.append({
                            "time": int(dt.timestamp()),
                            "open": float(item["open"]),
                            "high": float(item["high"]),
                            "low": float(item["low"]),
                            "close": float(item["close"]),
                            "volume": 1000
                        })
                    except:
                        continue
                if candles:
                    return candles[::-1]
        return None
    except:
        return None

def get_data_from_yfinance(symbol="XAUUSD=X", interval="15m", period="2mo"):
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(interval=interval, period=period)
        if df.empty:
            return None
        candles = []
        for idx, row in df.iterrows():
            candles.append({
                "time": int(idx.timestamp()),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]) if row["Volume"] else 1000
            })
        return candles
    except:
        return None

def get_market_data():
    candles = get_data_from_twelvedata(limit=5000)
    if candles and len(candles) >= 100:
        return candles
    candles = get_data_from_yfinance()
    if candles and len(candles) >= 100:
        return candles
    candles = get_data_from_gold_api(limit=1000)
    if candles and len(candles) >= 100:
        return candles
    return None

def resample_candles(candles, target_minutes):
    if not candles:
        return []
    target_seconds = target_minutes * 60
    result = []
    group = []
    start_time = None
    for c in candles:
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

def calc_vwap(candles):
    if not candles or len(candles) < 20:
        return None
    total_volume = 0
    total_high_low = 0
    for c in candles:
        typical_price = (c["high"] + c["low"] + c["close"]) / 3
        total_volume += c["volume"]
        total_high_low += typical_price * c["volume"]
    if total_volume == 0:
        return None
    return total_high_low / total_volume

def calc_fvg(candles, lookback=20):
    if len(candles) < 3:
        return 0
    fvg_score = 0
    for i in range(1, min(lookback, len(candles)-1)):
        if candles[i]["high"] < candles[i-1]["low"]:
            fvg_score += 1
        if candles[i]["low"] > candles[i-1]["high"]:
            fvg_score += 1
    return min(100, fvg_score * 10)

def calc_supply_demand(candles, lookback=20):
    if len(candles) < lookback:
        return 50
    volume_avg = sum(c["volume"] for c in candles[-lookback:]) / lookback
    last_volume = candles[-1]["volume"]
    if last_volume > volume_avg * 1.5:
        return 70
    elif last_volume < volume_avg * 0.5:
        return 30
    return 50

def calc_stop_hunt(candles, lookback=30):
    if len(candles) < lookback:
        return 50
    price = candles[-1]["close"]
    high_20 = max(c["high"] for c in candles[-20:])
    low_20 = min(c["low"] for c in candles[-20:])
    atr = calc_atr(candles, 14)
    if not atr:
        return 50
    if price > high_20 - atr * 0.5:
        return 70
    elif price < low_20 + atr * 0.5:
        return 30
    return 50

def calc_market_quality(candles):
    if len(candles) < 20:
        return 50
    atr = calc_atr(candles, 14)
    if not atr:
        return 50
    avg_range = sum(c["high"] - c["low"] for c in candles[-20:]) / 20
    ratio = avg_range / atr if atr > 0 else 0
    if 0.5 <= ratio <= 2:
        return 80
    elif ratio < 0.5 or ratio > 2:
        return 50
    return 50

def detect_whale_activity(candles):
    if len(candles) < 30:
        return 50, "خنثی"
    volumes = [c["volume"] for c in candles[-20:]]
    avg_volume = sum(volumes) / len(volumes) if volumes else 1
    last_volume = candles[-1]["volume"]
    volume_spike = last_volume / avg_volume if avg_volume > 0 else 1
    
    if volume_spike > 2.0:
        last_candle = candles[-1]
        prev_candle = candles[-2]
        price_move = abs(last_candle["close"] - prev_candle["close"])
        moves = [abs(candles[i]["close"] - candles[i-1]["close"]) for i in range(-19, 0) if i > 0]
        avg_move = sum(moves) / len(moves) if moves else 1
        
        if volume_spike > 2.0 and price_move > avg_move * 1.5:
            if last_candle["close"] > prev_candle["close"]:
                return 95, "WHALE_BUY"
            else:
                return 95, "WHALE_SELL"
    return 50, "خنثی"

def analyze_timeframes(candles_15m):
    tf_15m = candles_15m
    tf_1h = resample_candles(candles_15m, 60)
    tf_4h = resample_candles(candles_15m, 240)
    results = {}
    for name, tf in [("15m", tf_15m), ("1h", tf_1h), ("4h", tf_4h)]:
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

def get_alignment_direction(tf_results):
    bullish = sum(1 for t in tf_results.values() if t["direction"] == "bullish")
    bearish = sum(1 for t in tf_results.values() if t["direction"] == "bearish")
    if bullish >= 2:
        return "bullish"
    elif bearish >= 2:
        return "bearish"
    return "neutral"

def calculate_risk_management(entry_price, score, atr):
    if atr is None or atr == 0:
        sl_pips = 50
    else:
        sl_pips = max(30, int(atr * 2))
    
    tp1_pips = int(sl_pips * 1.6)
    tp2_pips = int(sl_pips * 2.4)
    
    sl_dollar = CAPITAL * (RISK_PERCENT / 100)
    
    lot_size = sl_dollar / (sl_pips * 0.1)
    lot_size = round(lot_size, 3)
    if lot_size < 0.001:
        lot_size = 0.001
    
    max_allowed_lot = 1.0
    if lot_size > max_allowed_lot:
        lot_size = max_allowed_lot
    
    sl1_price = entry_price - (sl_pips * 0.1)
    sl2_price = entry_price - (sl_pips * 0.1 * 2)
    tp1_price = entry_price + (tp1_pips * 0.1)
    tp2_price = entry_price + (tp2_pips * 0.1)
    
    return {
        "lot_size": lot_size,
        "sl1": round(sl1_price, 2),
        "sl2": round(sl2_price, 2),
        "tp1": round(tp1_price, 2),
        "tp2": round(tp2_price, 2),
        "sl_pips": sl_pips,
        "tp1_pips": tp1_pips,
        "tp2_pips": tp2_pips
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
        "🤖 ربات سیگنال‌دهنده طلا (نسخه حرفه‌ای)\n\n"
        "دستورات:\n"
        "/signal - دریافت سیگنال جدید\n"
        "/set_risk X - تنظیم درصد ریسک\n"
        "/status - وضعیت آخرین سیگنال\n"
        "/help - راهنما"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 راهنمای ربات\n\n"
        "ربات با ۱۲ لایه تحلیل ترکیبی سیگنال می‌دهد:\n"
        "• هم‌جهتی پویا\n"
        "• باندهای بولینگر\n"
        "• RSI و مومنتوم\n"
        "• فیلتر روند EMA\n"
        "• رژیم بازار\n"
        "• تأیید حجم هوشمند\n"
        "• تشخیص فعالیت نهنگ\n"
        "• FVG\n"
        "• عرضه/تقاضا\n"
        "• شکار استاپ\n"
        "• VWAP\n"
        "• کیفیت بازار\n\n"
        "/signal : دریافت سیگنال\n"
        "/set_risk 3 : تنظیم ریسک به ۳٪\n"
        "/status : نمایش کارنامه\n"
        "/help : راهنما"
    )

async def set_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global RISK_PERCENT
    try:
        new_risk = float(context.args[0])
        if 0.5 <= new_risk <= 10:
            RISK_PERCENT = new_risk
            await update.message.reply_text(f"✅ ریسک به {new_risk}٪ تغییر کرد.")
        else:
            await update.message.reply_text("❌ ریسک باید بین ۰.۵ تا ۱۰ درصد باشد.")
    except:
        await update.message.reply_text("❌ دستور صحیح: /set_risk 2")

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
    try:
        candles_15m = get_market_data()
        if not candles_15m or len(candles_15m) < 100:
            await update.message.reply_text("❌ خطا در دریافت داده. لطفاً چند دقیقه دیگر تلاش کنید.")
            return

        price = get_current_price()
        if not price:
            price = candles_15m[-1]["close"]

        data = check_previous_signal(price)
        last_signal = data["signals"][-1] if data["signals"] else None

        tf_results = analyze_timeframes(candles_15m)

        bb_score = 50
        if len(candles_15m) >= 20:
            closes = [c["close"] for c in candles_15m[-20:]]
            sma = sum(closes) / 20
            std = (sum((x - sma) ** 2 for x in closes) / 20) ** 0.5
            bb_upper = sma + 2 * std
            bb_lower = sma - 2 * std
            last_close = candles_15m[-1]["close"]
            if last_close < bb_lower:
                bb_score = 80
            elif last_close > bb_upper:
                bb_score = 20
            else:
                bb_score = 50

        rsi = calc_rsi(candles_15m, 14)
        if rsi < 30:
            rsi_score = 80
        elif rsi > 70:
            rsi_score = 20
        else:
            rsi_score = 50

        ema20 = calc_ema(candles_15m, 20)
        ema50 = calc_ema(candles_15m, 50)
        ema_score = 50
        if ema20 and ema50:
            if candles_15m[-1]["close"] > ema20 and ema20 > ema50:
                ema_score = 80
            elif candles_15m[-1]["close"] < ema20 and ema20 < ema50:
                ema_score = 20
            else:
                ema_score = 50

        ema200 = calc_ema(candles_15m, 200)
        market_regime = "neutral"
        market_regime_score = 50
        if ema200:
            diff_pct = ((candles_15m[-1]["close"] - ema200) / ema200) * 100
            if diff_pct > 2:
                market_regime = "strong_bullish"
                market_regime_score = 90
            elif diff_pct < -2:
                market_regime = "strong_bearish"
                market_regime_score = 90
            else:
                market_regime = "neutral"
                market_regime_score = 50

        volumes = [c["volume"] for c in candles_15m[-20:]]
        avg_volume = sum(volumes) / len(volumes) if volumes else 1
        last_volume = candles_15m[-1]["volume"]
        if last_volume > avg_volume * 1.5:
            volume_score = 80
        elif last_volume < avg_volume * 0.5:
            volume_score = 20
        else:
            volume_score = 50

        whale_score, whale_status = detect_whale_activity(candles_15m)
        fvg_score = calc_fvg(candles_15m, 20)
        sd_score = calc_supply_demand(candles_15m, 20)
        stop_hunt_score = calc_stop_hunt(candles_15m, 30)

        vwap = calc_vwap(candles_15m)
        vwap_score = 50
        if vwap:
            if candles_15m[-1]["close"] > vwap:
                vwap_score = 80
            else:
                vwap_score = 20

        quality_score = calc_market_quality(candles_15m)

        bullish_count = sum(1 for t in tf_results.values() if t["direction"] == "bullish")
        bearish_count = sum(1 for t in tf_results.values() if t["direction"] == "bearish")
        if market_regime == "strong_bullish":
            alignment_score = 100 if bullish_count == 3 else 70 if bullish_count == 2 else 40
        elif market_regime == "strong_bearish":
            alignment_score = 100 if bearish_count == 3 else 70 if bearish_count == 2 else 40
        else:
            alignment_score = 70 if bullish_count >= 2 or bearish_count >= 2 else 40

        final_score = (
            (alignment_score * 0.20) +
            (bb_score * 0.15) +
            (rsi_score * 0.15) +
            (ema_score * 0.10) +
            (market_regime_score * 0.10) +
            (volume_score * 0.10) +
            (whale_score * 0.10) +
            (fvg_score * 0.05) +
            (sd_score * 0.05) +
            (stop_hunt_score * 0.05) +
            (vwap_score * 0.05) +
            (quality_score * 0.05)
        )
        final_score = round(final_score, 1)

        if final_score < 45:
            await update.message.reply_text("⚠️ شرایط بازار مبهم است. هیچ سیگنالی صادر نمی‌شود.")
            return

        signal_type = "buy" if final_score >= 55 else "sell"
        atr = calc_atr(candles_15m, 14)
        risk = calculate_risk_management(price, final_score, atr)

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
        tf_lines = []
        for name, res in tf_results.items():
            tf_lines.append(f"{name}: {dir_emoji[res['direction']]}")

        prev_line = ""
        if last_signal:
            status_text = "✅ موفق" if last_signal["status"] == "win" else "❌ ناموفق" if last_signal["status"] == "loss" else "⏳ در انتظار"
            profit_text = f"سود {last_signal['profit_loss']} دلار" if last_signal.get("profit_loss") is not None else ""
            prev_line = f"📈 کارنامه قبلی:\n- تاریخ: {last_signal['timestamp']}\n- نتیجه: {status_text} {profit_text}\n- مجموع: {round(data['total_profit'], 2)} دلار\n"

        def bar(score):
            filled = int(score / 10)
            return "▰" * filled + "▱" * (10 - filled)

        alignment_direction = get_alignment_direction(tf_results)
        align_emoji = dir_emoji.get(alignment_direction, "➖")

        msg = (
            f"📊 **سیگنال {'خرید' if signal_type=='buy' else 'فروش'} طلا**\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"🔍 **امتیاز نهایی: {final_score}** ({'🔥 طلایی' if final_score>=70 else '✅ خوب' if final_score>=55 else '⚠️ متوسط'})\n\n"
            f"📈 **وضعیت پارامترها:**\n"
            f"هم‌جهتی     {bar(alignment_score)} {alignment_score}%  {align_emoji}\n"
            f"بولینگر     {bar(bb_score)} {bb_score}%\n"
            f"RSI         {bar(rsi_score)} {rsi_score}%\n"
            f"روند (EMA)  {bar(ema_score)} {ema_score}%\n"
            f"رژیم بازار  {bar(market_regime_score)} {market_regime_score}%\n"
            f"حجم هوشمند  {bar(volume_score)} {volume_score}%\n"
            f"نهنگ        {bar(whale_score)} {whale_score}%  {whale_status}\n"
            f"FVG         {bar(fvg_score)} {fvg_score}%\n"
            f"عرضه/تقاضا  {bar(sd_score)} {sd_score}%\n"
            f"شکار استاپ  {bar(stop_hunt_score)} {stop_hunt_score}%\n"
            f"VWAP        {bar(vwap_score)} {vwap_score}%\n"
            f"کیفیت بازار {bar(quality_score)} {quality_score}%\n\n"
            f"💰 **ورود:** {price}\n"
            f"📦 **حجم:** {risk['lot_size']} لات (ریسک {RISK_PERCENT}%)\n"
            f"🎯 **حد سود 1:** {risk['tp1']} ({risk['tp1_pips']} پیپ) ← بستن 60%\n"
            f"🎯 **حد سود 2:** {risk['tp2']} ({risk['tp2_pips']} پیپ) ← بستن 40%\n"
            f"🛡️ **حد ضرر:** {risk['sl2']} ({risk['sl_pips']} پیپ)\n\n"
            + prev_line
            + "📌 /set_risk X"
        )
        await update.message.reply_text(msg)

    except Exception as e:
        await update.message.reply_text(f"❌ خطای داخلی: `{str(e)}`")

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
    Thread(target=run_flask).start()
    Thread(target=keep_alive, daemon=True).start()
    main()