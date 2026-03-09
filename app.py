"""
🏦 Financial Retirement Planning — LINE Bot
FastAPI + LINE SDK v3 (แบบเดียวกับ Reference Project)

Flow: LINE User → Webhook → FastAPI → @handler.add → คำนวณ → reply
"""

import os
import re
import logging
import uvicorn

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse

# ── LINE SDK v3 (แบบเดียวกับ Reference) ──────────────────────────────────────
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, UserSource
from linebot.v3.exceptions import InvalidSignatureError

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("financial-bot")

# ── Config ────────────────────────────────────────────────────────────────────
CHANNEL_SECRET       = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler       = WebhookHandler(CHANNEL_SECRET)

# ── In-memory sessions ────────────────────────────────────────────────────────
sessions: dict[str, dict] = {}

# ── คำถาม 9 ข้อ ──────────────────────────────────────────────────────────────
QUESTIONS = [
    {"key": "current_age",        "q": "📋 1/9\n\n🎂 อายุปัจจุบัน (ปี)\nเช่น: 18 หรือ 18 ปีครับ"},
    {"key": "retire_age",         "q": "📋 2/9\n\n🏖️ อายุที่ต้องการเกษียณ (ปี)\nเช่น: 50"},
    {"key": "retire_years",       "q": "📋 3/9\n\n⏳ ระยะเวลาหลังเกษียณ (ปี)\nเช่น: 10"},
    {"key": "monthly_income",     "q": "📋 4/9\n\n💵 รายได้ต่อเดือน (บาท)\nเช่น: 50000 หรือ 50,000"},
    {"key": "fixed_expense",      "q": "📋 5/9\n\n🏠 ค่าใช้จ่ายคงที่/เดือน (บาท)\nเช่น: ค่าเช่า ผ่อนรถ → 5000"},
    {"key": "variable_expense",   "q": "📋 6/9\n\n🛒 ค่าใช้จ่ายผันแปร/เดือน (บาท)\nเช่น: อาหาร เดินทาง → 2000"},
    {"key": "current_investment", "q": "📋 7/9\n\n🏦 เงินออม/ลงทุนที่มีอยู่ (บาท)\nเช่น: 100000"},
    {"key": "inflation_rate",     "q": "📋 8/9\n\n📈 อัตราเงินเฟ้อ (%/ปี)\nค่าแนะนำ: 3"},
    {"key": "risk_level",         "q": "📋 9/9\n\n⚖️ ระดับความเสี่ยงการลงทุน\n\n1️⃣ ต่ำ  (Conservative) ~4%/ปี\n2️⃣ กลาง (Moderate)     ~6%/ปี\n3️⃣ สูง  (Aggressive)   ~8%/ปี\n\nพิมพ์ 1, 2 หรือ 3"},
]

# ── พอร์ตการลงทุน ─────────────────────────────────────────────────────────────
PORTFOLIO = {
    1: {"name": "ต่ำ (Conservative)", "avg_return": 0.04,
        "assets": [("เงินสด/ออมทรัพย์",0.30,0.01),("กองทุนตราสารหนี้",0.50,0.04),
                   ("กองทุนหุ้น",0.15,0.09),("กองทุนธีม/ทอง",0.05,0.10)]},
    2: {"name": "กลาง (Moderate)", "avg_return": 0.06,
        "assets": [("เงินสด/ออมทรัพย์",0.10,0.01),("กองทุนตราสารหนี้",0.35,0.04),
                   ("กองทุนหุ้น",0.45,0.09),("กองทุนธีม/ทอง",0.10,0.10)]},
    3: {"name": "สูง (Aggressive)", "avg_return": 0.08,
        "assets": [("เงินสด/ออมทรัพย์",0.05,0.01),("กองทุนตราสารหนี้",0.15,0.04),
                   ("กองทุนหุ้น",0.70,0.09),("กองทุนธีม/ทอง",0.10,0.10)]},
}


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────
def fmt(n: float) -> str:
    return f"{n:,.0f}"


def extract_number(text: str):
    """
    NLP Rule-based: ดึงตัวเลขออกจากข้อความ
    "18 ปีครับ" → 18.0
    "50,000 บาท" → 50000.0
    "อายุ 18" → 18.0
    """
    cleaned = text.replace(",", "")
    match   = re.search(r'\d+(\.\d+)?', cleaned)
    return float(match.group()) if match else None


def reply_msg(api: MessagingApi, reply_token: str, message: str):
    """Reply API — ฟรี ใช้ reply_token"""
    api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=[TextMessage(text=message)],
        )
    )


def push_msg(api: MessagingApi, user_id: str, messages: list):
    """Push API — ใช้เมื่อต้องส่งข้อความมากกว่า 1 ครั้ง"""
    for i in range(0, len(messages), 5):
        chunk = messages[i:i+5]
        api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=m) for m in chunk],
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
#  คำนวณแผนเกษียณ
# ─────────────────────────────────────────────────────────────────────────────
def calculate(data: dict) -> list:
    age    = int(data["current_age"])
    retire = int(data["retire_age"])
    ryears = int(data["retire_years"])
    income = float(data["monthly_income"])
    fixed  = float(data["fixed_expense"])
    var    = float(data["variable_expense"])
    invest = float(data["current_investment"])
    infl   = float(data["inflation_rate"]) / 100
    risk   = int(data["risk_level"])

    total_exp    = fixed + var
    net_saving   = income - total_exp
    saving_ratio = net_saving / income * 100
    years_left   = retire - age

    exp_at_retire  = total_exp * (1 + infl) ** years_left
    target         = exp_at_retire * 12 * ryears
    monthly_needed = (target - invest) / (years_left * 12)
    annual_needed  = monthly_needed * 12
    daily_needed   = annual_needed / 365

    port       = PORTFOLIO[risk]
    avg_return = port["avg_return"]
    total_ret  = sum(invest * p * r for _, p, r in port["assets"])
    w_ret      = total_ret / invest * 100

    n  = years_left
    fv = (invest * (1 + avg_return) ** n
          + annual_needed * (((1 + avg_return) ** n - 1) / avg_return))

    checkpoints = sorted(set([age] + list(range(age+1, retire+1, 5)) + [retire]))
    growth_rows = []
    for a in checkpoints:
        yr = a - age
        if yr == 0:
            growth_rows.append((a, invest))
        else:
            v = (invest * (1 + avg_return) ** yr
                 + annual_needed * (((1 + avg_return) ** yr - 1) / avg_return))
            growth_rows.append((a, v))

    feasible    = "✅ เป็นไปได้!" if net_saving >= monthly_needed else "⚠️ ต้องปรับแผน"
    surplus     = fv - target
    result_icon = "🚀 เกินเป้า!" if surplus > 0 else "⚠️ ต่ำกว่าเป้า"

    msg1 = (
        "═══════════════════\n"
        "📊 สถานะการเงินของคุณ\n"
        "═══════════════════\n"
        f"🎂 อายุ: {age} → เกษียณ {retire} ปี\n"
        f"⏳ เหลือเวลา: {years_left} ปี\n"
        f"💵 รายได้: {fmt(income)} บาท/เดือน\n"
        f"🏠 ค่าใช้จ่ายรวม: {fmt(total_exp)} บาท/เดือน\n\n"
        f"สูตร: {fmt(income)} - {fmt(total_exp)}\n"
        f"💰 เงินคงเหลือ: {fmt(net_saving)} บาท/เดือน\n"
        f"📊 Saving Ratio: {saving_ratio:.2f}%\n\n"
        "═══════════════════\n"
        f"📈 เงินเฟ้อ {infl*100:.0f}%/ปี\n"
        "═══════════════════\n"
        f"สูตร: {fmt(total_exp)} × (1+{infl})^{years_left}\n"
        f"💸 ค่าใช้จ่าย ณ เกษียณ:\n"
        f"   {fmt(exp_at_retire)} บาท/เดือน"
    )

    msg2 = (
        "═══════════════════\n"
        "🎯 เป้าหมายเกษียณ\n"
        "═══════════════════\n"
        f"สูตร: {fmt(exp_at_retire)} × 12 × {ryears}\n"
        f"🏆 เงินก้อนที่ต้องมี:\n"
        f"   {fmt(target)} บาท\n\n"
        "═══════════════════\n"
        "💰 แผนการออม\n"
        "═══════════════════\n"
        f"📌 ออม/เดือน: {fmt(monthly_needed)} บาท\n"
        f"📌 ออม/ปี:    {fmt(annual_needed)} บาท\n"
        f"📌 ออม/วัน:   {fmt(daily_needed)} บาท\n\n"
        f"เงินเหลือ: {fmt(net_saving)} บาท/เดือน\n"
        f"ต้องออม:   {fmt(monthly_needed)} บาท/เดือน\n"
        f"➡️ {feasible}"
    )

    port_lines = (
        "═══════════════════\n"
        f"📊 พอร์ต ({port['name']})\n"
        "═══════════════════\n"
    )
    for name, pct, ret in port["assets"]:
        amt = invest * pct
        port_lines += f"• {name} {pct*100:.0f}%\n"
        port_lines += f"  {fmt(amt)} บาท → {fmt(amt*ret)} บาท/ปี\n"
    port_lines += f"\n📈 รวม: {w_ret:.2f}%/ปี\n"
    port_lines += f"   = {fmt(total_ret)} บาท/ปี"

    surplus_str = f"+{fmt(surplus)}" if surplus > 0 else fmt(surplus)
    growth_txt = (
        "═══════════════════\n"
        f"📈 จำลองการเติบโต ({avg_return*100:.0f}%/ปี)\n"
        "═══════════════════\n"
    )
    for a, v in growth_rows:
        growth_txt += f"อายุ {a:2d} ปี → {fmt(v)} บาท\n"
    growth_txt += (
        "\n═══════════════════\n"
        "📌 สรุป\n"
        "═══════════════════\n"
        f"💎 มูลค่าตอนเกษียณ:\n"
        f"   {fmt(fv)} บาท\n"
        f"🎯 เป้าหมาย:\n"
        f"   {fmt(target)} บาท\n"
        f"{result_icon} ({surplus_str} บาท)\n\n"
        "✅ คำแนะนำ:\n"
        f"1. ออมอัตโนมัติ {fmt(monthly_needed)} บาท/เดือน\n"
        f"2. สำรองฉุกเฉิน {fmt(total_exp*6)} บาท\n"
        f"3. อายุ {retire-5}+ ปี ลดสัดส่วนหุ้น\n"
        "4. ทบทวนแผนทุก 1 ปี 📅\n\n"
        "พิมพ์ 'เริ่ม' เพื่อคำนวณใหม่ 🔄"
    )

    return [msg1, msg2, port_lines, growth_txt]


# ─────────────────────────────────────────────────────────────────────────────
#  LINE Event Handler (แบบเดียวกับ Reference Project)
# ─────────────────────────────────────────────────────────────────────────────
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = (event.source.user_id
               if isinstance(event.source, UserSource)
               else str(event.source))
    text = event.message.text.strip()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        try:

            # ── reset ─────────────────────────────────────────────────────────
            if text.lower() in {"เริ่มใหม่","reset","ใหม่","สวัสดี","หวัดดี","hi","hello","start"}:
                sessions.pop(user_id, None)
                reply_msg(line_bot_api, event.reply_token,
                    "🏦 ยินดีต้อนรับสู่\nโปรแกรมวางแผนเกษียณ!\n\n"
                    "พิมพ์ 'เริ่ม' เพื่อเริ่มคำนวณ 🚀"
                )
                return

            # ── เริ่ม ─────────────────────────────────────────────────────────
            if text.lower() in {"เริ่ม","begin","คำนวณ","ลอง"}:
                sessions[user_id] = {"step": 0, "data": {}}
                reply_msg(line_bot_api, event.reply_token, QUESTIONS[0]["q"])
                return

            # ── ไม่มี session ─────────────────────────────────────────────────
            if user_id not in sessions:
                reply_msg(line_bot_api, event.reply_token,
                    "💬 พิมพ์ 'เริ่ม' เพื่อเริ่มวางแผนการเงิน 🏦\n"
                    "หรือ 'เริ่มใหม่' เพื่อ reset"
                )
                return

            # ── รับคำตอบทีละข้อ ───────────────────────────────────────────────
            session = sessions[user_id]
            step    = session["step"]
            data    = session["data"]
            q_key   = QUESTIONS[step]["key"]

            # NLP: ดึงตัวเลขจากข้อความ
            val = extract_number(text)

            if val is None or val < 0:
                reply_msg(line_bot_api, event.reply_token,
                    f"⚠️ กรุณากรอกตัวเลขให้ถูกต้อง\n\n{QUESTIONS[step]['q']}"
                )
                return

            if q_key == "risk_level" and int(val) not in (1, 2, 3):
                reply_msg(line_bot_api, event.reply_token,
                    f"⚠️ กรุณาพิมพ์ 1, 2 หรือ 3 เท่านั้น\n\n{QUESTIONS[step]['q']}"
                )
                return

            if q_key in {"current_age","retire_age","retire_years","risk_level"}:
                val = int(val)

            if q_key == "retire_age" and val <= data.get("current_age", 0):
                reply_msg(line_bot_api, event.reply_token,
                    f"⚠️ อายุเกษียณต้องมากกว่าอายุปัจจุบัน "
                    f"({data['current_age']} ปี)\n\n{QUESTIONS[step]['q']}"
                )
                return

            data[q_key] = val
            step += 1
            session["step"] = step

            if step < len(QUESTIONS):
                reply_msg(line_bot_api, event.reply_token, QUESTIONS[step]["q"])
                return

            # ── ครบแล้ว → คำนวณ ──────────────────────────────────────────────
            del sessions[user_id]
            results = calculate(data)

            reply_msg(line_bot_api, event.reply_token, results[0])
            if len(results) > 1:
                push_msg(line_bot_api, user_id, results[1:])

        except Exception as e:
            logger.exception(f"🔥 Error: {e}")
            reply_msg(line_bot_api, event.reply_token,
                "⚠️ เกิดข้อผิดพลาด กรุณาลองใหม่\n"
                "หรือพิมพ์ 'เริ่มใหม่'"
            )


# ─────────────────────────────────────────────────────────────────────────────
#  FastAPI App
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Financial Retirement Bot")


@app.get("/")
def root():
    return {"status": "ok", "message": "🚀 Financial Bot is running!"}


@app.get("/health")
def health_check():
    return {"status": "ok", "active_sessions": len(sessions)}


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    body      = await request.body()
    signature = request.headers.get("X-Line-Signature", "")
    background_tasks.add_task(process_event, body.decode("utf-8"), signature)
    return JSONResponse(content={"status": "ok"})


def process_event(body_text: str, signature: str):
    try:
        handler.handle(body_text, signature)
        logger.info("✅ Processed LINE event")
    except InvalidSignatureError:
        logger.warning("❌ Invalid signature — ตรวจสอบ LINE_CHANNEL_SECRET")
    except Exception as e:
        logger.exception(f"🔥 Error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
