"""
🏦 Financial Retirement Planning — LINE Bot
FastAPI + LINE SDK v3
Output ครบ 9 Section ตามเอกสาร
"""


import os, re, math, logging, uvicorn
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi,
    ReplyMessageRequest, PushMessageRequest, TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, UserSource
from linebot.v3.exceptions import InvalidSignatureError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("retirement-bot")

CHANNEL_SECRET       = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
configuration        = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler              = WebhookHandler(CHANNEL_SECRET)
sessions: dict[str, dict] = {}

QUESTIONS = [
    {"key": "current_age",      "q": "📋 1/10\n\n🎂 อายุปัจจุบัน (ปี)\nเช่น: 25 ปีครับ"},
    {"key": "retire_age",       "q": "📋 2/10\n\n🏖️ อายุที่ต้องการเกษียณ (ปี)\nเช่น: 60"},
    {"key": "life_expectancy",  "q": "📋 3/10\n\n👴 อายุคาดหวัง (ปี)\nเช่น: 85"},
    {"key": "monthly_income",   "q": "📋 4/10\n\n💵 รายรับต่อเดือน (บาท)\nเช่น: 25000"},
    {"key": "fixed_expense",    "q": "📋 5/10\n\n🏠 ค่าใช้จ่ายคงที่/เดือน (บาท)\nเช่น: 8000"},
    {"key": "variable_expense", "q": "📋 6/10\n\n🛒 ค่าใช้จ่ายผันแปร/เดือน (บาท)\nเช่น: 7000"},
    {"key": "current_saving",   "q": "📋 7/10\n\n🏦 เงินออมปัจจุบัน (บาท)\nเช่น: 20000"},
    {"key": "monthly_invest",   "q": "📋 8/10\n\n📈 ลงทุนต่อเดือน (บาท)\nเช่น: 3000"},
    {"key": "inflation_rate",   "q": "📋 9/10\n\n📊 อัตราเงินเฟ้อ (%/ปี)\nค่าแนะนำ: 3"},
    {"key": "risk_level",       "q": "📋 10/10\n\n⚖️ ระดับความเสี่ยง\n\n1️⃣ ต่ำ  (Low)    ~3%/ปี\n2️⃣ กลาง (Medium) ~5%/ปี\n3️⃣ สูง  (High)   ~7%/ปี\n\nพิมพ์ 1, 2 หรือ 3"},
]

RETURN_RATES = {1: 0.03, 2: 0.05, 3: 0.07}
RISK_NAMES   = {1: "ต่ำ (Low)", 2: "กลาง (Medium)", 3: "สูง (High)"}
ASSET_ALLOC  = {
    1: {"stock": 30,  "bond": 70},
    2: {"stock": 60,  "bond": 40},
    3: {"stock": 80,  "bond": 20},
}

# ── สูตรทั้งหมด ───────────────────────────────────────────────────────────────

def extract_number(text: str):
    cleaned = text.replace(",", "")
    match   = re.search(r'\d+(\.\d+)?', cleaned)
    return float(match.group()) if match else None

def fmt(n: float) -> str:
    return f"{n:,.0f}"

def fv_savings(pv, r, n):
    """สูตร 4: FV = PV x (1+r)^n"""
    return pv * (1 + r) ** n

def fv_monthly_invest(pmt, r, n):
    """สูตร 4: FV = PMT x [(1+r/12)^(12n)-1] / (r/12)"""
    rm = r / 12
    return pmt * (((1 + rm) ** (12 * n) - 1) / rm)

def required_fund_4pct(annual_exp):
    """สูตร 5.1: Retirement Fund = Annual Expense / 0.04"""
    return annual_exp / 0.04

def additional_monthly(gap, r, n):
    """สูตร 6.1: Additional = Gap x (r/12) / [(1+r/12)^(12n)-1]"""
    if gap <= 0: return 0.0
    rm = r / 12
    return gap * rm / (((1 + rm) ** (12 * n)) - 1)

def readiness_score(fv, req):
    return min(int((fv / req) * 100), 100) if req > 0 else 0

def score_label(s):
    if s >= 80: return "🟢 พร้อมเกษียณ (80-100)"
    if s >= 60: return "🟡 ใกล้เป้าหมาย (60-79)"
    return "🔴 ต้องปรับแผน (<60)"

# ── สร้างผลลัพธ์ 5 ข้อความ ───────────────────────────────────────────────────

def calculate(data: dict) -> list:
    age     = int(data["current_age"])
    retire  = int(data["retire_age"])
    life    = int(data["life_expectancy"])
    income  = float(data["monthly_income"])
    fixed   = float(data["fixed_expense"])
    var     = float(data["variable_expense"])
    saving  = float(data["current_saving"])
    m_inv   = float(data["monthly_invest"])
    infl    = float(data["inflation_rate"]) / 100
    risk    = int(data["risk_level"])

    r          = RETURN_RATES[risk]
    n_accum    = retire - age           # ระยะเวลาสะสมเงิน
    n_retire   = life - retire          # ระยะเวลาหลังเกษียณ
    total_exp  = fixed + var
    net_save   = income - total_exp
    save_ratio = net_save / income * 100

    # Section 2: Future Expense = Expense x (1.03)^n
    future_exp_m = total_exp * (1 + infl) ** n_accum
    future_exp_y = future_exp_m * 12   # Annual Retirement Expense

    # Section 5.1: Retirement Fund = Annual Expense / 0.04
    req_fund = required_fund_4pct(future_exp_y)

    # Section 4: FV เงินออม + FV ลงทุน
    fv_sav = fv_savings(saving, r, n_accum)
    fv_inv = fv_monthly_invest(m_inv, r, n_accum)
    fv_sum = fv_sav + fv_inv

    # Section 5.2: Gap = Required Fund - Future Investment
    gap    = req_fund - fv_sum

    # Section 6.1: Additional Monthly
    add_m  = additional_monthly(gap, r, n_accum)
    total_m = m_inv + add_m
    alloc  = ASSET_ALLOC[risk]

    # Section 7.2: Scenario Analysis
    sc1_fv   = fv_monthly_invest(m_inv + 2000, r, n_accum)
    sc1_diff = sc1_fv - fv_inv
    r2       = min(r + 0.02, 0.15)
    sc2_fv   = fv_savings(saving, r2, n_accum) + fv_monthly_invest(m_inv, r2, n_accum)
    sc2_diff = sc2_fv - fv_sum

    # Section 8: Tax Shield
    tax_save = min(m_inv * 12, 500000) * 0.20

    # Section 9: Retirement Readiness Score
    score  = readiness_score(fv_sum, req_fund)
    s_bar  = "⭐" * (score // 20) + "☆" * (5 - score // 20)

    # ── ข้อความที่ 1: Section 1 + 2 ──────────────────────────────────────────
    msg1 = (
        "═══════════════════════\n"
        "1️⃣ ข้อมูล & เป้าหมาย\n"
        "═══════════════════════\n"
        f"🎂 อายุปัจจุบัน:   {age} ปี\n"
        f"🏖️ อายุเกษียณ:    {retire} ปี\n"
        f"👴 อายุคาดหวัง:   {life} ปี\n\n"
        f"Years to Retire = {retire} - {age}\n"
        f"⏳ ระยะสะสมเงิน:  {n_accum} ปี\n\n"
        f"Years in Retirement = {life} - {retire}\n"
        f"🕰️ ระยะหลังเกษียณ: {n_retire} ปี\n\n"
        "═══════════════════════\n"
        "2️⃣ สถานะการเงิน\n"
        "═══════════════════════\n"
        "📍 ปัจจุบัน\n"
        f"💵 รายรับ/เดือน:    {fmt(income)} บาท\n"
        f"🏠 ค่าใช้จ่าย/เดือน: {fmt(total_exp)} บาท\n"
        f"🏦 เงินออมปัจจุบัน: {fmt(saving)} บาท\n"
        f"📈 ลงทุน/เดือน:    {fmt(m_inv)} บาท\n\n"
        f"Net Saving = {fmt(income)} - {fmt(total_exp)}\n"
        f"💰 เงินออมสุทธิ:   {fmt(net_save)} บาท/เดือน\n"
        f"📊 Saving Ratio:   {save_ratio:.1f}%\n\n"
        "📍 หลังเกษียณ (เงินเฟ้อ {:.0f}%)\n"
        "Future Expense = {:.0f} x (1+{:.2f})^{}\n"
        "💸 ค่าใช้จ่าย ณ เกษียณ:\n"
        "   {} บาท/เดือน\n"
        "   {} บาท/ปี"
    ).format(infl*100, total_exp, infl, n_accum, fmt(future_exp_m), fmt(future_exp_y))

    # ── ข้อความที่ 2: Section 3 + 4 + 5 ──────────────────────────────────────
    gap_icon = "🟢 เกินเป้าแล้ว!" if gap <= 0 else "🔴 ยังขาดอยู่"
    msg2 = (
        "═══════════════════════\n"
        "3️⃣ เป้าหมายเกษียณ\n"
        "═══════════════════════\n"
        f"Annual Expense = {fmt(future_exp_m)} x 12\n"
        f"              = {fmt(future_exp_y)} บาท/ปี\n\n"
        "═══════════════════════\n"
        "4️⃣ จำลองการเติบโต\n"
        f"({RISK_NAMES[risk]}, {r*100:.0f}%/ปี)\n"
        "═══════════════════════\n"
        f"FV เงินออมก้อน:\n"
        f"{fmt(saving)} x (1+{r})^{n_accum}\n"
        f"= {fmt(fv_sav)} บาท\n\n"
        f"FV ลงทุน DCA รายเดือน:\n"
        f"{fmt(m_inv)} x [(1+r/12)^(12x{n_accum})-1]/(r/12)\n"
        f"= {fmt(fv_inv)} บาท\n\n"
        f"💎 FV รวม: {fmt(fv_sum)} บาท\n\n"
        "═══════════════════════\n"
        "5️⃣ The Big Picture\n"
        "═══════════════════════\n"
        "5.1 Retirement Fund (4% Rule)\n"
        f"= {fmt(future_exp_y)} / 0.04\n"
        f"🏆 ต้องมี: {fmt(req_fund)} บาท\n\n"
        "5.2 Retirement Gap\n"
        f"= {fmt(req_fund)} - {fmt(fv_sum)}\n"
        f"= {fmt(gap)} บาท\n"
        f"{gap_icon}"
    )

    # ── ข้อความที่ 3: Section 6 ────────────────────────────────────────────────
    msg3 = (
        "═══════════════════════\n"
        "6️⃣ Action Plan\n"
        "═══════════════════════\n"
        "6.1 ต้องออมเพิ่มเท่าไร\n\n"
        "Additional = Gap x (r/12)\n"
        "           / [(1+r/12)^(12n)-1]\n\n"
        f"📌 ลงทุนเพิ่ม/เดือน:  {fmt(add_m)} บาท\n"
        f"📌 รวมลงทุน/เดือน:    {fmt(total_m)} บาท\n"
        f"📌 รวมลงทุน/ปี:       {fmt(total_m*12)} บาท\n\n"
        "═══════════════════════\n"
        "6.2 Asset Allocation\n"
        f"(Krungsri — {RISK_NAMES[risk]})\n"
        "═══════════════════════\n"
        f"📊 หุ้น:       {alloc['stock']}%\n"
        f"📊 ตราสารหนี้: {alloc['bond']}%\n\n"
        f"จากเงินออม {fmt(saving)} บาท:\n"
        f"หุ้น {alloc['stock']}% = {fmt(saving * alloc['stock'] / 100)} บาท\n"
        f"ตราสารหนี้ {alloc['bond']}% = {fmt(saving * alloc['bond'] / 100)} บาท"
    )

    # ── ข้อความที่ 4: Section 7 + 8 ───────────────────────────────────────────
    msg4 = (
        "═══════════════════════\n"
        "7️⃣ Dashboard\n"
        "═══════════════════════\n"
        "📈 7.1 สรุปตัวเลข\n"
        f"เส้นที่ 1 (มีอยู่): {fmt(fv_sum)} บาท\n"
        f"เส้นที่ 2 (ต้องมี): {fmt(req_fund)} บาท\n\n"
        "🔮 7.2 Scenario Analysis\n\n"
        "➜ ถ้าเพิ่มออม +2,000 บาท/เดือน\n"
        f"New FV = {fmt(sc1_fv)} บาท\n"
        f"เพิ่มขึ้น: +{fmt(sc1_diff)} บาท\n\n"
        f"➜ ถ้าผลตอบแทน +2% ({r2*100:.0f}%/ปี)\n"
        f"New FV = {fmt(sc2_fv)} บาท\n"
        f"เพิ่มขึ้น: +{fmt(sc2_diff)} บาท\n\n"
        "═══════════════════════\n"
        "8️⃣ Tax Shield\n"
        "═══════════════════════\n"
        "📌 SSF / RMF\n"
        "  ลดหย่อนรวมไม่เกิน 500,000 บาท/ปี\n\n"
        "📌 ประกันชีวิต + สุขภาพ\n"
        "  ลดหย่อนรวมได้ถึง 100,000 บาท/ปี\n\n"
        "📌 กองทุนสำรองเลี้ยงชีพ (PVD)\n"
        "  หักลดหย่อนได้ตามจ่ายจริง\n\n"
        f"💡 ประหยัดภาษีได้ ~{fmt(tax_save)} บาท/ปี\n"
        f"   (ใช้สิทธิ SSF/RMF, ภาษี 20%)"
    )

    # ── ข้อความที่ 5: Section 9 + Summary ────────────────────────────────────
    msg5 = (
        "═══════════════════════\n"
        "9️⃣ Retirement Readiness\n"
        "═══════════════════════\n"
        f"{s_bar}\n"
        f"คะแนน: {score}/100\n"
        f"สถานะ: {score_label(score)}\n\n"
        "═══════════════════════\n"
        "🎯 Output Summary\n"
        "═══════════════════════\n"
        f"👤 Profile:    อายุ {age} / เกษียณ {retire} / คาดหวัง {life} ปี\n"
        f"💸 Future Exp: {fmt(future_exp_m)} บาท/เดือน\n"
        f"🏆 Required:   {fmt(req_fund)} บาท\n"
        f"💎 Projected:  {fmt(fv_sum)} บาท\n"
        f"📉 Gap:        {fmt(gap)} บาท\n"
        f"📌 Add Monthly:{fmt(add_m)} บาท\n"
        f"📊 Allocation: หุ้น {alloc['stock']}% / ตราสารหนี้ {alloc['bond']}%\n"
        f"💡 Tax Saving: ~{fmt(tax_save)} บาท/ปี\n"
        f"⭐ Score:      {score}/100\n\n"
        "คำแนะนำ:\n"
        f"1. ลงทุนรวม {fmt(total_m)} บาท/เดือน\n"
        f"2. ใช้สิทธิ SSF/RMF ลดภาษี\n"
        f"3. รักษา Asset {alloc['stock']}/{alloc['bond']}\n"
        "4. ทบทวนแผนทุก 1 ปี 📅\n\n"
        "พิมพ์ 'เริ่ม' เพื่อคำนวณใหม่ 🔄"
    )

    return [msg1, msg2, msg3, msg4, msg5]

# ── LINE Handler ──────────────────────────────────────────────────────────────

def reply_msg(api, token, text):
    api.reply_message(ReplyMessageRequest(
        reply_token=token, messages=[TextMessage(text=text)]))

def push_msg(api, user_id, messages):
    for i in range(0, len(messages), 5):
        chunk = messages[i:i+5]
        api.push_message(PushMessageRequest(
            to=user_id, messages=[TextMessage(text=m) for m in chunk]))

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id if isinstance(event.source, UserSource) else str(event.source)
    text    = event.message.text.strip()

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        try:
            if text.lower() in {"เริ่มใหม่","reset","ใหม่","สวัสดี","หวัดดี","hi","hello","start"}:
                sessions.pop(user_id, None)
                reply_msg(line_bot_api, event.reply_token,
                    "🏦 ยินดีต้อนรับสู่โปรแกรมวางแผนเกษียณ!\n\nพิมพ์ 'เริ่ม' เพื่อเริ่มคำนวณ 🚀")
                return

            if text.lower() in {"เริ่ม","begin","คำนวณ","ลอง"}:
                sessions[user_id] = {"step": 0, "data": {}}
                reply_msg(line_bot_api, event.reply_token, QUESTIONS[0]["q"])
                return

            if user_id not in sessions:
                reply_msg(line_bot_api, event.reply_token,
                    "💬 พิมพ์ 'เริ่ม' เพื่อเริ่มวางแผนการเงิน 🏦\nหรือ 'เริ่มใหม่' เพื่อ reset")
                return

            session = sessions[user_id]
            step    = session["step"]
            data    = session["data"]
            q_key   = QUESTIONS[step]["key"]
            val     = extract_number(text)

            if val is None or val < 0:
                reply_msg(line_bot_api, event.reply_token,
                    f"⚠️ กรุณากรอกตัวเลขให้ถูกต้อง\n\n{QUESTIONS[step]['q']}")
                return

            if q_key == "risk_level" and int(val) not in (1, 2, 3):
                reply_msg(line_bot_api, event.reply_token,
                    f"⚠️ กรุณาพิมพ์ 1, 2 หรือ 3\n\n{QUESTIONS[step]['q']}")
                return

            if q_key == "retire_age" and val <= data.get("current_age", 0):
                reply_msg(line_bot_api, event.reply_token,
                    f"⚠️ อายุเกษียณต้องมากกว่าอายุปัจจุบัน ({int(data['current_age'])} ปี)\n\n{QUESTIONS[step]['q']}")
                return

            if q_key == "life_expectancy" and val <= data.get("retire_age", 0):
                reply_msg(line_bot_api, event.reply_token,
                    f"⚠️ อายุคาดหวังต้องมากกว่าอายุเกษียณ ({int(data['retire_age'])} ปี)\n\n{QUESTIONS[step]['q']}")
                return

            if q_key in {"current_age","retire_age","life_expectancy","risk_level"}:
                val = int(val)

            data[q_key] = val
            step += 1
            session["step"] = step

            if step < len(QUESTIONS):
                reply_msg(line_bot_api, event.reply_token, QUESTIONS[step]["q"])
                return

            # ครบแล้ว → คำนวณ
            del sessions[user_id]
            reply_msg(line_bot_api, event.reply_token,
                "ยอดเยี่ยมครับ 👍 ข้อมูลทั้งหมดได้รับแล้วครับ\n"
                "ผมกำลังคำนวณผลลัพธ์ตามเป้าหมายการเกษียณของคุณ...")
            results = calculate(data)
            push_msg(line_bot_api, user_id, results)

        except Exception as e:
            logger.exception(f"Error: {e}")
            reply_msg(line_bot_api, event.reply_token,
                "⚠️ เกิดข้อผิดพลาด กรุณาพิมพ์ 'เริ่มใหม่'")

from linebot.v3.webhooks import ImageMessageContent, VideoMessageContent, AudioMessageContent

@handler.add(MessageEvent, message=ImageMessageContent)
@handler.add(MessageEvent, message=VideoMessageContent)
@handler.add(MessageEvent, message=AudioMessageContent)
def handle_non_text(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)

        reply_msg(
            line_bot_api,
            event.reply_token,
            "⚠️ กรุณาระบุเป็นข้อความเท่านั้นครับ"
        )

# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Financial Retirement Bot")

@app.get("/")
def root():
    return {"status": "ok", "message": "Financial Bot is running!"}

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
    except InvalidSignatureError:
        logger.warning("Invalid signature")
    except Exception as e:
        logger.exception(f"Error: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
  
