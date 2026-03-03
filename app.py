"""
🏦 KrungsriRetire Bot — LINE Bot วางแผนเกษียณ
FastAPI + LINE SDK v3  |  v2.0

Input  : 9 คำถาม (รวม 8.1 + 8.2)
Output : 4 ข้อความ / 9 หัวข้อ
         1. Profile & สถานะการเงิน
         2. เป้าหมายเกษียณ + Growth Table
         3. Big Picture + Action Plan
         4. Scenario / Tax Shield / ผลิตภัณฑ์กรุงศรี
"""

import os, re, math, logging
import uvicorn
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi,
    ReplyMessageRequest, PushMessageRequest, TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, UserSource
from linebot.v3.exceptions import InvalidSignatureError

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("krungsri-retire")

# ─── LINE Config ───────────────────────────────────────────────────────────────
CHANNEL_SECRET       = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
configuration        = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler              = WebhookHandler(CHANNEL_SECRET)

# ─── Session Store ─────────────────────────────────────────────────────────────
sessions: dict[str, dict] = {}

# ─── Constants ─────────────────────────────────────────────────────────────────
INFLATION = 0.03  # 3% คงที่ ไม่ถามผู้ใช้

# ─── Krungsri Products (อ้างอิงผลิตภัณฑ์จริง) ────────────────────────────────
PRODUCTS = {
    "saving":    {"name": "เงินฝากออมทรัพย์ กรุงศรี",         "rate": 1.0,  "ref": "https://www.krungsri.com/th/personal/saving-account"},
    "fixed_1y":  {"name": "เงินฝากประจำ 12 เดือน กรุงศรี",   "rate": 2.0,  "ref": "https://www.krungsri.com/th/personal/fixed-deposit"},
    "kf_money":  {"name": "KF-MONEYA (กองทุนตลาดเงิน)",       "rate": 3.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-MONEYA"},
    "kf_fixed":  {"name": "KF-FIXEDPLUS (ตราสารหนี้)",         "rate": 3.5,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-FIXEDPLUS"},
    "kf_bal":    {"name": "KF-BALANCED (กองทุนผสม)",           "rate": 6.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-BALANCED"},
    "kf_star":   {"name": "KF-STAR (ผสมเน้นหุ้น)",            "rate": 6.5,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-STAR"},
    "kf_growth": {"name": "KF-GROWTH (หุ้นในประเทศ)",          "rate": 8.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-GROWTH"},
    "kf_gtech":  {"name": "KF-GTECH (หุ้นเทคโนโลยีโลก)",      "rate": 9.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-GTECH"},
    "kf_rmf":    {"name": "KF-RMFA (RMF ตราสารหนี้)",          "rate": 4.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-RMFA"},
    "kf_rmfg":   {"name": "KF-RMFG (RMF หุ้น)",               "rate": 8.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-RMFG"},
    "kf_ssf":    {"name": "KF-SSFPLUS (SSF กองทุนผสม)",        "rate": 6.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-SSFPLUS"},
}

RISK_CONFIG = {
    1: {
        "name":     "ต่ำ (Conservative)",
        "return":   0.04,
        "alloc":    {"เงินฝากออมทรัพย์/ประจำ": 40,
                     "กองทุนตลาดเงิน/ตราสารหนี้": 50,
                     "กองทุนผสม (เล็กน้อย)": 10},
        "products": ["saving", "fixed_1y", "kf_money", "kf_fixed"],
        "desc":     "รักษาเงินต้น ยอมรับผลตอบแทนที่ต่ำกว่า\nเพื่อความมั่นคงสูง",
    },
    2: {
        "name":     "ปานกลาง (Moderate)",
        "return":   0.06,
        "alloc":    {"เงินฝากประจำ": 20,
                     "กองทุนตราสารหนี้": 30,
                     "กองทุนผสม/หุ้น": 50},
        "products": ["fixed_1y", "kf_fixed", "kf_bal", "kf_star"],
        "desc":     "สมดุลระหว่างการเติบโตและความมั่นคง\nเหมาะกับนักลงทุนทั่วไป",
    },
    3: {
        "name":     "สูง (Aggressive)",
        "return":   0.08,
        "alloc":    {"เงินฝากประจำ": 5,
                     "กองทุนตราสารหนี้": 15,
                     "กองทุนผสม": 20,
                     "กองทุนหุ้น": 60},
        "products": ["kf_bal", "kf_growth", "kf_gtech"],
        "desc":     "เน้นผลตอบแทนระยะยาว ยอมรับความผันผวน\nสูงในระยะสั้นได้",
    },
}

# ─── คำถาม 9 ขั้นตอน ──────────────────────────────────────────────────────────
QUESTIONS = [
    {
        "key": "current_age",
        "q": (
            "📋 ข้อที่ 1 จาก 8\n"
            "────────────────────\n"
            "🎂 อายุปัจจุบันของคุณ\n\n"
            "กรุณาระบุเป็นจำนวนปีเต็ม\n\n"
            "✏️ ตัวอย่าง: 30"
        ),
    },
    {
        "key": "retire_age",
        "q": (
            "📋 ข้อที่ 2 จาก 8\n"
            "────────────────────\n"
            "🏖️ อายุที่ต้องการเกษียณ\n\n"
            "ระบุอายุที่คุณวางแผนจะหยุดทำงาน\n\n"
            "✏️ ตัวอย่าง: 55"
        ),
    },
    {
        "key": "life_expectancy",
        "q": (
            "📋 ข้อที่ 3 จาก 8\n"
            "────────────────────\n"
            "⏳ อายุขัยที่คาดหวัง\n\n"
            "คุณวางแผนดูแลค่าใช้จ่าย\n"
            "หลังเกษียณถึงอายุเท่าใด?\n\n"
            "💡 ค่าเฉลี่ยคนไทย: 80 ปี\n"
            "   WHO แนะนำควรวางแผนถึง 85 ปี\n\n"
            "✏️ ตัวอย่าง: 85"
        ),
    },
    {
        "key": "fixed_expense",
        "q": (
            "📋 ข้อที่ 4 จาก 8\n"
            "────────────────────\n"
            "🏠 ค่าใช้จ่ายคงที่ต่อเดือน (บาท)\n\n"
            "ได้แก่: ผ่อนบ้าน/ค่าเช่า ผ่อนรถ\n"
            "เบี้ยประกัน ค่างวดต่างๆ\n\n"
            "✏️ ตัวอย่าง: 15000\n"
            "(ถ้าไม่มีพิมพ์: 0)"
        ),
    },
    {
        "key": "variable_expense",
        "q": (
            "📋 ข้อที่ 5 จาก 8\n"
            "────────────────────\n"
            "🛍️ ค่าใช้จ่ายแปรผันต่อเดือน (บาท)\n\n"
            "ได้แก่: อาหาร ค่าเดินทาง\n"
            "ช้อปปิ้ง ท่องเที่ยว ค่าสาธารณูปโภค\n\n"
            "✏️ ตัวอย่าง: 12000"
        ),
    },
    {
        "key": "monthly_income",
        "q": (
            "📋 ข้อที่ 6 จาก 8\n"
            "────────────────────\n"
            "💰 รายรับทั้งหมดต่อเดือน (บาท)\n\n"
            "รวมเงินเดือน โบนัส (เฉลี่ย)\n"
            "รายได้เสริม และรายรับทุกประเภท\n\n"
            "✏️ ตัวอย่าง: 50000"
        ),
    },
    {
        "key": "risk_level",
        "q": (
            "📋 ข้อที่ 7 จาก 8\n"
            "────────────────────\n"
            "⚖️ ระดับความเสี่ยงที่ยอมรับได้\n\n"
            "1️⃣  ต่ำ — Conservative\n"
            "    รักษาเงินต้น ผลตอบแทน ~4%/ปี\n"
            "    เน้นเงินฝากและตราสารหนี้\n\n"
            "2️⃣  ปานกลาง — Moderate\n"
            "    สมดุลการเติบโต ผลตอบแทน ~6%/ปี\n"
            "    ผสมตราสารหนี้และกองทุนหุ้น\n\n"
            "3️⃣  สูง — Aggressive\n"
            "    เน้นเติบโตระยะยาว ~8%/ปี\n"
            "    เน้นกองทุนหุ้นในและต่างประเทศ\n\n"
            "✏️ พิมพ์ 1, 2 หรือ 3"
        ),
    },
    {
        "key": "current_investment",
        "q": (
            "📋 ข้อที่ 8.1 จาก 8\n"
            "────────────────────\n"
            "🏦 เงินออมและลงทุนสะสมปัจจุบัน (บาท)\n\n"
            "รวมเงินฝาก กองทุน หุ้น\n"
            "และสินทรัพย์ลงทุนทั้งหมด\n\n"
            "✏️ ตัวอย่าง: 200000\n"
            "(ถ้ายังไม่มีพิมพ์: 0)"
        ),
    },
    {
        "key": "monthly_dca",
        "q": (
            "📋 ข้อที่ 8.2 จาก 8\n"
            "────────────────────\n"
            "📅 เงินลงทุน/ออมที่ตั้งใจจะลงทุนทุกเดือน (บาท)\n\n"
            "จำนวนเงินที่คุณวางแผนจะ DCA\n"
            "ลงทุนสม่ำเสมอทุกเดือนนับจากนี้\n\n"
            "✏️ ตัวอย่าง: 5000\n"
            "(ถ้ายังไม่แน่ใจพิมพ์: 0)"
        ),
    },
]

TOTAL_STEPS = len(QUESTIONS)  # 9 ขั้นตอน


# ═══════════════════════════════════════════════════════════════════════════════
#  Utility
# ═══════════════════════════════════════════════════════════════════════════════

def extract_number(text: str):
    cleaned = text.replace(",", "")
    m = re.search(r'\d+(\.\d+)?', cleaned)
    return float(m.group()) if m else None

def fmt(n: float, dec: int = 0) -> str:
    return f"{n:,.{dec}f}"

def progress_bar(ratio: float, w: int = 10) -> str:
    filled = max(0, min(w, round(ratio * w)))
    return "█" * filled + "░" * (w - filled)


# ═══════════════════════════════════════════════════════════════════════════════
#  สูตรการเงิน
# ═══════════════════════════════════════════════════════════════════════════════

def fv_lump(pv: float, r: float, n: int) -> float:
    return pv * (1 + r) ** n

def fv_dca_monthly(pmt: float, r: float, n: int) -> float:
    """FV ของการลงทุน DCA รายเดือน"""
    if r == 0:
        return pmt * 12 * n
    rm = r / 12
    return pmt * (((1 + rm) ** (12 * n) - 1) / rm)

def pmt_required(fv: float, r: float, n: int) -> float:
    """PMT ที่ต้องออมรายเดือนเพื่อให้ได้ FV"""
    if n <= 0:
        return float('inf')
    rm = r / 12
    denom = (1 + rm) ** (12 * n) - 1
    if denom <= 0:
        return float('inf')
    return fv * rm / denom

def real_r(r: float) -> float:
    return (1 + r) / (1 + INFLATION) - 1


# ═══════════════════════════════════════════════════════════════════════════════
#  คำนวณและสร้าง Output
# ═══════════════════════════════════════════════════════════════════════════════

def calculate(data: dict) -> list[str]:
    age      = int(data["current_age"])
    retire   = int(data["retire_age"])
    life_exp = int(data["life_expectancy"])
    fixed_ex = float(data["fixed_expense"])
    var_ex   = float(data["variable_expense"])
    income   = float(data["monthly_income"])
    risk     = int(data["risk_level"])
    invest   = float(data["current_investment"])
    dca      = float(data["monthly_dca"])

    n_accum  = retire - age       # ปีสะสมทุน
    n_retire = life_exp - retire  # ปีใช้เงินหลังเกษียณ
    r        = RISK_CONFIG[risk]["return"]
    rcfg     = RISK_CONFIG[risk]

    total_exp  = fixed_ex + var_ex
    net_flow   = income - total_exp
    save_pct   = (net_flow / income * 100) if income > 0 else 0

    # ── ค่าใช้จ่าย ณ วันเกษียณ ──────────────────────────────────────────
    exp_retire_m = fv_lump(total_exp, INFLATION, n_accum)
    exp_retire_y = exp_retire_m * 12

    # ── เงินเป้าหมาย (ใช้ค่าสูงกว่าระหว่าง 4% Rule และ PV Annuity) ─────
    fund_4pct = exp_retire_y / 0.04
    rr = real_r(r)
    if rr > 0:
        fund_pv = exp_retire_y * (1 - (1 + rr) ** (-n_retire)) / rr
    else:
        fund_pv = exp_retire_y * n_retire
    target = max(fund_4pct, fund_pv)

    # ── การเติบโตของเงิน ─────────────────────────────────────────────────
    fv_exist = fv_lump(invest, r, n_accum)
    fv_dca   = fv_dca_monthly(dca, r, n_accum)
    projected = fv_exist + fv_dca

    # ── Gap & Extra PMT ──────────────────────────────────────────────────
    gap       = target - projected
    gap_flag  = gap > 0
    extra_pmt = pmt_required(max(gap, 0), r, n_accum) if gap_flag else 0
    total_pmt = dca + extra_pmt

    withdrawal_m = projected * 0.04 / 12
    withdrawal_y = projected * 0.04

    # ── Growth Table ─────────────────────────────────────────────────────
    cps = sorted(set([age] + list(range(age + 5, retire, 5)) + [retire]))
    rows = []
    for a in cps:
        yrs = a - age
        if yrs == 0:
            rows.append((a, invest, 0.0, invest))
        else:
            fl  = fv_lump(invest, r, yrs)
            fd  = fv_dca_monthly(dca, r, yrs)
            rows.append((a, fl, fd, fl + fd))

    # ── Scenario ─────────────────────────────────────────────────────────
    scen_a = projected + fv_dca_monthly(2000, r, n_accum)
    r2     = r + 0.02
    scen_b = fv_lump(invest, r2, n_accum) + fv_dca_monthly(dca, r2, n_accum)
    years_early = 0
    for nt in range(max(n_accum - 20, 1), n_accum):
        if (fv_lump(invest, r, nt) + fv_dca_monthly(dca + 2000, r, nt)) >= target:
            years_early = n_accum - nt
            break

    # ── Tax Shield ────────────────────────────────────────────────────────
    income_y = income * 12
    max_ssf  = min(income_y * 0.30, 200_000)
    max_rmf  = min(income_y * 0.30, 500_000)
    tax_save = (max_ssf + max_rmf) * 0.15  # marginal ~15%

    # ─────────────────────────────────────────────────────────────────────
    D = "────────────────────"

    # ══ MSG 1: Profile + สถานะการเงิน ══════════════════════════════════════
    msg1 = (
        "┌──────────────────────────┐\n"
        "  🏦 รายงานแผนเกษียณ\n"
        "   KrungsriRetire v2.0\n"
        "└──────────────────────────┘\n\n"

        f"👤 1. ข้อมูลส่วนบุคคล & เป้าหมาย\n{D}\n"
        f"• อายุปัจจุบัน          {age} ปี\n"
        f"• เกษียณที่อายุ         {retire} ปี\n"
        f"• วางแผนถึงอายุ         {life_exp} ปี\n"
        f"• ระยะสะสมทุน           {n_accum} ปี\n"
        f"• ระยะใช้เงินหลังเกษียณ {n_retire} ปี\n"
        f"• ความเสี่ยง            {rcfg['name']}\n"
        f"• ผลตอบแทนคาดการณ์     {r*100:.0f}%/ปี\n"
        f"• อัตราเงินเฟ้อ (คงที่)  3%/ปี\n\n"

        f"💼 2. สถานะการเงินปัจจุบัน\n{D}\n"
        f"• รายรับต่อเดือน          {fmt(income)} บาท\n"
        f"• ค่าใช้จ่ายคงที่/เดือน    {fmt(fixed_ex)} บาท\n"
        f"• ค่าใช้จ่ายแปรผัน/เดือน  {fmt(var_ex)} บาท\n"
        f"• รวมค่าใช้จ่าย/เดือน     {fmt(total_exp)} บาท\n"
        f"• เงินเหลือสุทธิ/เดือน    {fmt(net_flow)} บาท\n"
        f"• อัตราการออม              {fmt(save_pct,1)}%\n"
        f"  {'✅ ดีมาก! (เกิน 20%)' if save_pct >= 20 else ('⚠️ ควรเพิ่ม (ต่ำกว่า 20%)' if save_pct >= 0 else '❌ รายจ่ายเกินรายรับ')}\n\n"
        f"• เงินออมสะสมปัจจุบัน     {fmt(invest)} บาท\n"
        f"• DCA ที่วางแผน/เดือน     {fmt(dca)} บาท\n\n"

        f"📊 สถานะการเงินหลังเกษียณ\n{D}\n"
        f"• ค่าใช้จ่าย ณ อายุ {retire} ปี:\n"
        f"  {fmt(exp_retire_m)} บาท/เดือน\n"
        f"  {fmt(exp_retire_y)} บาท/ปี\n"
        f"  (เงินเฟ้อ 3% × {n_accum} ปี)\n"
        f"• ถอนได้ตามแผน 4% Rule:\n"
        f"  {fmt(withdrawal_m)} บาท/เดือน\n"
        f"  {fmt(withdrawal_y)} บาท/ปี"
    )

    # ══ MSG 2: เป้าหมายเกษียณ + Growth Table ══════════════════════════════
    msg2 = (
        f"🎯 3. เป้าหมายเกษียณ\n{D}\n"
        f"• วิธี 4% Rule:\n"
        f"  {fmt(fund_4pct)} บาท\n"
        f"• วิธี PV Annuity ({n_retire} ปี):\n"
        f"  {fmt(fund_pv)} บาท\n"
        f"• ✅ เป้าหมาย (Conservative):\n"
        f"  💎 {fmt(target)} บาท\n\n"

        f"📈 4. จำลองการเติบโตของเงินทุน\n{D}\n"
        f"  (ผลตอบแทน {r*100:.0f}%/ปี + เงินเฟ้อ 3%)\n\n"
    )
    for a, fl, fd, tot in rows:
        ratio    = min(tot / target, 1.0) if target > 0 else 0
        pb       = progress_bar(ratio, 8)
        msg2 += (
            f"  อายุ {a} ปี\n"
            f"  [{pb}] {fmt(ratio*100,0)}%\n"
            f"  รวม: {fmt(tot)} บาท\n\n"
        )
    msg2 += (
        f"💰 คาดการณ์ ณ วันเกษียณ:\n"
        f"  เงินก้อน: {fmt(fv_exist)} บาท\n"
        f"  DCA สะสม: {fmt(fv_dca)} บาท\n"
        f"  รวมทั้งหมด: {fmt(projected)} บาท"
    )

    # ══ MSG 3: Big Picture + Action Plan ══════════════════════════════════
    surplus   = projected - target
    surplus_s = f"+{fmt(surplus)}" if surplus >= 0 else fmt(surplus)
    icon      = "✅" if surplus >= 0 else "⚠️"

    alloc_txt = "\n".join(
        f"  • {k}: {v}%" for k, v in rcfg["alloc"].items()
    )

    msg3 = (
        f"🔭 5. สรุปภาพรวม (The Big Picture)\n{D}\n"
        f"• เงินก้อนที่ต้องมี ณ เกษียณ:\n"
        f"  🎯 {fmt(target)} บาท\n"
        f"• เงินที่คาดว่าจะสะสมได้:\n"
        f"  💎 {fmt(projected)} บาท\n\n"
        f"• Retirement Gap:\n"
        f"  {icon} {surplus_s} บาท\n"
        f"  {'เกินเป้าหมาย — ยอดเยี่ยมมาก! 🎉' if surplus >= 0 else 'ยังขาดเป้าหมาย — มีแผนรองรับ 👇'}\n\n"

        f"⚡ 6. แผนปฏิบัติการ (Action Plan)\n{D}\n"
    )

    if not gap_flag:
        msg3 += (
            f"✅ แผนปัจจุบันของคุณดีมากแล้ว!\n"
            f"   DCA {fmt(dca)} บาท/เดือน\n"
            f"   เพียงพอที่จะบรรลุเป้าหมาย\n\n"
        )
    else:
        msg3 += (
            f"📌 6.1 ต้องออมเพิ่มต่อเดือน:\n"
            f"   +{fmt(extra_pmt)} บาท\n"
            f"   (รวม DCA รายเดือนที่แนะนำ:\n"
            f"    {fmt(total_pmt)} บาท/เดือน)\n\n"
        )

    msg3 += (
        f"📊 6.2 Asset Allocation ({rcfg['name']}):\n"
        f"{alloc_txt}\n\n"
        f"💡 {rcfg['desc']}"
    )

    # ══ MSG 4: Scenario + Tax Shield + Products ═════════════════════════════
    msg4 = (
        f"🔮 7. วิเคราะห์สถานการณ์จำลอง\n{D}\n"
        f"📌 Scenario A: ออมเพิ่ม 2,000 บาท/เดือน\n"
        f"   เงิน ณ เกษียณ: {fmt(scen_a)} บาท\n"
        f"   {'✅ เกินเป้าหมายได้' if scen_a >= target else '⚠️ ยังขาดอยู่'}"
        + (f"\n   🎉 เกษียณเร็วขึ้น ~{years_early} ปีได้!" if years_early > 0 else "") +
        f"\n\n📌 Scenario B: ผลตอบแทนเพิ่มขึ้น 2%\n"
        f"   ({r*100:.0f}% → {(r+0.02)*100:.0f}%/ปี)\n"
        f"   เงิน ณ เกษียณ: {fmt(scen_b)} บาท\n"
        f"   เพิ่มขึ้น: +{fmt(scen_b - projected)} บาท\n\n"

        f"🛡️ 8. Tax Shield — ลดหย่อนภาษี\n{D}\n"
        f"• SSF (สูงสุด 30% รายได้ / 2 แสนบาท):\n"
        f"  {fmt(max_ssf)} บาท/ปี\n"
        f"• RMF (สูงสุด 30% รายได้ / 5 แสนบาท):\n"
        f"  {fmt(max_rmf)} บาท/ปี\n"
        f"• ภาษีที่ประหยัดได้ (ประมาณ):\n"
        f"  💚 ~{fmt(tax_save)} บาท/ปี\n\n"
        f"✅ แนะนำ: เปิด SSF + RMF กรุงศรี\n"
        f"   ลงทุนระยะยาว + ลดภาษีได้ทันที!\n\n"

        f"🏦 9. ผลิตภัณฑ์กรุงศรีแนะนำ\n{D}\n"
        f"(เหมาะกับ Risk Profile: {rcfg['name']})\n\n"
    )

    for pid in rcfg["products"]:
        p = PRODUCTS.get(pid)
        if not p: continue
        msg4 += f"📦 {p['name']}\n"
        msg4 += f"   ผลตอบแทนเฉลี่ย ~{p['rate']:.1f}%/ปี\n"
        msg4 += f"   🔗 {p['ref']}\n\n"

    msg4 += (
        f"💎 กองทุนลดหย่อนภาษีแนะนำ:\n\n"
        f"📦 {PRODUCTS['kf_rmf']['name']}\n"
        f"   ~{PRODUCTS['kf_rmf']['rate']:.0f}%/ปี\n"
        f"   🔗 {PRODUCTS['kf_rmf']['ref']}\n\n"
        f"📦 {PRODUCTS['kf_ssf']['name']}\n"
        f"   ~{PRODUCTS['kf_ssf']['rate']:.0f}%/ปี\n"
        f"   🔗 {PRODUCTS['kf_ssf']['ref']}\n\n"
        f"{D}\n"
        f"⚠️ ผลตอบแทนในอดีตมิได้รับประกัน\n"
        f"อนาคต ควรศึกษาหนังสือชี้ชวน\n"
        f"ก่อนตัดสินใจลงทุนทุกครั้ง\n\n"
        f"🔄 พิมพ์ 'เริ่ม' เพื่อคำนวณใหม่"
    )

    return [msg1, msg2, msg3, msg4]


# ═══════════════════════════════════════════════════════════════════════════════
#  Validation
# ═══════════════════════════════════════════════════════════════════════════════

def validate(step: int, val: float, data: dict) -> str | None:
    key = QUESTIONS[step]["key"]
    if key == "current_age":
        if not (10 <= val <= 80):
            return "⚠️ กรุณาระบุอายุระหว่าง 10–80 ปีค่ะ"
    elif key == "retire_age":
        cur = data.get("current_age", 0)
        if val <= cur:
            return f"⚠️ อายุเกษียณต้องมากกว่าอายุปัจจุบัน ({int(cur)} ปี)\nกรุณาระบุใหม่ค่ะ"
        if val > 85:
            return "⚠️ กรุณาระบุอายุเกษียณไม่เกิน 85 ปีค่ะ"
    elif key == "life_expectancy":
        ret = data.get("retire_age", 0)
        if val <= ret:
            return (
                f"⚠️ อายุขัยต้องมากกว่าอายุเกษียณ ({int(ret)} ปี)\n"
                f"💡 เช่น ถ้าเกษียณอายุ {int(ret)} ควรวางแผนถึง {int(ret)+25} ปี"
            )
        if val > 110:
            return "⚠️ กรุณาระบุอายุขัยไม่เกิน 110 ปีค่ะ"
    elif key == "risk_level":
        if int(val) not in (1, 2, 3):
            return "⚠️ กรุณาพิมพ์เพียง 1, 2 หรือ 3 ค่ะ"
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  LINE Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def reply_msg(api: MessagingApi, token: str, text: str):
    api.reply_message(ReplyMessageRequest(
        reply_token=token,
        messages=[TextMessage(text=text)],
    ))

def push_msg(api: MessagingApi, uid: str, msgs: list[str]):
    for i in range(0, len(msgs), 5):
        api.push_message(PushMessageRequest(
            to=uid,
            messages=[TextMessage(text=m) for m in msgs[i:i+5]],
        ))


# ═══════════════════════════════════════════════════════════════════════════════
#  LINE Event Handler
# ═══════════════════════════════════════════════════════════════════════════════

WELCOME = (
    "✨ ยินดีต้อนรับสู่ KrungsriRetire\n"
    "ผู้ช่วยวางแผนเกษียณอัจฉริยะ\n\n"
    "🏦 ระบบช่วยคุณ:\n"
    "• คำนวณเงินเป้าหมายเกษียณ\n"
    "• วางแผนออมและลงทุนระยะยาว\n"
    "• แนะนำผลิตภัณฑ์กรุงศรีที่เหมาะสม\n"
    "• ลดหย่อนภาษีผ่าน SSF / RMF\n\n"
    "📝 พิมพ์ 'เริ่ม' เพื่อเริ่มต้นเลยค่ะ 🚀"
)

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    uid  = event.source.user_id if isinstance(event.source, UserSource) else str(event.source)
    text = event.message.text.strip()
    cmd  = text.lower()

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        try:
            # ── Reset / Welcome ────────────────────────────────────────────
            if cmd in {"เริ่มใหม่","reset","ใหม่","สวัสดี","หวัดดี","hi","hello","ไหว้"}:
                sessions.pop(uid, None)
                reply_msg(api, event.reply_token, WELCOME)
                return

            # ── Start ──────────────────────────────────────────────────────
            if cmd in {"เริ่ม","begin","คำนวณ","ลอง","start","เริ่มต้น"}:
                sessions[uid] = {"step": 0, "data": {}}
                reply_msg(api, event.reply_token, QUESTIONS[0]["q"])
                return

            # ── No Session ─────────────────────────────────────────────────
            if uid not in sessions:
                reply_msg(api, event.reply_token,
                    "💬 พิมพ์ 'เริ่ม' เพื่อเริ่มวางแผนการเงินได้เลยค่ะ 🏦")
                return

            # ── Collect Answers ────────────────────────────────────────────
            sess = sessions[uid]
            step = sess["step"]
            data = sess["data"]

            val = extract_number(text)
            if val is None or val < 0:
                reply_msg(api, event.reply_token,
                    f"⚠️ กรุณากรอกเป็นตัวเลขนะคะ\n\n{QUESTIONS[step]['q']}")
                return

            err = validate(step, val, data)
            if err:
                reply_msg(api, event.reply_token, f"{err}\n\n{QUESTIONS[step]['q']}")
                return

            # Cast type
            key = QUESTIONS[step]["key"]
            if key in {"current_age","retire_age","life_expectancy","risk_level"}:
                data[key] = int(val)
            else:
                data[key] = float(val)

            step += 1
            sess["step"] = step

            if step < TOTAL_STEPS:
                reply_msg(api, event.reply_token, QUESTIONS[step]["q"])
                return

            # ── Calculate! ─────────────────────────────────────────────────
            del sessions[uid]
            reply_msg(api, event.reply_token,
                "⏳ กำลังวิเคราะห์และจัดทำรายงาน...\n"
                "กรุณารอสักครู่นะคะ 📊")
            results = calculate(data)
            push_msg(api, uid, results)

        except Exception as e:
            logger.exception(f"Error: {e}")
            reply_msg(api, event.reply_token,
                "⚠️ เกิดข้อผิดพลาดบางอย่างค่ะ\n"
                "กรุณาพิมพ์ 'เริ่มใหม่' เพื่อลองใหม่อีกครั้ง")


# ═══════════════════════════════════════════════════════════════════════════════
#  FastAPI
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="KrungsriRetire Bot")

@app.get("/")
def root():
    return {"status": "ok", "bot": "KrungsriRetire", "version": "2.0"}

@app.get("/health")
def health():
    return {"status": "ok", "active_sessions": len(sessions)}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    body      = await request.body()
    signature = request.headers.get("X-Line-Signature", "")
    background_tasks.add_task(process_event, body.decode("utf-8"), signature)
    return JSONResponse(content={"status": "ok"})

def process_event(body: str, sig: str):
    try:
        handler.handle(body, sig)
    except InvalidSignatureError:
        logger.warning("Invalid LINE signature")
    except Exception as e:
        logger.exception(f"Error: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
