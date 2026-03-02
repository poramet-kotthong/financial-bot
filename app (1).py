"""
🏦 Financial Retirement Planning — LINE Bot
FastAPI + LINE SDK (ไม่ผ่าน Make)

Flow: LINE User → LINE Webhook → FastAPI → LINE SDK reply
"""

import os
import re
import hashlib
import hmac
import base64
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
import httpx

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─── Config from Environment Variables ───────────────────────────────────────
LINE_CHANNEL_SECRET      = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_REPLY_URL           = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL            = "https://api.line.me/v2/bot/message/push"

# ─── In-memory sessions ───────────────────────────────────────────────────────
sessions: dict[str, dict] = {}

# ─── Questions ───────────────────────────────────────────────────────────────
QUESTIONS = [
    {"key": "current_age",        "q": "📋 1/9\n\n🎂 อายุปัจจุบัน (ปี)\nเช่น: 18"},
    {"key": "retire_age",         "q": "📋 2/9\n\n🏖️ อายุที่ต้องการเกษียณ (ปี)\nเช่น: 50"},
    {"key": "retire_years",       "q": "📋 3/9\n\n⏳ ระยะเวลาหลังเกษียณ (ปี)\nเช่น: 10"},
    {"key": "monthly_income",     "q": "📋 4/9\n\n💵 รายได้ต่อเดือน (บาท)\nเช่น: 50000"},
    {"key": "fixed_expense",      "q": "📋 5/9\n\n🏠 ค่าใช้จ่ายคงที่/เดือน (บาท)\nเช่น: ค่าเช่า, ผ่อนรถ → 5000"},
    {"key": "variable_expense",   "q": "📋 6/9\n\n🛒 ค่าใช้จ่ายผันแปร/เดือน (บาท)\nเช่น: อาหาร, เดินทาง → 2000"},
    {"key": "current_investment", "q": "📋 7/9\n\n🏦 เงินออม/ลงทุนที่มีอยู่ (บาท)\nเช่น: 100000"},
    {"key": "inflation_rate",     "q": "📋 8/9\n\n📈 อัตราเงินเฟ้อ (%/ปี)\nค่าแนะนำ: 3"},
    {"key": "risk_level",         "q": "📋 9/9\n\n⚖️ ระดับความเสี่ยงการลงทุน\n\n1️⃣ ต่ำ  (Conservative) ~4%/ปี\n2️⃣ กลาง (Moderate)     ~6%/ปี\n3️⃣ สูง  (Aggressive)   ~8%/ปี\n\nพิมพ์ 1, 2 หรือ 3"},
]

PORTFOLIO = {
    1: {"name": "ต่ำ (Conservative)", "avg_return": 0.04,
        "assets": [("เงินสด/ออมทรัพย์",0.30,0.01),("กองทุนตราสารหนี้",0.50,0.04),("กองทุนหุ้น",0.15,0.09),("กองทุนธีม/ทอง",0.05,0.10)]},
    2: {"name": "กลาง (Moderate)",    "avg_return": 0.06,
        "assets": [("เงินสด/ออมทรัพย์",0.10,0.01),("กองทุนตราสารหนี้",0.35,0.04),("กองทุนหุ้น",0.45,0.09),("กองทุนธีม/ทอง",0.10,0.10)]},
    3: {"name": "สูง (Aggressive)",   "avg_return": 0.08,
        "assets": [("เงินสด/ออมทรัพย์",0.05,0.01),("กองทุนตราสารหนี้",0.15,0.04),("กองทุนหุ้น",0.70,0.09),("กองทุนธีม/ทอง",0.10,0.10)]},
}

# ─── Helpers ──────────────────────────────────────────────────────────────────
def fmt(n: float) -> str:
    return f"{n:,.0f}"

def extract_number(text: str):
    """
    ดึงตัวเลขออกจากข้อความภาษาไทย/อังกฤษ
    รองรับทุกรูปแบบที่ user อาจพิมพ์มา เช่น:
      "18"          → 18.0
      "18 ปีครับ"   → 18.0
      "อายุ 18 ปี"  → 18.0
      "50,000 บาท"  → 50000.0
      "ประมาณ 3%"   → 3.0
    """
    # ลบ comma ออกก่อน เช่น 50,000 → 50000
    cleaned = text.replace(",", "")
    # ค้นหาตัวเลข (รองรับทศนิยม)
    match = re.search(r'\d+(\.\d+)?', cleaned)
    if match:
        return float(match.group())
    return None

def verify_signature(body: bytes, signature: str) -> bool:
    """ยืนยันว่า Request มาจาก LINE จริง"""
    if not LINE_CHANNEL_SECRET:
        return True  # dev mode
    h = hmac.new(LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(h).decode(), signature)

async def line_reply(reply_token: str, messages: list[str]):
    """ส่งข้อความกลับ LINE ผ่าน Reply API (ฟรี, ใช้ reply token)"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    # LINE Reply รองรับสูงสุด 5 ข้อความต่อครั้ง
    payload = {
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": m} for m in messages[:5]],
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(LINE_REPLY_URL, headers=headers, json=payload)
        if r.status_code != 200:
            log.error("LINE reply error: %s %s", r.status_code, r.text)

async def line_push(user_id: str, messages: list[str]):
    """ส่งข้อความเพิ่มเติมผ่าน Push API (ใช้เมื่อข้อความ > 5 หรือ reply token หมด)"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    for i in range(0, len(messages), 5):
        chunk = messages[i:i+5]
        payload = {
            "to": user_id,
            "messages": [{"type": "text", "text": m} for m in chunk],
        }
        async with httpx.AsyncClient() as client:
            await client.post(LINE_PUSH_URL, headers=headers, json=payload)

# ─── Core Calculation ─────────────────────────────────────────────────────────
def calculate(data: dict) -> list[str]:
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

    # FV เงินเฟ้อ
    exp_at_retire = total_exp * (1 + infl) ** years_left
    # เป้าหมายเงินก้อน
    target = exp_at_retire * 12 * ryears
    # เงินออมที่ต้องการ
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

    feasible = "✅ เป็นไปได้!" if net_saving >= monthly_needed else "⚠️ ต้องปรับแผน"
    surplus  = fv - target
    result   = "🚀 เกินเป้า!" if surplus > 0 else "⚠️ ต่ำกว่าเป้า"

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
        f"💸 ค่าใช้จ่าย ณ เกษียณ:\n   {fmt(exp_at_retire)} บาท/เดือน"
    )

    msg2 = (
        "═══════════════════\n"
        "🎯 เป้าหมายเกษียณ\n"
        "═══════════════════\n"
        f"สูตร: {fmt(exp_at_retire)} × 12 × {ryears}\n"
        f"🏆 เงินก้อนที่ต้องมี:\n   {fmt(target)} บาท\n\n"
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
        port_lines += f"• {name} {pct*100:.0f}%\n  {fmt(amt)} บาท → {fmt(amt*ret)} บาท/ปี\n"
    port_lines += f"\n📈 รวม: {w_ret:.2f}%/ปี = {fmt(total_ret)} บาท/ปี"

    growth_txt = (
        "═══════════════════\n"
        f"📈 จำลองการเติบโต ({avg_return*100:.0f}%/ปี)\n"
        "═══════════════════\n"
    )
    for a, v in growth_rows:
        growth_txt += f"อายุ {a:2d} ปี → {fmt(v)} บาท\n"

    surplus_str = f"+{fmt(surplus)}" if surplus > 0 else fmt(surplus)
    growth_txt += (
        "\n═══════════════════\n"
        "📌 สรุป\n"
        "═══════════════════\n"
        f"💎 มูลค่าตอนเกษียณ:\n   {fmt(fv)} บาท\n"
        f"🎯 เป้าหมาย:\n   {fmt(target)} บาท\n"
        f"{result} ({surplus_str} บาท)\n\n"
        "✅ คำแนะนำ:\n"
        f"1. ออมอัตโนมัติ {fmt(monthly_needed)} บาท/เดือน\n"
        f"2. สำรองฉุกเฉิน {fmt(total_exp*6)} บาท\n"
        f"3. อายุ {retire-5}+ ปี ลดสัดส่วนหุ้น\n"
        "4. ทบทวนแผนทุก 1 ปี 📅\n\n"
        "พิมพ์ 'เริ่ม' เพื่อคำนวณใหม่ 🔄"
    )

    return [msg1, msg2, port_lines, growth_txt]

# ─── Session Handler ───────────────────────────────────────────────────────────
async def handle_message(user_id: str, text: str, reply_token: str):
    text = text.strip()

    # คำสั่ง reset / เริ่มใหม่
    if text.lower() in {"เริ่มใหม่","reset","ใหม่","สวัสดี","หวัดดี","hi","hello","start"}:
        sessions.pop(user_id, None)
        await line_reply(reply_token, [
            "🏦 ยินดีต้อนรับสู่\nโปรแกรมวางแผนเกษียณ!\n\nพิมพ์ 'เริ่ม' เพื่อเริ่มคำนวณ 🚀"
        ])
        return

    if text.lower() in {"เริ่ม","begin","คำนวณ","ลอง"}:
        sessions[user_id] = {"step": 0, "data": {}}
        await line_reply(reply_token, [QUESTIONS[0]["q"]])
        return

    # ไม่มี session
    if user_id not in sessions:
        await line_reply(reply_token, [
            "💬 พิมพ์ 'เริ่ม' เพื่อเริ่มวางแผนการเงิน 🏦\n\nหรือ 'เริ่มใหม่' เพื่อ reset"
        ])
        return

    session = sessions[user_id]
    step    = session["step"]
    data    = session["data"]
    q_key   = QUESTIONS[step]["key"]

    # ดึงตัวเลขออกจากข้อความ (รองรับ "18 ปีครับ", "50,000 บาท" ฯลฯ)
    try:
        val = extract_number(text)
        if val is None or val < 0:
            raise ValueError
        if q_key == "risk_level" and int(val) not in (1, 2, 3):
            raise ValueError
        if q_key in {"current_age","retire_age","retire_years","risk_level"}:
            val = int(val)
    except ValueError:
        err = "⚠️ กรุณากรอกตัวเลขที่ถูกต้อง"
        if q_key == "risk_level":
            err = "⚠️ กรุณาพิมพ์ 1, 2 หรือ 3 เท่านั้น"
        await line_reply(reply_token, [f"{err}\n\n{QUESTIONS[step]['q']}"])
        return

    # validate retire_age > current_age
    if q_key == "retire_age" and int(val) <= data.get("current_age", 0):
        await line_reply(reply_token, [f"⚠️ อายุเกษียณต้องมากกว่าอายุปัจจุบัน ({data['current_age']} ปี)\n\n{QUESTIONS[step]['q']}"])
        return

    data[q_key] = val
    step += 1
    session["step"] = step

    if step < len(QUESTIONS):
        await line_reply(reply_token, [QUESTIONS[step]["q"]])
        return

    # ครบ → คำนวณ
    del sessions[user_id]
    results = calculate(data)
    # ส่งข้อความแรกผ่าน Reply (ฟรี), ที่เหลือผ่าน Push
    await line_reply(reply_token, [results[0]])
    if len(results) > 1:
        await line_push(user_id, results[1:])

# ─── FastAPI App ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("🚀 Financial Bot started")
    yield
    log.info("Bot stopped")

app = FastAPI(title="Financial Retirement Bot", lifespan=lifespan)

@app.get("/")
async def root():
    return {"status": "ok", "bot": "Financial Retirement Planner", "sessions": len(sessions)}

@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(sessions)}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    """
    LINE Webhook endpoint
    LINE จะส่ง POST มาที่นี่ทุกครั้งที่มีข้อความ
    """
    body      = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    # ยืนยัน signature
    if LINE_CHANNEL_SECRET and not verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    import json
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # ส่งไป background task เพื่อตอบ LINE ภายใน 3 วินาที
    for event in payload.get("events", []):
        if event.get("type") == "message" and event["message"].get("type") == "text":
            user_id     = event["source"]["userId"]
            text        = event["message"]["text"]
            reply_token = event["replyToken"]
            background_tasks.add_task(handle_message, user_id, text, reply_token)

    # ตอบ LINE ทันที (จำเป็น ไม่งั้น timeout)
    return JSONResponse(content={"status": "ok"})
