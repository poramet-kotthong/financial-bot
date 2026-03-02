"""
🏦 Financial Retirement Planning — LINE Bot
FastAPI + LINE SDK v3

สูตรครบทุกข้อ:
  1.1 ค่าใช้จ่ายต่อปี
  1.2 เงินที่ต้องใช้ทั้งหมด
  2.1 FV เงินเฟ้อ
  3.1 4% Rule
  4.1/4.2 เงินออม
  5.1/5.2/5.3 ทบต้น + DCA
  6 PMT ต้องออมเท่าไร
  7 ระยะเวลาถึงเป้า
  8 Real Return
  9 ถอนเงิน 4%
  10 Net Growth
  11 สูตรสรุปใหญ่
"""

import os
import re
import math
import logging
import uvicorn

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, UserSource
from linebot.v3.exceptions import InvalidSignatureError

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("financial-bot")

# ─── LINE Config ───────────────────────────────────────────────────────────────
CHANNEL_SECRET        = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN  = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
configuration         = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler               = WebhookHandler(CHANNEL_SECRET)

# ─── Sessions ──────────────────────────────────────────────────────────────────
sessions: dict[str, dict] = {}

# ─── คำถาม 9 ข้อ ──────────────────────────────────────────────────────────────
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

RETURN_RATES = {1: 0.04, 2: 0.06, 3: 0.08}
RISK_NAMES   = {1: "ต่ำ (Conservative)", 2: "กลาง (Moderate)", 3: "สูง (Aggressive)"}


# ═══════════════════════════════════════════════════════════════════════════════
#  สูตรคำนวณทั้งหมด
# ═══════════════════════════════════════════════════════════════════════════════

def extract_number(text: str):
    """NLP: ดึงตัวเลขจากข้อความ เช่น '18 ปีครับ' → 18"""
    cleaned = text.replace(",", "")
    match   = re.search(r'\d+(\.\d+)?', cleaned)
    return float(match.group()) if match else None

def fmt(n: float) -> str:
    return f"{n:,.0f}"

# สูตร 1.1
def annual_expense(monthly: float) -> float:
    """ค่าใช้จ่ายต่อปี = ค่าใช้จ่ายต่อเดือน × 12"""
    return monthly * 12

# สูตร 1.2
def total_need_simple(annual_exp: float, retire_years: int) -> float:
    """เงินที่ต้องใช้ทั้งหมด = ค่าใช้จ่ายต่อปี × จำนวนปีหลังเกษียณ"""
    return annual_exp * retire_years

# สูตร 2.1 / 2.2
def fv_inflation(pv: float, inflation: float, n: int) -> float:
    """FV = PV × (1 + i)^n"""
    return pv * (1 + inflation) ** n

# สูตร 3.1
def retirement_fund_4pct(annual_exp_future: float) -> float:
    """เงินที่ต้องมี = ค่าใช้จ่ายต่อปี / 0.04"""
    return annual_exp_future / 0.04

# สูตร 4.1 / 4.2
def saving_per_month(income: float, expense: float) -> float:
    return income - expense

def saving_per_year(monthly: float) -> float:
    return monthly * 12

# สูตร 5.1
def fv_lump_sum(pv: float, r: float, n: int) -> float:
    """FV = PV × (1 + r)^n"""
    return pv * (1 + r) ** n

# สูตร 5.2
def fv_dca_annual(p: float, r: float, n: int) -> float:
    """FV = P × [(1+r)^n - 1] / r"""
    return p * (((1 + r) ** n - 1) / r)

# สูตร 5.3
def fv_dca_monthly(pmt: float, r: float, n: int) -> float:
    """FV = PMT × [(1+r/12)^(12n) - 1] / (r/12)"""
    rm = r / 12
    return pmt * (((1 + rm) ** (12 * n) - 1) / rm)

# สูตร 6
def required_pmt_monthly(fv: float, r: float, n: int) -> float:
    """PMT = FV × (r/12) / [(1+r/12)^(12n) - 1]"""
    rm = r / 12
    return fv * rm / (((1 + rm) ** (12 * n)) - 1)

# สูตร 7
def years_to_goal(fv: float, pv: float, r: float) -> float:
    """n = ln(FV/PV) / ln(1+r)"""
    if pv <= 0 or r <= 0:
        return 0
    return math.log(fv / pv) / math.log(1 + r)

# สูตร 8
def real_return(r: float, inflation: float) -> float:
    """Real Return = (1+r)/(1+i) - 1"""
    return (1 + r) / (1 + inflation) - 1

# สูตร 9
def annual_withdrawal(total_fund: float, rate=0.04) -> float:
    """เงินที่ใช้ต่อปี = เงินทั้งหมด × อัตราถอนเงิน"""
    return total_fund * rate

# สูตร 10
def net_growth(r: float, inflation: float) -> float:
    """Net Growth = ผลตอบแทน - เงินเฟ้อ"""
    return r - inflation

# สูตร 11 — สูตรสรุปใหญ่
def retirement_full_calc(expense_now: float, inflation: float, n: int,
                          r: float, current_fund: float) -> dict:
    """
    Step 1: Future Expense = Expense × (1+i)^n
    Step 2: Retirement Fund = Future Expense × 12 / 0.04
    Step 3: PMT = (FV - FV_ปัจจุบัน) × (r/12) / [(1+r/12)^(12n)-1]
    """
    future_exp       = fv_inflation(expense_now, inflation, n)
    retire_fund      = retirement_fund_4pct(annual_expense(future_exp))
    fv_current       = fv_lump_sum(current_fund, r, n)
    need_from_saving = max(retire_fund - fv_current, 0)
    pmt = required_pmt_monthly(need_from_saving, r, n) if need_from_saving > 0 else 0
    return {
        "future_exp":       future_exp,
        "retire_fund":      retire_fund,
        "fv_current":       fv_current,
        "need_from_saving": need_from_saving,
        "pmt_monthly":      pmt,
    }

# ตาราง Growth
def growth_table(current_fund: float, pmt: float,
                 r: float, age_now: int, retire_age: int) -> list:
    n_total      = retire_age - age_now
    checkpoints  = sorted(set([age_now] +
                              list(range(age_now + 1, retire_age + 1, 5)) +
                              [retire_age]))
    rows = []
    for age in checkpoints:
        n = age - age_now
        if n == 0:
            rows.append((age, current_fund))
        else:
            v = fv_lump_sum(current_fund, r, n) + fv_dca_monthly(pmt, r, n)
            rows.append((age, v))
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
#  สร้างข้อความผลลัพธ์ 4 ข้อความ
# ═══════════════════════════════════════════════════════════════════════════════

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

    n         = retire - age
    r         = RETURN_RATES[risk]
    total_exp = fixed + var

    # ── สูตร 4.1 / 4.2 ─────────────────────────────────────────────────────
    net_save    = saving_per_month(income, total_exp)
    save_ratio  = net_save / income * 100
    annual_save = saving_per_year(net_save)

    # ── สูตร 2.1 ───────────────────────────────────────────────────────────
    future_exp_m = fv_inflation(total_exp, infl, n)
    future_exp_y = annual_expense(future_exp_m)

    # ── สูตร 1.2 ───────────────────────────────────────────────────────────
    simple_need = total_need_simple(future_exp_y, ryears)

    # ── สูตร 3.1 + 11 ──────────────────────────────────────────────────────
    result = retirement_full_calc(total_exp, infl, n, r, invest)

    # ── สูตร 7 ─────────────────────────────────────────────────────────────
    yrs_goal = years_to_goal(result["retire_fund"], max(invest, 1), r)

    # ── สูตร 8 / 10 ────────────────────────────────────────────────────────
    real_r = real_return(r, infl)
    net_g  = net_growth(r, infl)

    # ── สูตร 9 ─────────────────────────────────────────────────────────────
    withdrawal_y = annual_withdrawal(result["retire_fund"])
    withdrawal_m = withdrawal_y / 12

    # ── ตาราง growth ────────────────────────────────────────────────────────
    table    = growth_table(invest, result["pmt_monthly"], r, age, retire)
    final_fv = table[-1][1]
    surplus  = final_fv - result["retire_fund"]

    feasible    = "✅ เป็นไปได้!" if net_save >= result["pmt_monthly"] else "⚠️ ต้องปรับแผน"
    result_icon = "🚀 เกินเป้า!" if surplus > 0 else "⚠️ ต่ำกว่าเป้า"
    surplus_str = f"+{fmt(surplus)}" if surplus > 0 else fmt(surplus)

    # ── ข้อความที่ 1: การออม + เงินเฟ้อ ────────────────────────────────────
    msg1 = (
        "═══════════════════════\n"
        "📊 สถานะการเงิน\n"
        "═══════════════════════\n"
        f"🎂 อายุ {age} → เกษียณ {retire} ปี\n"
        f"⏳ เหลือเวลา: {n} ปี\n\n"
        "── สูตร 4.1 การออม ──\n"
        f"💵 รายได้:        {fmt(income)} บาท/เดือน\n"
        f"🏠 ค่าใช้จ่าย:   {fmt(total_exp)} บาท/เดือน\n"
        f"💰 ออมได้:        {fmt(net_save)} บาท/เดือน\n"
        f"💰 ออมได้/ปี:     {fmt(annual_save)} บาท\n"
        f"📊 Saving Ratio: {save_ratio:.1f}%\n\n"
        "── สูตร 2.1 เงินเฟ้อ ──\n"
        f"FV = {fmt(total_exp)} × (1+{infl})^{n}\n"
        f"💸 ค่าใช้จ่าย ณ เกษียณ:\n"
        f"   {fmt(future_exp_m)} บาท/เดือน\n"
        f"   {fmt(future_exp_y)} บาท/ปี"
    )

    # ── ข้อความที่ 2: เป้าหมาย + PMT ──────────────────────────────────────
    msg2 = (
        "═══════════════════════\n"
        "🏦 เป้าหมายเกษียณ\n"
        "═══════════════════════\n"
        "── สูตร 1.2 (แบบง่าย) ──\n"
        f"ค่าใช้จ่าย/ปี × {ryears} ปี\n"
        f"💡 ต้องใช้ทั้งหมด: {fmt(simple_need)} บาท\n\n"
        "── สูตร 3.1 (4% Rule) ──\n"
        f"ค่าใช้จ่าย/ปี ÷ 0.04\n"
        f"🏆 เงินก้อนที่ต้องมี:\n"
        f"   {fmt(result['retire_fund'])} บาท\n\n"
        "── สูตร 11 สรุปใหญ่ ──\n"
        f"FV เงินปัจจุบัน:  {fmt(result['fv_current'])} บาท\n"
        f"ต้องหาจากการออม: {fmt(result['need_from_saving'])} บาท\n\n"
        "── สูตร 6 PMT ──\n"
        f"📌 ต้องออม/เดือน: {fmt(result['pmt_monthly'])} บาท\n"
        f"📌 ต้องออม/ปี:    {fmt(result['pmt_monthly']*12)} บาท\n"
        f"📌 ต้องออม/วัน:   {fmt(result['pmt_monthly']*12/365)} บาท\n\n"
        f"เงินเหลือ: {fmt(net_save)} บาท/เดือน\n"
        f"ต้องออม:   {fmt(result['pmt_monthly'])} บาท/เดือน\n"
        f"➡️ {feasible}"
    )

    # ── ข้อความที่ 3: การลงทุน + ตาราง Growth ──────────────────────────────
    msg3 = (
        "═══════════════════════\n"
        f"📈 จำลองการเติบโต\n"
        f"({RISK_NAMES[risk]}, {r*100:.0f}%/ปี)\n"
        "═══════════════════════\n"
        "── สูตร 5.1 + 5.3 DCA ──\n"
        "FV = ก้อน×(1+r)^n + PMT×[(1+r/12)^(12n)-1]/(r/12)\n\n"
    )
    for a, v in table:
        msg3 += f"อายุ {a:2d} ปี → {fmt(v)} บาท\n"
    msg3 += (
        f"\n💎 มูลค่าเมื่อเกษียณ:\n"
        f"   {fmt(final_fv)} บาท"
    )

    # ── ข้อความที่ 4: สูตรเพิ่มเติม + สรุป ────────────────────────────────
    msg4 = (
        "═══════════════════════\n"
        "🧮 สูตรเพิ่มเติม\n"
        "═══════════════════════\n"
        "── สูตร 7 ระยะเวลาถึงเป้า ──\n"
        f"n = ln(FV/PV) / ln(1+r)\n"
        f"ถ้าลงทุนแค่ {fmt(invest)} บาท\n"
        f"⏱️ ใช้เวลา {yrs_goal:.1f} ปี ถึงเป้า\n\n"
        "── สูตร 8 Real Return ──\n"
        f"(1+{r}) / (1+{infl}) - 1\n"
        f"📉 Real Return: {real_r*100:.2f}%\n\n"
        "── สูตร 10 Net Growth ──\n"
        f"{r*100:.0f}% - {infl*100:.0f}% = {net_g*100:.0f}%\n\n"
        "── สูตร 9 ถอนเงิน 4% ──\n"
        f"หลังเกษียณถอนได้:\n"
        f"💵 {fmt(withdrawal_y)} บาท/ปี\n"
        f"💵 {fmt(withdrawal_m)} บาท/เดือน\n\n"
        "═══════════════════════\n"
        "📌 สรุปผล\n"
        "═══════════════════════\n"
        f"🏆 เป้า (4% Rule): {fmt(result['retire_fund'])} บาท\n"
        f"💎 คาดว่าจะมี:     {fmt(final_fv)} บาท\n"
        f"{result_icon}\n"
        f"ส่วนต่าง: {surplus_str} บาท\n\n"
        "✅ คำแนะนำ:\n"
        f"1. ออมอัตโนมัติ {fmt(result['pmt_monthly'])} บาท/เดือน\n"
        f"2. สำรองฉุกเฉิน {fmt(total_exp*6)} บาท\n"
        f"3. อายุ {retire-5}+ ปี ลดสัดส่วนหุ้น\n"
        "4. ทบทวนแผนทุก 1 ปี 📅\n\n"
        "พิมพ์ 'เริ่ม' เพื่อคำนวณใหม่ 🔄"
    )

    return [msg1, msg2, msg3, msg4]


# ═══════════════════════════════════════════════════════════════════════════════
#  LINE Handler
# ═══════════════════════════════════════════════════════════════════════════════

def reply_msg(api: MessagingApi, token: str, text: str):
    api.reply_message(ReplyMessageRequest(
        reply_token=token,
        messages=[TextMessage(text=text)],
    ))

def push_msg(api: MessagingApi, user_id: str, messages: list):
    for i in range(0, len(messages), 5):
        chunk = messages[i:i+5]
        api.push_message(PushMessageRequest(
            to=user_id,
            messages=[TextMessage(text=m) for m in chunk],
        ))


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = (event.source.user_id
               if isinstance(event.source, UserSource)
               else str(event.source))
    text = event.message.text.strip()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        try:
            # ── reset ──────────────────────────────────────────────────────
            if text.lower() in {"เริ่มใหม่","reset","ใหม่","สวัสดี","หวัดดี","hi","hello","start"}:
                sessions.pop(user_id, None)
                reply_msg(line_bot_api, event.reply_token,
                    "🏦 ยินดีต้อนรับสู่\nโปรแกรมวางแผนเกษียณ!\n\n"
                    "พิมพ์ 'เริ่ม' เพื่อเริ่มคำนวณ 🚀"
                )
                return

            # ── เริ่ม ──────────────────────────────────────────────────────
            if text.lower() in {"เริ่ม","begin","คำนวณ","ลอง"}:
                sessions[user_id] = {"step": 0, "data": {}}
                reply_msg(line_bot_api, event.reply_token, QUESTIONS[0]["q"])
                return

            # ── ไม่มี session ───────────────────────────────────────────────
            if user_id not in sessions:
                reply_msg(line_bot_api, event.reply_token,
                    "💬 พิมพ์ 'เริ่ม' เพื่อเริ่มวางแผนการเงิน 🏦\n"
                    "หรือ 'เริ่มใหม่' เพื่อ reset"
                )
                return

            # ── รับคำตอบทีละข้อ ────────────────────────────────────────────
            session = sessions[user_id]
            step    = session["step"]
            data    = session["data"]
            q_key   = QUESTIONS[step]["key"]

            # NLP: ดึงตัวเลขจากข้อความ
            val = extract_number(text)

            if val is None or val < 0:
                reply_msg(line_bot_api, event.reply_token,
                    f"⚠️ กรุณากรอกตัวเลขให้ถูกต้อง\n\n{QUESTIONS[step]['q']}")
                return

            if q_key == "risk_level" and int(val) not in (1, 2, 3):
                reply_msg(line_bot_api, event.reply_token,
                    f"⚠️ กรุณาพิมพ์ 1, 2 หรือ 3\n\n{QUESTIONS[step]['q']}")
                return

            if q_key in {"current_age","retire_age","retire_years","risk_level"}:
                val = int(val)

            if q_key == "retire_age" and val <= data.get("current_age", 0):
                reply_msg(line_bot_api, event.reply_token,
                    f"⚠️ อายุเกษียณต้องมากกว่าอายุปัจจุบัน "
                    f"({data['current_age']} ปี)\n\n{QUESTIONS[step]['q']}")
                return

            data[q_key] = val
            step += 1
            session["step"] = step

            if step < len(QUESTIONS):
                reply_msg(line_bot_api, event.reply_token, QUESTIONS[step]["q"])
                return

            # ── ครบทุกข้อ → คำนวณ ─────────────────────────────────────────
            del sessions[user_id]
            results = calculate(data)
            reply_msg(line_bot_api, event.reply_token, results[0])
            if len(results) > 1:
                push_msg(line_bot_api, user_id, results[1:])

        except Exception as e:
            logger.exception(f"🔥 Error: {e}")
            reply_msg(line_bot_api, event.reply_token,
                "⚠️ เกิดข้อผิดพลาด กรุณาลองใหม่\n"
                "หรือพิมพ์ 'เริ่มใหม่'")


# ═══════════════════════════════════════════════════════════════════════════════
#  FastAPI
# ═══════════════════════════════════════════════════════════════════════════════

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
        logger.warning("❌ Invalid signature")
    except Exception as e:
        logger.exception(f"🔥 Error: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
