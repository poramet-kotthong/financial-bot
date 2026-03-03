"""
🏦 KrungsriRetire Bot — LINE Bot วางแผนเกษียณ
FastAPI + LINE SDK v3 + Flex Messages  |  v3.0
"""

import os, re, math, logging
import uvicorn
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage, FlexMessage, FlexContainer,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, UserSource
from linebot.v3.exceptions import InvalidSignatureError

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("krungsri-retire")

CHANNEL_SECRET       = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
configuration        = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler              = WebhookHandler(CHANNEL_SECRET)

sessions: dict[str, dict] = {}
INFLATION = 0.03

PRODUCTS = {
    "saving":    {"name": "เงินฝากออมทรัพย์ กรุงศรี",         "rate": 1.0,  "ref": "https://www.krungsri.com/th/personal/saving-account"},
    "fixed_1y":  {"name": "เงินฝากประจำ 12 เดือน กรุงศรี",   "rate": 2.0,  "ref": "https://www.krungsri.com/th/personal/fixed-deposit"},
    "kf_money":  {"name": "KF-MONEYA (กองทุนตลาดเงิน)",       "rate": 3.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-MONEYA"},
    "kf_fixed":  {"name": "KF-FIXEDPLUS (ตราสารหนี้)",         "rate": 3.5,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-FIXEDPLUS"},
    "kf_bal":    {"name": "KF-BALANCED (กองทุนผสม)",           "rate": 6.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-BALANCED"},
    "kf_star":   {"name": "KF-STAR (ผสมเน้นหุ้น)",             "rate": 6.5,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-STAR"},
    "kf_growth": {"name": "KF-GROWTH (หุ้นในประเทศ)",          "rate": 8.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-GROWTH"},
    "kf_gtech":  {"name": "KF-GTECH (หุ้นเทคโนโลยีโลก)",      "rate": 9.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-GTECH"},
    "kf_rmf":    {"name": "KF-RMFA (RMF ตราสารหนี้)",          "rate": 4.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-RMFA"},
    "kf_rmfg":   {"name": "KF-RMFG (RMF หุ้น)",               "rate": 8.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-RMFG"},
    "kf_ssf":    {"name": "KF-SSFPLUS (SSF กองทุนผสม)",        "rate": 6.0,  "ref": "https://www.krungsriasset.com/TH/FundInfo/FundDetail.html?FundID=KF-SSFPLUS"},
}

RISK_CONFIG = {
    1: {"name": "ต่ำ (Conservative)", "return": 0.04, "color": "#4CAF50",
        "alloc": {"เงินฝากออมทรัพย์/ประจำ": 40, "กองทุนตลาดเงิน/ตราสารหนี้": 50, "กองทุนผสม": 10},
        "products": ["saving", "fixed_1y", "kf_money", "kf_fixed"],
        "desc": "รักษาเงินต้น ยอมรับผลตอบแทนที่ต่ำกว่า เพื่อความมั่นคงสูง"},
    2: {"name": "ปานกลาง (Moderate)", "return": 0.06, "color": "#FF9800",
        "alloc": {"เงินฝากประจำ": 20, "กองทุนตราสารหนี้": 30, "กองทุนผสม/หุ้น": 50},
        "products": ["fixed_1y", "kf_fixed", "kf_bal", "kf_star", "kf_ssf", "kf_rmf"],
        "desc": "สมดุลระหว่างการเติบโตและความมั่นคง เหมาะกับนักลงทุนทั่วไป"},
    3: {"name": "สูง (Aggressive)", "return": 0.08, "color": "#F44336",
        "alloc": {"เงินฝากประจำ": 5, "กองทุนตราสารหนี้": 15, "กองทุนผสม": 20, "กองทุนหุ้น": 60},
        "products": ["kf_bal", "kf_growth", "kf_gtech", "kf_rmfg"],
        "desc": "เน้นผลตอบแทนระยะยาว ยอมรับความผันผวนสูงในระยะสั้นได้"},
}

QUESTIONS = [
    {"key": "current_age",        "step_label": "1/8",   "emoji": "🎂", "label": "อายุปัจจุบัน",                    "example": "เช่น: 30",         "hint": "กรุณาระบุเป็นจำนวนปีเต็ม"},
    {"key": "retire_age",         "step_label": "2/8",   "emoji": "🏖", "label": "อายุที่ต้องการเกษียณ",            "example": "เช่น: 55",         "hint": "อายุที่คุณวางแผนจะหยุดทำงาน"},
    {"key": "life_expectancy",    "step_label": "3/8",   "emoji": "⏳", "label": "อายุขัยที่วางแผน",                "example": "เช่น: 85",         "hint": "ค่าเฉลี่ยคนไทย 80 ปี / WHO แนะนำ 85 ปี"},
    {"key": "fixed_expense",      "step_label": "4/8",   "emoji": "🏠", "label": "ค่าใช้จ่ายคงที่/เดือน (บาท)",   "example": "เช่น: 15000",      "hint": "ผ่อนบ้าน/รถ เบี้ยประกัน ค่างวดต่างๆ (ไม่มีพิมพ์ 0)"},
    {"key": "variable_expense",   "step_label": "5/8",   "emoji": "🛍", "label": "ค่าใช้จ่ายแปรผัน/เดือน (บาท)", "example": "เช่น: 12000",      "hint": "อาหาร ค่าเดินทาง ช้อปปิ้ง สาธารณูปโภค"},
    {"key": "monthly_income",     "step_label": "6/8",   "emoji": "💰", "label": "รายรับทั้งหมด/เดือน (บาท)",     "example": "เช่น: 50000",      "hint": "รวมเงินเดือน โบนัส (เฉลี่ย) รายได้เสริม"},
    {"key": "risk_level",         "step_label": "7/8",   "emoji": "⚖", "label": "ระดับความเสี่ยง",                "example": "พิมพ์ 1, 2 หรือ 3","hint": "1=ต่ำ (~4%/ปี)  2=ปานกลาง (~6%/ปี)  3=สูง (~8%/ปี)"},
    {"key": "current_investment", "step_label": "8.1/8", "emoji": "🏦", "label": "เงินออม/ลงทุนสะสม (บาท)",      "example": "เช่น: 200000",     "hint": "รวมเงินฝาก กองทุน หุ้น (ยังไม่มีพิมพ์ 0)"},
    {"key": "monthly_dca",        "step_label": "8.2/8", "emoji": "📅", "label": "DCA รายเดือน (บาท)",             "example": "เช่น: 5000",       "hint": "เงินที่วางแผนลงทุนสม่ำเสมอทุกเดือน"},
]
TOTAL_STEPS = len(QUESTIONS)

# ── Color Palette ──────────────────────────────────────────────────────────────
C_GREEN  = "#006B3F"
C_GOLD   = "#C9A84C"
C_LIGHT  = "#E8F5E9"
C_WHITE  = "#FFFFFF"
C_DARK   = "#1A1A2E"
C_GRAY   = "#666666"
C_POS    = "#2E7D32"
C_NEG    = "#C62828"
C_BLUE   = "#1565C0"

# ═══════════════════════════════════════════════════════════════════════════════
#  Flex Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def row_item(label, value, val_color=None, bold=False):
    return {"type": "box", "layout": "horizontal", "margin": "sm",
            "contents": [
                {"type": "text", "text": label, "size": "sm", "color": C_GRAY, "flex": 5, "wrap": True},
                {"type": "text", "text": value, "size": "sm", "flex": 4, "align": "end",
                 "color": val_color or C_DARK, "weight": "bold" if bold else "regular", "wrap": True},
            ]}

def sec_header(emoji, title, color=None):
    return {"type": "box", "layout": "horizontal", "margin": "md",
            "contents": [
                {"type": "text", "text": emoji, "size": "sm", "flex": 0},
                {"type": "text", "text": f"  {title}", "size": "sm", "weight": "bold",
                 "color": color or C_GREEN, "flex": 1},
            ]}

def divider():
    return {"type": "separator", "margin": "sm", "color": "#E0E0E0"}

def prog_bar(ratio, w=10, color=C_GREEN):
    filled = max(0, min(w, int(ratio * w)))
    cells = []
    for i in range(w):
        # ⚠️ แก้ไขตรงนี้: เพิ่ม "contents": [] ให้กับกล่องว่าง
        cells.append({"type": "box", "layout": "vertical", "flex": 1, "height": "6px",
                       "cornerRadius": "3px",
                       "backgroundColor": color if i < filled else "#E0E0E0",
                       "contents": []})
    return {"type": "box", "layout": "horizontal", "spacing": "xs", "contents": cells}

# ═══════════════════════════════════════════════════════════════════════════════
#  Flex Builders
# ═══════════════════════════════════════════════════════════════════════════════

def build_welcome_flex():
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": C_GREEN, "paddingAll": "20px",
                   "contents": [
                       {"type": "text", "text": "🏦 Financial Retirement Planning", "color": C_WHITE, "size": "xl", "weight": "bold", "align": "center"},
                       {"type": "text", "text": "ผู้ช่วยวางแผนเกษียณอัจฉริยะ", "color": C_GOLD, "size": "sm", "align": "center", "margin": "xs"},
                   ]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "20px",
                 "contents": [
                     {"type": "text", "text": "ระบบจะช่วยคุณ", "size": "md", "weight": "bold", "color": C_DARK},
                     {"type": "box", "layout": "vertical", "margin": "md", "spacing": "sm",
                      "contents": [
                          {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "✅", "size": "sm", "flex": 0}, {"type": "text", "text": "  คำนวณเงินเป้าหมายเกษียณ", "size": "sm", "color": C_GRAY, "flex": 1}]},
                          {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "✅", "size": "sm", "flex": 0}, {"type": "text", "text": "  วางแผนออมและลงทุนระยะยาว", "size": "sm", "color": C_GRAY, "flex": 1}]},
                          {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "✅", "size": "sm", "flex": 0}, {"type": "text", "text": "  แนะนำผลิตภัณฑ์กรุงศรีที่เหมาะสม", "size": "sm", "color": C_GRAY, "flex": 1}]},
                          {"type": "box", "layout": "horizontal", "contents": [{"type": "text", "text": "✅", "size": "sm", "flex": 0}, {"type": "text", "text": "  ลดหย่อนภาษีผ่าน SSF / RMF", "size": "sm", "color": C_GRAY, "flex": 1}]},
                      ]},
                     divider(),
                     {"type": "text", "text": "ใช้เวลาเพียง 2 นาที — 8 คำถามสั้นๆ", "size": "xs", "color": C_GRAY, "align": "center", "margin": "md"},
                 ]},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "15px", "backgroundColor": C_LIGHT,
                   "contents": [{"type": "button", "style": "primary", "color": C_GREEN, "height": "sm",
                                 "action": {"type": "message", "label": "🚀 เริ่มวางแผนเลย!", "text": "เริ่ม"}}]},
    }

def build_question_flex(step_idx):
    q = QUESTIONS[step_idx]
    ratio = step_idx / TOTAL_STEPS
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": C_GREEN, "paddingAll": "15px",
                   "contents": [
                       {"type": "box", "layout": "horizontal", "contents": [
                           {"type": "text", "text": f"ข้อที่ {q['step_label']}", "color": C_GOLD, "size": "xs", "flex": 1},
                           {"type": "text", "text": f"{int(ratio*100)}%", "color": C_WHITE, "size": "xs", "align": "end"},
                       ]},
                       {**prog_bar(ratio), "margin": "sm"},
                   ]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "20px",
                 "contents": [
                     {"type": "box", "layout": "horizontal",
                      "contents": [
                          {"type": "text", "text": q["emoji"], "size": "xxl", "flex": 0},
                          {"type": "box", "layout": "vertical", "flex": 1, "margin": "md",
                           "contents": [
                               {"type": "text", "text": q["label"], "size": "md", "weight": "bold", "color": C_DARK, "wrap": True},
                               {"type": "text", "text": q["hint"], "size": "xs", "color": C_GRAY, "wrap": True, "margin": "xs"},
                           ]},
                      ]},
                     divider(),
                     {"type": "box", "layout": "vertical", "margin": "md", "backgroundColor": C_LIGHT,
                      "cornerRadius": "8px", "paddingAll": "12px",
                      "contents": [{"type": "text", "text": "✏️  " + q["example"], "size": "sm", "color": C_GREEN}]},
                 ]},
    }

def build_profile_flex(age, retire, life_exp, n_accum, n_retire,
                       income, fixed_ex, var_ex, total_exp, net_flow, save_pct,
                       invest, dca, exp_retire_m, exp_retire_y,
                       withdrawal_m, withdrawal_y, rcfg, r):
    save_color  = C_POS if save_pct >= 20 else (C_NEG if save_pct < 0 else "#E65100")
    save_status = "✅ ดีมาก!" if save_pct >= 20 else ("❌ รายจ่ายเกินรายรับ" if save_pct < 0 else "⚠️ ควรเพิ่ม")
    fmt = lambda n, d=0: f"{n:,.{d}f}"
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": C_GREEN, "paddingAll": "18px",
                   "contents": [
                       {"type": "text", "text": "📋 รายงานแผนเกษียณ", "color": C_WHITE, "size": "lg", "weight": "bold"},
                       {"type": "text", "text": "KrungsriRetire  •  ข้อมูลส่วนตัว & สถานะการเงิน",
                        "color": C_GOLD, "size": "xs", "margin": "xs"},
                   ]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "18px",
                 "contents": [
                     sec_header("👤", "ข้อมูลส่วนบุคคล & เป้าหมาย"),
                     divider(),
                     row_item("อายุปัจจุบัน", f"{age} ปี"),
                     row_item("เกษียณที่อายุ", f"{retire} ปี ({n_accum} ปีข้างหน้า)", C_GREEN, True),
                     row_item("วางแผนถึงอายุ", f"{life_exp} ปี (หลังเกษียณ {n_retire} ปี)"),
                     row_item("ระดับความเสี่ยง", rcfg["name"], rcfg["color"], True),
                     row_item("ผลตอบแทนคาดการณ์", f"{r*100:.0f}%/ปี", C_GREEN),
                     row_item("อัตราเงินเฟ้อ", "3%/ปี (คงที่)"),

                     sec_header("💼", "สถานะการเงินปัจจุบัน"),
                     divider(),
                     row_item("รายรับต่อเดือน", f"฿{fmt(income)}", C_POS, True),
                     row_item("ค่าใช้จ่ายคงที่/เดือน", f"฿{fmt(fixed_ex)}"),
                     row_item("ค่าใช้จ่ายแปรผัน/เดือน", f"฿{fmt(var_ex)}"),
                     row_item("รวมค่าใช้จ่าย/เดือน", f"฿{fmt(total_exp)}"),
                     row_item("เงินเหลือสุทธิ/เดือน", f"฿{fmt(net_flow)}", C_POS if net_flow >= 0 else C_NEG, True),
                     {"type": "box", "layout": "horizontal", "margin": "sm",
                      "contents": [
                          {"type": "text", "text": "อัตราการออม", "size": "sm", "color": C_GRAY, "flex": 5},
                          {"type": "text", "text": f"{fmt(save_pct,1)}%  {save_status}",
                           "size": "sm", "color": save_color, "weight": "bold", "flex": 4, "align": "end", "wrap": True},
                      ]},
                     row_item("เงินออมสะสมปัจจุบัน", f"฿{fmt(invest)}"),
                     row_item("DCA วางแผน/เดือน", f"฿{fmt(dca)}", C_GREEN),

                     sec_header("📊", f"ค่าใช้จ่ายหลังเกษียณ (อายุ {retire} ปี)"),
                     divider(),
                     row_item("ค่าใช้จ่าย/เดือน", f"฿{fmt(exp_retire_m)}", C_NEG, True),
                     row_item("ค่าใช้จ่าย/ปี", f"฿{fmt(exp_retire_y)}"),
                     {"type": "box", "layout": "vertical", "margin": "md", "backgroundColor": C_LIGHT,
                      "cornerRadius": "8px", "paddingAll": "12px",
                      "contents": [
                          {"type": "text", "text": "ถอนได้ตามแผน 4% Rule", "size": "xs", "color": C_GRAY, "weight": "bold"},
                          {"type": "box", "layout": "horizontal", "margin": "sm",
                           "contents": [
                               {"type": "text", "text": f"฿{fmt(withdrawal_m)}/เดือน", "size": "sm", "color": C_POS, "weight": "bold", "flex": 1},
                               {"type": "text", "text": f"฿{fmt(withdrawal_y)}/ปี", "size": "sm", "color": C_POS, "flex": 1, "align": "end"},
                           ]},
                      ]},
                 ]},
    }

def build_goal_flex(target, fund_4pct, fund_pv, n_retire, r, projected, fv_exist, fv_dca_v, rows_data):
    fmt = lambda n, d=0: f"{n:,.{d}f}"
    row_items = []
    for a, fl, fd, tot in rows_data:
        ratio = min(tot / target, 1.0) if target > 0 else 0
        pct   = int(ratio * 100)
        bar_w = max(1, int(ratio * 8))
        bar_cells = []
        for i in range(8):
            # ⚠️ แก้ไขตรงนี้: เพิ่ม "contents": [] ให้กับกล่องว่าง
            bar_cells.append({"type": "box", "layout": "vertical", "flex": 1,
                               "backgroundColor": C_GREEN if i < bar_w else "#E0E0E0",
                               "height": "8px", "cornerRadius": "2px",
                               "contents": []})
        row_items.append({
            "type": "box", "layout": "vertical", "margin": "sm",
            "contents": [
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": f"อายุ {a} ปี", "size": "xs", "color": C_GRAY, "flex": 2},
                    {"type": "text", "text": f"฿{fmt(tot)}", "size": "xs",
                     "color": C_POS if ratio >= 1.0 else C_DARK, "weight": "bold", "flex": 3, "align": "end"},
                    {"type": "text", "text": f"{pct}%", "size": "xs", "color": C_GREEN, "flex": 1, "align": "end"},
                ]},
                {"type": "box", "layout": "horizontal", "margin": "xs", "spacing": "xs", "contents": bar_cells},
            ]
        })
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": C_BLUE, "paddingAll": "18px",
                   "contents": [
                       {"type": "text", "text": "🎯 เป้าหมายเกษียณ", "color": C_WHITE, "size": "lg", "weight": "bold"},
                       {"type": "text", "text": "การจำลองการเติบโตของเงินทุน", "color": "#BBDEFB", "size": "xs", "margin": "xs"},
                   ]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "18px",
                 "contents": [
                     sec_header("🏆", "เงินเป้าหมายที่ต้องมี", C_BLUE),
                     divider(),
                     row_item("วิธี 4% Rule", f"฿{fmt(fund_4pct)}"),
                     row_item(f"วิธี PV Annuity ({n_retire} ปี)", f"฿{fmt(fund_pv)}"),
                     {"type": "box", "layout": "horizontal", "margin": "md",
                      "backgroundColor": C_LIGHT, "cornerRadius": "8px", "paddingAll": "12px",
                      "contents": [
                          {"type": "text", "text": "🎯 เป้าหมาย", "size": "sm", "color": C_GREEN, "weight": "bold", "flex": 5},
                          {"type": "text", "text": f"฿{fmt(target)}", "size": "md", "color": C_GREEN, "weight": "bold", "flex": 4, "align": "end"},
                      ]},
                     sec_header("📈", f"จำลองการเติบโต ({r*100:.0f}%/ปี)", C_BLUE),
                     divider(),
                     *row_items,
                     divider(),
                     {"type": "box", "layout": "vertical", "margin": "md",
                      "backgroundColor": "#E3F2FD", "cornerRadius": "8px", "paddingAll": "12px",
                      "contents": [
                          {"type": "text", "text": "💰 คาดการณ์ ณ วันเกษียณ", "size": "sm", "color": C_BLUE, "weight": "bold"},
                          row_item("เงินก้อนเดิมเติบโต", f"฿{fmt(fv_exist)}"),
                          row_item("DCA สะสมทั้งหมด", f"฿{fmt(fv_dca_v)}"),
                          {"type": "separator", "margin": "sm"},
                          {"type": "box", "layout": "horizontal", "margin": "sm",
                           "contents": [
                               {"type": "text", "text": "รวมทั้งหมด", "size": "md", "weight": "bold", "color": C_DARK, "flex": 1},
                               {"type": "text", "text": f"฿{fmt(projected)}", "size": "md", "weight": "bold", "color": C_BLUE, "flex": 1, "align": "end"},
                           ]},
                      ]},
                 ]},
    }

def build_bigpicture_flex(projected, target):
    fmt = lambda n, d=0: f"{n:,.{d}f}"
    gap = projected - target
    is_success = gap >= 0
    color = C_POS if is_success else C_NEG
    emoji = "🎉" if is_success else "⚠️"
    status_msg = "ยอดเยี่ยม! คุณจะบรรลุเป้าหมาย" if is_success else "เป้าหมายยังขาดอยู่อีกนิด"
    
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": "#FFC107", "paddingAll": "18px",
                   "contents": [
                       {"type": "text", "text": "🔭 ภาพรวมความสำเร็จ", "color": C_DARK, "size": "lg", "weight": "bold"},
                       {"type": "text", "text": "Big Picture & Action Plan", "color": C_GRAY, "size": "xs", "margin": "xs"},
                   ]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "18px",
                 "contents": [
                     {"type": "text", "text": f"{emoji} {status_msg}", "size": "md", "weight": "bold", "color": color, "align": "center"},
                     {"type": "text", "text": f"{'+' if is_success else ''}฿{fmt(gap)}", "size": "xl", "weight": "bold", "color": color, "align": "center", "margin": "md"},
                     divider(),
                     sec_header("💡", "คำแนะนำเพิ่มเติม", C_DARK),
                     {"type": "text", "text": "• ปรับเพิ่มเงินลงทุนต่อเดือน (DCA) ขึ้นอีก 10-15%" if not is_success else "• รักษาวินัยการลงทุนนี้ไว้อย่างต่อเนื่อง", "size": "sm", "color": C_GRAY, "wrap": True},
                     {"type": "text", "text": "• ลดรายจ่ายที่ไม่จำเป็นเพื่อเพิ่มเงินออม", "size": "sm", "color": C_GRAY, "wrap": True},
                     {"type": "text", "text": "• ทบทวนพอร์ตการลงทุนอย่างน้อยปีละ 1 ครั้ง", "size": "sm", "color": C_GRAY, "wrap": True}
                 ]}
    }

def build_scenario_flex(rcfg):
    alloc_items = []
    for k, v in rcfg["alloc"].items():
        alloc_items.append(row_item(k, f"{v}%", C_DARK))
        
    product_items = []
    for pid in rcfg.get("products", []):
        if pid in PRODUCTS:
            p = PRODUCTS[pid]
            product_items.append({"type": "text", "text": f"🔹 {p['name']} (ผลตอบแทนอ้างอิง {p['rate']}%)", "size": "xs", "color": C_GRAY, "wrap": True, "margin": "sm"})
            
    return {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": rcfg["color"], "paddingAll": "18px",
                   "contents": [
                       {"type": "text", "text": "🔮 แผนการลงทุนที่แนะนำ", "color": C_WHITE, "size": "lg", "weight": "bold"},
                       {"type": "text", "text": "ผลิตภัณฑ์กรุงศรีที่เหมาะกับคุณ", "color": C_WHITE, "size": "xs", "margin": "xs"},
                   ]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "18px",
                 "contents": [
                     sec_header("⚖", f"ความเสี่ยง: {rcfg['name']}", rcfg["color"]),
                     {"type": "text", "text": rcfg["desc"], "size": "sm", "color": C_GRAY, "wrap": True, "margin": "sm"},
                     divider(),
                     sec_header("พอร์ตการลงทุนที่แนะนำ", "", C_DARK),
                     *alloc_items,
                     divider(),
                     sec_header("ตัวอย่างผลิตภัณฑ์", "", C_DARK),
                     *product_items
                 ]}
    }

# ═══════════════════════════════════════════════════════════════════════════════
#  Business Logic Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def validate(step, val, data):
    if step == 0 and val < 18: return "อายุควรมากกว่า 18 ปีค่ะ"
    if step == 1 and val <= data.get("current_age", 0): return "อายุเกษียณต้องมากกว่าอายุปัจจุบันค่ะ"
    if step == 2 and val <= data.get("retire_age", 0): return "อายุขัยต้องมากกว่าอายุเกษียณค่ะ"
    if step == 6 and val not in [1, 2, 3]: return "กรุณาพิมพ์ระดับความเสี่ยง 1, 2 หรือ 3 ค่ะ"
    return None

def calculate(data):
    age = data["current_age"]
    retire = data["retire_age"]
    life_exp = data["life_expectancy"]
    fixed_ex = data["fixed_expense"]
    var_ex = data["variable_expense"]
    income = data["monthly_income"]
    risk = data["risk_level"]
    invest = data["current_investment"]
    dca = data["monthly_dca"]

    n_accum = retire - age
    n_retire = life_exp - retire
    total_exp = fixed_ex + var_ex
    net_flow = income - total_exp
    save_pct = (net_flow / income * 100) if income > 0 else 0

    rcfg = RISK_CONFIG[risk]
    r = rcfg["return"]

    # Inflation adjusted expenses at retirement
    inflation_factor = (1 + INFLATION) ** n_accum
    exp_retire_m = total_exp * inflation_factor
    exp_retire_y = exp_retire_m * 12
    
    # Target funds
    fund_4pct = exp_retire_y / 0.04
    # Simple PV calculation (approximated for real return)
    real_return = (1 + r) / (1 + INFLATION) - 1
    if real_return == 0:
        fund_pv = exp_retire_y * n_retire
    else:
        fund_pv = exp_retire_y * ((1 - (1 + real_return)**-n_retire) / real_return)
        
    target = max(fund_4pct, fund_pv)

    # Future Value projections
    fv_exist = invest * ((1 + r) ** n_accum)
    fv_dca_v = dca * 12 * (((1 + r) ** n_accum - 1) / r) if r > 0 else dca * 12 * n_accum
    projected = fv_exist + fv_dca_v
    
    withdrawal_y = projected * 0.04
    withdrawal_m = withdrawal_y / 12

    # Simulate growth milestones
    rows_data = []
    checkpoints = [age + n_accum // 3, age + 2 * (n_accum // 3), retire]
    for cp in checkpoints:
        yrs = cp - age
        v_ex = invest * ((1 + r) ** yrs)
        v_dc = dca * 12 * (((1 + r) ** yrs - 1) / r) if r > 0 else dca * 12 * yrs
        rows_data.append((cp, v_ex, v_dc, v_ex + v_dc))

    flex1 = build_profile_flex(age, retire, life_exp, n_accum, n_retire, income, fixed_ex, var_ex, total_exp, net_flow, save_pct, invest, dca, exp_retire_m, exp_retire_y, withdrawal_m, withdrawal_y, rcfg, r)
    flex2 = build_goal_flex(target, fund_4pct, fund_pv, n_retire, r, projected, fv_exist, fv_dca_v, rows_data)
    flex3 = build_bigpicture_flex(projected, target)
    flex4 = build_scenario_flex(rcfg)
    
    return [flex1, flex2, flex3, flex4]

# ═══════════════════════════════════════════════════════════════════════════════
#  LINE Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def extract_number(text):
    m = re.search(r'\d+(\.\d+)?', text.replace(",", ""))
    return float(m.group()) if m else None

def make_flex(alt, flex_dict):
    return FlexMessage(alt_text=alt, contents=FlexContainer.from_dict(flex_dict))

def reply_flex(api, token, alt, flex_dict):
    api.reply_message(ReplyMessageRequest(reply_token=token,
                                          messages=[make_flex(alt, flex_dict)]))

def reply_text(api, token, text):
    api.reply_message(ReplyMessageRequest(reply_token=token,
                                          messages=[TextMessage(text=text)]))

def push_flex_list(api, uid, flex_list, alts):
    msgs = [make_flex(alts[i], flex_list[i]) for i in range(len(flex_list))]
    for i in range(0, len(msgs), 5):
        api.push_message(PushMessageRequest(to=uid, messages=msgs[i:i+5]))

# ═══════════════════════════════════════════════════════════════════════════════
#  LINE Event Handler
# ═══════════════════════════════════════════════════════════════════════════════

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    uid  = event.source.user_id if isinstance(event.source, UserSource) else str(event.source)
    text = event.message.text.strip()
    cmd  = text.lower()

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        try:
            if cmd in {"เริ่มใหม่","reset","ใหม่","สวัสดี","หวัดดี","hi","hello","ไหว้"}:
                sessions.pop(uid, None)
                reply_flex(api, event.reply_token, "ยินดีต้อนรับสู่ KrungsriRetire", build_welcome_flex())
                return

            if cmd in {"เริ่ม","begin","คำนวณ","ลอง","start","เริ่มต้น"}:
                sessions[uid] = {"step": 0, "data": {}}
                reply_flex(api, event.reply_token,
                           f"คำถามข้อ 1: {QUESTIONS[0]['label']}", build_question_flex(0))
                return

            if uid not in sessions:
                reply_flex(api, event.reply_token, "ยินดีต้อนรับสู่ Financial Retirement Planning", build_welcome_flex())
                return

            sess = sessions[uid]
            step = sess["step"]
            data = sess["data"]

            val = extract_number(text)
            if val is None or val < 0:
                reply_text(api, event.reply_token,
                           f"⚠️ กรุณากรอกเป็นตัวเลขนะคะ\n{QUESTIONS[step]['example']}")
                return

            err = validate(step, val, data)
            if err:
                reply_text(api, event.reply_token, err)
                return

            key = QUESTIONS[step]["key"]
            data[key] = int(val) if key in {"current_age","retire_age","life_expectancy","risk_level"} else float(val)

            step += 1
            sess["step"] = step

            if step < TOTAL_STEPS:
                reply_flex(api, event.reply_token,
                           f"คำถามข้อ {QUESTIONS[step]['step_label']}: {QUESTIONS[step]['label']}",
                           build_question_flex(step))
                return

            del sessions[uid]
            reply_text(api, event.reply_token,
                       "⏳ กำลังวิเคราะห์และจัดทำรายงาน...\nกรุณารอสักครู่นะคะ 📊")
            results = calculate(data)
            push_flex_list(api, uid, results, [
                "📋 รายงานแผนเกษียณ (1/4) — ข้อมูลส่วนตัว",
                "🎯 รายงานแผนเกษียณ (2/4) — เป้าหมายและการเติบโต",
                "🔭 รายงานแผนเกษียณ (3/4) — Big Picture & Action Plan",
                "🔮 รายงานแผนเกษียณ (4/4) — Scenario & ผลิตภัณฑ์",
            ])

        except Exception as e:
            logger.exception(f"Error: {e}")
            reply_text(api, event.reply_token,
                       "⚠️ เกิดข้อผิดพลาดบางอย่างค่ะ\nกรุณาพิมพ์ 'เริ่มใหม่' เพื่อลองใหม่อีกครั้ง")

# ═══════════════════════════════════════════════════════════════════════════════
#  FastAPI
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="KrungsriRetire Bot")

@app.get("/")
def root(): return {"status": "ok", "bot": "KrungsriRetire", "version": "3.0"}

@app.get("/health")
def health(): return {"status": "ok", "active_sessions": len(sessions)}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    sig  = request.headers.get("X-Line-Signature", "")
    background_tasks.add_task(process_event, body.decode("utf-8"), sig)
    return JSONResponse(content={"status": "ok"})

def process_event(body, sig):
    try:
        handler.handle(body, sig)
    except InvalidSignatureError:
        logger.warning("Invalid LINE signature")
    except Exception as e:
        logger.exception(f"Error: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
