"""
🏦 KrungsriRetire Bot — LINE Bot วางแผนเกษียณ
FastAPI + LINE SDK v3 + Flex Messages  |  v4.0
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

# ── Validation Limits ──────────────────────────────────────────────────────────
LIMITS = {
    "current_age":        (15, 80,   "อายุควรอยู่ระหว่าง 15–80 ปี"),
    "retire_age":         (30, 90,   "อายุเกษียณควรอยู่ระหว่าง 30–90 ปี"),
    "life_expectancy":    (50, 120,  "อายุขัยควรอยู่ระหว่าง 50–120 ปี"),
    "fixed_expense":      (0,  10_000_000, "ค่าใช้จ่ายคงที่ไม่ควรเกิน 10,000,000 บาท/เดือน"),
    "variable_expense":   (0,  10_000_000, "ค่าใช้จ่ายแปรผันไม่ควรเกิน 10,000,000 บาท/เดือน"),
    "monthly_income":     (0,  50_000_000, "รายรับไม่ควรเกิน 50,000,000 บาท/เดือน"),
    "risk_level":         (1,  3,    "กรุณาพิมพ์ระดับความเสี่ยง 1, 2 หรือ 3 เท่านั้น"),
    "current_investment": (0,  1_000_000_000, "เงินออมสะสมไม่ควรเกิน 1,000,000,000 บาท"),
    "monthly_dca":        (0,  10_000_000, "DCA ต่อเดือนไม่ควรเกิน 10,000,000 บาท"),
}

PRODUCTS = {
    "saving":    {"name": "เงินฝากออมทรัพย์ กรุงศรี",        "rate": 1.0,  "tag": "ความเสี่ยงต่ำ",         "color": "#4CAF50"},
    "fixed_1y":  {"name": "เงินฝากประจำ 12 เดือน กรุงศรี",  "rate": 2.0,  "tag": "ความเสี่ยงต่ำ",         "color": "#66BB6A"},
    "kf_money":  {"name": "KF-MONEYA (ตลาดเงิน)",            "rate": 3.0,  "tag": "ความเสี่ยงต่ำ-กลาง",   "color": "#29B6F6"},
    "kf_fixed":  {"name": "KF-FIXEDPLUS (ตราสารหนี้)",        "rate": 3.5,  "tag": "ความเสี่ยงปานกลาง",    "color": "#26C6DA"},
    "kf_rmf":    {"name": "KF-RMFA (RMF ตราสารหนี้)",        "rate": 4.0,  "tag": "ลดหย่อนภาษี",          "color": "#FF9800"},
    "kf_bal":    {"name": "KF-BALANCED (กองทุนผสม)",          "rate": 6.0,  "tag": "ความเสี่ยงปานกลาง",    "color": "#EF5350"},
    "kf_ssf":    {"name": "KF-SSFPLUS (SSF ผสม)",             "rate": 6.0,  "tag": "ลดหย่อนภาษี",          "color": "#FFA726"},
    "kf_star":   {"name": "KF-STAR (ผสมเน้นหุ้น)",            "rate": 6.5,  "tag": "ความเสี่ยงสูง",        "color": "#EC407A"},
    "kf_growth": {"name": "KF-GROWTH (หุ้นในประเทศ)",         "rate": 8.0,  "tag": "ความเสี่ยงสูง",        "color": "#F44336"},
    "kf_rmfg":   {"name": "KF-RMFG (RMF หุ้น)",              "rate": 8.0,  "tag": "ลดหย่อนภาษี+สูง",     "color": "#C62828"},
    "kf_gtech":  {"name": "KF-GTECH (หุ้นเทคโนโลยีโลก)",    "rate": 9.0,  "tag": "ความเสี่ยงสูงมาก",    "color": "#7B1FA2"},
}

RISK_CONFIG = {
    1: {
        "name": "🟢 ต่ำ (Conservative)", "return": 0.04, "color": "#2E7D32",
        "alloc": [
            ("เงินฝากออมทรัพย์/ประจำ", 40, "#4CAF50"),
            ("กองทุนตลาดเงิน/ตราสารหนี้", 50, "#29B6F6"),
            ("กองทุนผสม", 10, "#FF9800"),
        ],
        "products": ["saving", "fixed_1y", "kf_money", "kf_fixed"],
        "desc": "รักษาเงินต้น ยอมรับผลตอบแทนต่ำกว่า เหมาะกับผู้ใกล้เกษียณหรือไม่ชอบความเสี่ยง",
        "icon": "🛡️",
    },
    2: {
        "name": "🟡 ปานกลาง (Moderate)", "return": 0.06, "color": "#E65100",
        "alloc": [
            ("เงินฝากประจำ", 20, "#66BB6A"),
            ("กองทุนตราสารหนี้", 30, "#29B6F6"),
            ("กองทุนผสม/หุ้น", 50, "#FF9800"),
        ],
        "products": ["fixed_1y", "kf_fixed", "kf_bal", "kf_star", "kf_ssf", "kf_rmf"],
        "desc": "สมดุลระหว่างการเติบโตและความมั่นคง เหมาะกับนักลงทุนทั่วไปที่มีระยะยาว",
        "icon": "⚖️",
    },
    3: {
        "name": "🔴 สูง (Aggressive)", "return": 0.08, "color": "#B71C1C",
        "alloc": [
            ("เงินฝากประจำ", 5, "#66BB6A"),
            ("กองทุนตราสารหนี้", 15, "#29B6F6"),
            ("กองทุนผสม", 20, "#FF9800"),
            ("กองทุนหุ้น", 60, "#F44336"),
        ],
        "products": ["kf_bal", "kf_growth", "kf_gtech", "kf_rmfg"],
        "desc": "เน้นผลตอบแทนระยะยาว ยอมรับความผันผวนสูงได้ เหมาะกับอายุน้อยและมีเวลาลงทุนนาน",
        "icon": "🚀",
    },
}

QUESTIONS = [
    {
        "key": "current_age", "step_label": "1/8", "emoji": "🎂",
        "label": "อายุปัจจุบันของคุณ",
        "hint": "ระบุเป็นจำนวนปีเต็ม (15–80 ปี)",
        "example": "เช่น พิมพ์: 30",
        "unit": "ปี",
    },
    {
        "key": "retire_age", "step_label": "2/8", "emoji": "🏖️",
        "label": "อายุที่ต้องการเกษียณ",
        "hint": "ต้องมากกว่าอายุปัจจุบัน (30–90 ปี)",
        "example": "เช่น พิมพ์: 60",
        "unit": "ปี",
    },
    {
        "key": "life_expectancy", "step_label": "3/8", "emoji": "⏳",
        "label": "อายุขัยที่วางแผน",
        "hint": "คนไทยเฉลี่ย 80 ปี / WHO แนะนำ 85 ปี (50–120 ปี)",
        "example": "เช่น พิมพ์: 85",
        "unit": "ปี",
    },
    {
        "key": "fixed_expense", "step_label": "4/8", "emoji": "🏠",
        "label": "ค่าใช้จ่ายคงที่ต่อเดือน",
        "hint": "ผ่อนบ้าน/รถ เบี้ยประกัน ค่างวดต่างๆ\nหากไม่มีให้พิมพ์ 0",
        "example": "เช่น พิมพ์: 15000",
        "unit": "บาท/เดือน",
    },
    {
        "key": "variable_expense", "step_label": "5/8", "emoji": "🛍️",
        "label": "ค่าใช้จ่ายแปรผันต่อเดือน",
        "hint": "อาหาร ค่าเดินทาง ช้อปปิ้ง สาธารณูปโภค",
        "example": "เช่น พิมพ์: 12000",
        "unit": "บาท/เดือน",
    },
    {
        "key": "monthly_income", "step_label": "6/8", "emoji": "💰",
        "label": "รายรับทั้งหมดต่อเดือน",
        "hint": "รวมเงินเดือน โบนัส (เฉลี่ย) และรายได้เสริมทุกทาง",
        "example": "เช่น พิมพ์: 50000",
        "unit": "บาท/เดือน",
    },
    {
        "key": "risk_level", "step_label": "7/8", "emoji": "⚖️",
        "label": "ระดับความเสี่ยงที่รับได้",
        "hint": "1 = ต่ำ (~4%/ปี)  |  2 = ปานกลาง (~6%/ปี)  |  3 = สูง (~8%/ปี)",
        "example": "พิมพ์ตัวเลข: 1, 2 หรือ 3",
        "unit": "",
    },
    {
        "key": "current_investment", "step_label": "8.1/8", "emoji": "🏦",
        "label": "เงินออม/ลงทุนที่มีอยู่แล้ว",
        "hint": "รวมเงินฝาก กองทุน หุ้น ทุกประเภท\nหากยังไม่มีให้พิมพ์ 0",
        "example": "เช่น พิมพ์: 200000",
        "unit": "บาท",
    },
    {
        "key": "monthly_dca", "step_label": "8.2/8", "emoji": "📅",
        "label": "เงินที่จะลงทุนเพิ่มทุกเดือน (DCA)",
        "hint": "จำนวนที่วางแผนลงทุนสม่ำเสมอต่อเดือน\nหากยังไม่มีแผนให้พิมพ์ 0",
        "example": "เช่น พิมพ์: 5000",
        "unit": "บาท/เดือน",
    },
]
TOTAL_STEPS = len(QUESTIONS)

# ── Color Palette ──────────────────────────────────────────────────────────────
C_GREEN  = "#006B3F"
C_GREEN2 = "#00894F"
C_GOLD   = "#C9A84C"
C_LIGHT  = "#F1F8E9"
C_WHITE  = "#FFFFFF"
C_DARK   = "#1A1A2E"
C_GRAY   = "#757575"
C_LGRAY  = "#F5F5F5"
C_POS    = "#2E7D32"
C_NEG    = "#C62828"
C_BLUE   = "#1565C0"
C_LBLUE  = "#E3F2FD"
C_WARN   = "#E65100"

# ═══════════════════════════════════════════════════════════════════════════════
#  Flex Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def txt(text, size="sm", color=C_DARK, weight="regular", align="start",
        wrap=True, flex=None, margin=None):
    o = {"type": "text", "text": str(text), "size": size, "color": color,
         "weight": weight, "align": align, "wrap": wrap}
    if flex is not None: o["flex"] = flex
    if margin:           o["margin"] = margin
    return o

def row_item(label, value, val_color=None, bold=False, label_flex=5, val_flex=4):
    return {
        "type": "box", "layout": "horizontal", "margin": "sm",
        "contents": [
            txt(label, color=C_GRAY, flex=label_flex),
            txt(value, color=val_color or C_DARK,
                weight="bold" if bold else "regular",
                flex=val_flex, align="end"),
        ],
    }

def sec_header(emoji, title, color=None):
    return {
        "type": "box", "layout": "horizontal", "margin": "md",
        "contents": [
            txt(emoji, size="sm", flex=0),
            txt(f"  {title}", size="sm", weight="bold",
                color=color or C_GREEN, flex=1),
        ],
    }

def divider(margin="sm"):
    return {"type": "separator", "margin": margin, "color": "#E0E0E0"}

def empty_box():
    return {"type": "box", "layout": "vertical", "contents": []}

def prog_bar(ratio, w=10, color=C_GREEN):
    filled = max(0, min(w, round(ratio * w)))
    cells = []
    for i in range(w):
        cells.append({
            "type": "box", "layout": "vertical", "flex": 1,
            "height": "6px", "cornerRadius": "3px",
            "backgroundColor": color if i < filled else "#E0E0E0",
            "contents": [],
        })
    return {"type": "box", "layout": "horizontal", "spacing": "xs", "contents": cells}

def chip(contents, bg=C_LIGHT):
    return {
        "type": "box", "layout": "vertical",
        "backgroundColor": bg, "cornerRadius": "10px",
        "paddingAll": "12px", "margin": "md",
        "contents": contents,
    }

def tag_box(label, color):
    return {
        "type": "box", "layout": "vertical", "flex": 0,
        "backgroundColor": color + "22",
        "cornerRadius": "6px", "paddingStart": "8px",
        "paddingEnd": "8px", "paddingTop": "3px", "paddingBottom": "3px",
        "contents": [txt(label, size="xxs", color=color, weight="bold")],
    }

# ═══════════════════════════════════════════════════════════════════════════════
#  Flex Builders
# ═══════════════════════════════════════════════════════════════════════════════

def build_welcome_flex():
    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": C_GREEN, "paddingAll": "22px",
            "contents": [
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {"type": "text", "text": "🏦", "size": "xxl", "flex": 0},
                        {
                            "type": "box", "layout": "vertical", "flex": 1,
                            "margin": "md",
                            "contents": [
                                txt("KrungsriRetire", size="xl", color=C_WHITE, weight="bold"),
                                txt("ผู้ช่วยวางแผนเกษียณอัจฉริยะ", size="xs", color=C_GOLD),
                            ],
                        },
                    ],
                },
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "20px",
            "contents": [
                txt("ระบบจะช่วยคุณ", size="md", weight="bold", color=C_DARK),
                {"type": "box", "layout": "vertical", "margin": "md", "spacing": "sm",
                 "contents": [
                     {
                         "type": "box", "layout": "horizontal", "spacing": "sm",
                         "contents": [
                             txt("✅", size="sm", flex=0),
                             txt("คำนวณเงินเป้าหมายเกษียณที่แม่นยำ", size="sm", color=C_GRAY, flex=1),
                         ],
                     },
                     {
                         "type": "box", "layout": "horizontal", "spacing": "sm",
                         "contents": [
                             txt("✅", size="sm", flex=0),
                             txt("วางแผนออมและลงทุนระยะยาว", size="sm", color=C_GRAY, flex=1),
                         ],
                     },
                     {
                         "type": "box", "layout": "horizontal", "spacing": "sm",
                         "contents": [
                             txt("✅", size="sm", flex=0),
                             txt("แนะนำผลิตภัณฑ์กรุงศรีที่เหมาะสม", size="sm", color=C_GRAY, flex=1),
                         ],
                     },
                     {
                         "type": "box", "layout": "horizontal", "spacing": "sm",
                         "contents": [
                             txt("✅", size="sm", flex=0),
                             txt("ลดหย่อนภาษีผ่าน SSF / RMF", size="sm", color=C_GRAY, flex=1),
                         ],
                     },
                     {
                         "type": "box", "layout": "horizontal", "spacing": "sm",
                         "contents": [
                             txt("✅", size="sm", flex=0),
                             txt("วิเคราะห์ด้วย 4% Rule & PV Annuity", size="sm", color=C_GRAY, flex=1),
                         ],
                     },
                 ]},
                divider("lg"),
                chip([
                    {
                        "type": "box", "layout": "horizontal",
                        "contents": [
                            {"type": "box", "layout": "vertical", "flex": 1, "alignItems": "center",
                             "contents": [txt("8", size="xl", color=C_GREEN, weight="bold", align="center"), txt("คำถาม", size="xxs", color=C_GRAY, align="center")]},
                            {"type": "separator", "color": "#E0E0E0"},
                            {"type": "box", "layout": "vertical", "flex": 1, "alignItems": "center",
                             "contents": [txt("4", size="xl", color=C_GREEN, weight="bold", align="center"), txt("รายงาน", size="xxs", color=C_GRAY, align="center")]},
                            {"type": "separator", "color": "#E0E0E0"},
                            {"type": "box", "layout": "vertical", "flex": 1, "alignItems": "center",
                             "contents": [txt("~2", size="xl", color=C_GREEN, weight="bold", align="center"), txt("นาที", size="xxs", color=C_GRAY, align="center")]},
                        ],
                    },
                ]),
            ],
        },
        "footer": {
            "type": "box", "layout": "vertical",
            "paddingAll": "16px", "backgroundColor": C_LIGHT, "spacing": "sm",
            "contents": [
                {
                    "type": "button", "style": "primary", "color": C_GREEN,
                    "height": "sm",
                    "action": {"type": "message", "label": "🚀 เริ่มวางแผนเลย!", "text": "เริ่ม"},
                },
                txt("พิมพ์ 'เริ่มใหม่' เพื่อรีเซ็ตข้อมูลได้ตลอดเวลา",
                    size="xxs", color=C_GRAY, align="center", margin="sm"),
            ],
        },
    }


def build_question_flex(step_idx):
    q = QUESTIONS[step_idx]
    ratio = step_idx / TOTAL_STEPS
    pct   = int(ratio * 100)

    # Special UI for risk level
    extra_contents = []
    if q["key"] == "risk_level":
        for lvl, cfg in RISK_CONFIG.items():
            extra_contents.append({
                "type": "box", "layout": "horizontal",
                "backgroundColor": cfg["color"] + "15",
                "cornerRadius": "8px", "paddingAll": "10px", "margin": "sm",
                "contents": [
                    txt(f"{lvl}", size="lg", color=cfg["color"], weight="bold", flex=0),
                    {
                        "type": "box", "layout": "vertical", "flex": 1, "margin": "md",
                        "contents": [
                            txt(cfg["name"], size="sm", color=cfg["color"], weight="bold"),
                            txt(f'ผลตอบแทนคาด ~{cfg["return"]*100:.0f}%/ปี', size="xs", color=C_GRAY),
                        ],
                    },
                ],
            })

    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": C_GREEN, "paddingAll": "15px",
            "contents": [
                {
                    "type": "box", "layout": "horizontal", "margin": "none",
                    "contents": [
                        txt(f"ขั้นตอนที่ {q['step_label']}", size="xs", color=C_GOLD, flex=1),
                        txt(f"{pct}%", size="xs", color=C_WHITE, align="end"),
                    ],
                },
                {**prog_bar(ratio), "margin": "sm"},
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "20px",
            "contents": [
                {
                    "type": "box", "layout": "horizontal", "spacing": "md",
                    "contents": [
                        txt(q["emoji"], size="xxl", flex=0),
                        {
                            "type": "box", "layout": "vertical", "flex": 1,
                            "contents": [
                                txt(q["label"], size="md", color=C_DARK, weight="bold"),
                                txt(q["hint"], size="xs", color=C_GRAY, margin="xs"),
                            ],
                        },
                    ],
                },
                divider(),
                *([chip([txt("✏️  " + q["example"], size="sm", color=C_GREEN)])]
                  if q["key"] != "risk_level" else []),
                *extra_contents,
                *([] if not q.get("unit") else [
                    {
                        "type": "box", "layout": "horizontal", "margin": "sm",
                        "contents": [
                            txt("หน่วย:", size="xs", color=C_GRAY, flex=0),
                            txt(q["unit"], size="xs", color=C_GREEN, weight="bold", flex=1, margin="sm"),
                        ],
                    },
                ]),
            ],
        },
    }


def build_profile_flex(d, n_accum, n_retire, total_exp, net_flow, save_pct,
                       exp_retire_m, exp_retire_y, withdrawal_m, withdrawal_y, rcfg, r):
    fmt = lambda n, decimals=0: f"{n:,.{decimals}f}"
    save_pct_capped = min(max(save_pct, 0), 100)

    if save_pct >= 30:
        save_color, save_status = C_POS, "✅ ยอดเยี่ยม!"
    elif save_pct >= 20:
        save_color, save_status = C_POS, "✅ ดีมาก"
    elif save_pct >= 10:
        save_color, save_status = C_WARN, "⚠️ พอใช้"
    elif save_pct >= 0:
        save_color, save_status = C_NEG, "❌ ควรเพิ่ม"
    else:
        save_color, save_status = C_NEG, "❌ รายจ่ายเกินรายรับ!"

    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": C_GREEN, "paddingAll": "18px",
            "contents": [
                txt("📋 รายงานแผนเกษียณ (1/4)", size="lg", color=C_WHITE, weight="bold"),
                txt("ข้อมูลส่วนตัว & สถานะการเงิน", size="xs", color=C_GOLD, margin="xs"),
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "18px",
            "contents": [

                # ── ส่วน 1: ข้อมูลส่วนบุคคล ──────────────────────────────
                sec_header("👤", "ข้อมูลส่วนบุคคล & เป้าหมาย"),
                divider(),
                row_item("อายุปัจจุบัน",      f"{d['current_age']} ปี"),
                row_item("เกษียณที่อายุ",      f"{d['retire_age']} ปี  ({n_accum} ปีข้างหน้า)", C_GREEN, True),
                row_item("วางแผนถึงอายุ",      f"{d['life_expectancy']} ปี  (หลังเกษียณ {n_retire} ปี)"),
                row_item("ระดับความเสี่ยง",    rcfg["name"], rcfg["color"], True),
                row_item("ผลตอบแทนคาดการณ์",  f"{r*100:.0f}%/ปี", C_GREEN),
                row_item("อัตราเงินเฟ้อ",      "3%/ปี (สมมติฐาน)"),

                # ── ส่วน 2: สถานะการเงิน ──────────────────────────────────
                sec_header("💼", "สถานะการเงินปัจจุบัน"),
                divider(),
                row_item("รายรับต่อเดือน",         f"฿{fmt(d['monthly_income'])}", C_POS, True),
                row_item("ค่าใช้จ่ายคงที่/เดือน",  f"฿{fmt(d['fixed_expense'])}"),
                row_item("ค่าใช้จ่ายแปรผัน/เดือน", f"฿{fmt(d['variable_expense'])}"),
                row_item("รวมค่าใช้จ่าย/เดือน",    f"฿{fmt(total_exp)}"),
                row_item("เงินเหลือสุทธิ/เดือน",
                         f"฿{fmt(net_flow)}",
                         C_POS if net_flow >= 0 else C_NEG, True),
                {
                    "type": "box", "layout": "vertical", "margin": "sm",
                    "contents": [
                        {
                            "type": "box", "layout": "horizontal",
                            "contents": [
                                txt("อัตราการออม", size="sm", color=C_GRAY, flex=5),
                                txt(f"{fmt(save_pct, 1)}%  {save_status}",
                                    size="sm", color=save_color, weight="bold",
                                    flex=4, align="end"),
                            ],
                        },
                        {**prog_bar(save_pct_capped / 100, color=save_color), "margin": "xs"},
                    ],
                },
                row_item("เงินออมสะสมปัจจุบัน", f"฿{fmt(d['current_investment'])}"),
                row_item("DCA วางแผน/เดือน",    f"฿{fmt(d['monthly_dca'])}", C_GREEN),

                # ── ส่วน 3: ค่าใช้จ่ายหลังเกษียณ ──────────────────────────
                sec_header("📊", f"ค่าใช้จ่ายหลังเกษียณ (อายุ {d['retire_age']} ปี)"),
                divider(),
                txt("*ปรับตามเงินเฟ้อ 3%/ปี", size="xxs", color=C_GRAY, margin="none"),
                row_item("ค่าใช้จ่าย/เดือน", f"฿{fmt(exp_retire_m)}", C_NEG, True),
                row_item("ค่าใช้จ่าย/ปี",    f"฿{fmt(exp_retire_y)}"),
                chip([
                    txt("💧 ถอนได้ตามแผน 4% Rule", size="xs", color=C_GRAY, weight="bold"),
                    {
                        "type": "box", "layout": "horizontal", "margin": "sm",
                        "contents": [
                            txt(f"฿{fmt(withdrawal_m)}/เดือน", size="sm", color=C_POS, weight="bold", flex=1),
                            txt(f"฿{fmt(withdrawal_y)}/ปี", size="sm", color=C_POS, flex=1, align="end"),
                        ],
                    },
                ]),
            ],
        },
    }


def build_goal_flex(target, fund_4pct, fund_pv, n_retire, r,
                    projected, fv_exist, fv_dca_v, rows_data):
    fmt = lambda n, d=0: f"{n:,.{d}f}"

    row_items = []
    for a, fl, fd, tot in rows_data:
        ratio = min(tot / target, 1.0) if target > 0 else 0
        pct   = int(ratio * 100)
        bar_cells = []
        for i in range(8):
            bar_cells.append({
                "type": "box", "layout": "vertical", "flex": 1,
                "backgroundColor": C_GREEN if i < max(1, round(ratio * 8)) else "#E0E0E0",
                "height": "8px", "cornerRadius": "2px", "contents": [],
            })
        row_items += [
            {
                "type": "box", "layout": "vertical", "margin": "sm",
                "contents": [
                    {
                        "type": "box", "layout": "horizontal",
                        "contents": [
                            txt(f"อายุ {a} ปี", size="xs", color=C_GRAY, flex=3),
                            txt(f"฿{fmt(tot)}", size="xs",
                                color=C_POS if ratio >= 1.0 else C_DARK,
                                weight="bold", flex=4, align="end"),
                            txt(f"{pct}%", size="xs",
                                color=C_POS if ratio >= 1.0 else C_GREEN,
                                flex=2, align="end"),
                        ],
                    },
                    {"type": "box", "layout": "horizontal", "margin": "xs",
                     "spacing": "xs", "contents": bar_cells},
                ],
            },
        ]

    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": C_BLUE, "paddingAll": "18px",
            "contents": [
                txt("🎯 เป้าหมายเกษียณ (2/4)", size="lg", color=C_WHITE, weight="bold"),
                txt("การจำลองการเติบโตของเงินทุน", size="xs", color="#BBDEFB", margin="xs"),
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "18px",
            "contents": [

                # ── เงินเป้าหมาย ──────────────────────────────────────────
                sec_header("🏆", "เงินเป้าหมายที่ต้องมี", C_BLUE),
                divider(),
                row_item("วิธี 4% Rule", f"฿{fmt(fund_4pct)}"),
                row_item(f"วิธี PV Annuity ({n_retire} ปี)", f"฿{fmt(fund_pv)}"),
                txt("*ใช้ค่าที่สูงกว่าเพื่อความปลอดภัย", size="xxs", color=C_GRAY, margin="xs"),
                chip([
                    {
                        "type": "box", "layout": "horizontal",
                        "contents": [
                            txt("🎯 เป้าหมายสุดท้าย", size="sm", color=C_GREEN, weight="bold", flex=5),
                            txt(f"฿{fmt(target)}", size="md", color=C_GREEN, weight="bold", flex=4, align="end"),
                        ],
                    },
                ]),

                # ── จำลองการเติบโต ─────────────────────────────────────────
                sec_header("📈", f"จำลองการเติบโต ({r*100:.0f}%/ปี)", C_BLUE),
                divider(),
                *row_items,

                # ── สรุปยอด ณ วันเกษียณ ────────────────────────────────────
                divider(),
                chip([
                    txt("💰 คาดการณ์ ณ วันเกษียณ", size="sm", color=C_BLUE, weight="bold"),
                    row_item("เงินก้อนเดิมเติบโต", f"฿{fmt(fv_exist)}", label_flex=5, val_flex=4),
                    row_item("DCA สะสมทั้งหมด",    f"฿{fmt(fv_dca_v)}", label_flex=5, val_flex=4),
                    divider(),
                    {
                        "type": "box", "layout": "horizontal", "margin": "sm",
                        "contents": [
                            txt("รวมทั้งหมด", size="md", weight="bold", color=C_DARK, flex=1),
                            txt(f"฿{fmt(projected)}", size="md", weight="bold", color=C_BLUE, flex=1, align="end"),
                        ],
                    },
                ], bg=C_LBLUE),
            ],
        },
    }


def build_bigpicture_flex(projected, target, dca, n_accum, r, net_flow):
    fmt = lambda n, d=0: f"{n:,.{d}f}"
    gap        = projected - target
    is_success = gap >= 0
    color      = C_POS if is_success else C_NEG
    emoji      = "🎉" if is_success else "⚠️"
    status_msg = "ยอดเยี่ยม! แผนการเงินของคุณ\nบรรลุเป้าหมายเกษียณสำเร็จ" if is_success else "เป้าหมายยังขาดอยู่\nลองปรับแผนตามคำแนะนำด้านล่าง"
    header_bg  = "#1B5E20" if is_success else "#B71C1C"

    # คำนวณ DCA ที่ต้องเพิ่ม (ถ้ายังไม่ถึงเป้า)
    extra_tips = []
    if not is_success:
        shortage = abs(gap)
        # FV of 1 baht DCA per month for n_accum years
        fv_unit = (12 * (((1 + r) ** n_accum - 1) / r)) if r > 0 else 12 * n_accum
        extra_dca_needed = shortage / fv_unit if fv_unit > 0 else 0
        extra_tips += [
            row_item("DCA ที่ต้องเพิ่ม/เดือน",
                     f"฿{fmt(extra_dca_needed)}", C_NEG, True),
            row_item("รายจ่ายที่ควรลด/เดือน",
                     f"฿{fmt(extra_dca_needed)}", C_NEG),
        ]

    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": header_bg, "paddingAll": "18px",
            "contents": [
                txt("🔭 ภาพรวมความสำเร็จ (3/4)", size="lg", color=C_WHITE, weight="bold"),
                txt("Big Picture & Action Plan", size="xs", color="#FFD54F", margin="xs"),
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "18px",
            "contents": [

                # ── ผลลัพธ์หลัก ───────────────────────────────────────────
                chip([
                    txt(f"{emoji} {status_msg}", size="md", weight="bold",
                        color=color, align="center"),
                    txt(f"{'+' if is_success else '-'}฿{fmt(abs(gap))}",
                        size="xxl", weight="bold", color=color, align="center", margin="md"),
                    txt("ส่วนต่างจากเป้าหมาย", size="xs", color=C_GRAY, align="center"),
                ], bg=color + "11"),

                # ── สรุปตัวเลขสำคัญ ───────────────────────────────────────
                sec_header("📌", "สรุปตัวเลขสำคัญ", C_DARK),
                divider(),
                row_item("เงินที่จะมี ณ วันเกษียณ", f"฿{fmt(projected)}", C_BLUE, True),
                row_item("เงินเป้าหมาย",             f"฿{fmt(target)}"),
                *extra_tips,

                # ── คำแนะนำ ───────────────────────────────────────────────
                sec_header("💡", "คำแนะนำ Action Plan", C_DARK),
                divider(),
                chip([
                    *([
                        txt("✅ รักษาวินัยการลงทุน DCA ต่อเนื่อง", size="sm", color=C_DARK),
                        txt("✅ ทบทวน rebalance พอร์ตปีละ 1 ครั้ง", size="sm", color=C_DARK, margin="sm"),
                        txt("✅ เพิ่ม DCA ทุกครั้งที่รายได้ขึ้น", size="sm", color=C_DARK, margin="sm"),
                    ] if is_success else [
                        txt("⚡ เพิ่ม DCA รายเดือนตามยอดด้านบน", size="sm", color=C_DARK),
                        txt("⚡ ลดค่าใช้จ่ายที่ไม่จำเป็นออก", size="sm", color=C_DARK, margin="sm"),
                        txt("⚡ พิจารณาเพิ่มรายได้ทางเสริม", size="sm", color=C_DARK, margin="sm"),
                        txt("⚡ ปรับระดับความเสี่ยงให้สูงขึ้น (ถ้าทำได้)", size="sm", color=C_DARK, margin="sm"),
                    ]),
                    txt("📅 ทบทวนแผนทุก 1-2 ปี เพื่อปรับตามสถานการณ์", size="xs", color=C_GRAY, margin="md"),
                ]),
            ],
        },
    }


def build_scenario_flex(rcfg):
    alloc_cells = []
    for name, pct, color in rcfg["alloc"]:
        alloc_cells.append({
            "type": "box", "layout": "vertical",
            "flex": pct, "backgroundColor": color,
            "height": "12px", "contents": [],
        })

    product_items = []
    for pid in rcfg.get("products", []):
        p = PRODUCTS.get(pid)
        if not p:
            continue
        bar_pct = int((p["rate"] / 9.0) * 10)
        bar_cells = [
            {
                "type": "box", "layout": "vertical", "flex": 1,
                "backgroundColor": p["color"] if i < bar_pct else "#E0E0E0",
                "height": "4px", "cornerRadius": "2px", "contents": [],
            }
            for i in range(10)
        ]
        product_items.append({
            "type": "box", "layout": "vertical", "margin": "sm",
            "backgroundColor": "#FAFAFA", "cornerRadius": "8px",
            "paddingAll": "10px",
            "contents": [
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {
                            "type": "box", "layout": "vertical",
                            "width": "4px", "backgroundColor": p["color"],
                            "cornerRadius": "2px", "contents": [],
                        },
                        {
                            "type": "box", "layout": "vertical", "flex": 1, "margin": "sm",
                            "contents": [
                                txt(p["name"], size="xs", color=C_DARK, weight="bold"),
                                txt(p["tag"], size="xxs", color=C_GRAY),
                            ],
                        },
                        txt(f"{p['rate']}%", size="sm", color=p["color"], weight="bold", flex=0),
                    ],
                },
                {"type": "box", "layout": "horizontal", "margin": "xs",
                 "spacing": "xs", "contents": bar_cells},
            ],
        })

    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": rcfg["color"], "paddingAll": "18px",
            "contents": [
                txt("🔮 แผนการลงทุนที่แนะนำ (4/4)", size="lg", color=C_WHITE, weight="bold"),
                txt("ผลิตภัณฑ์กรุงศรีที่เหมาะกับคุณ", size="xs", color=C_WHITE, margin="xs"),
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "18px",
            "contents": [

                # ── ระดับความเสี่ยง ───────────────────────────────────────
                sec_header(rcfg["icon"], f"ระดับความเสี่ยง: {rcfg['name']}", rcfg["color"]),
                txt(rcfg["desc"], size="sm", color=C_GRAY, margin="sm"),
                divider(),

                # ── สัดส่วนพอร์ต ──────────────────────────────────────────
                sec_header("📊", "สัดส่วนพอร์ตที่แนะนำ", C_DARK),
                {
                    "type": "box", "layout": "horizontal",
                    "margin": "sm", "cornerRadius": "6px",
                    "contents": alloc_cells,
                },
                {
                    "type": "box", "layout": "horizontal", "margin": "sm",
                    "flexWrap": "wrap", "spacing": "sm",
                    "contents": [
                        {
                            "type": "box", "layout": "horizontal", "flex": 0,
                            "spacing": "xs", "margin": "xs",
                            "contents": [
                                {"type": "box", "layout": "vertical", "width": "8px", "height": "8px",
                                 "backgroundColor": c, "cornerRadius": "2px", "contents": []},
                                txt(f"{n} {p}%", size="xxs", color=C_GRAY, flex=0),
                            ],
                        }
                        for n, p, c in rcfg["alloc"]
                    ],
                },
                divider(),

                # ── ผลิตภัณฑ์แนะนำ ────────────────────────────────────────
                sec_header("🏦", "ผลิตภัณฑ์ที่แนะนำ", C_DARK),
                *product_items,

                # ── หมายเหตุ ──────────────────────────────────────────────
                chip([
                    txt("⚠️ หมายเหตุ", size="xs", color=C_WARN, weight="bold"),
                    txt("ผลตอบแทนเป็นการประมาณการอ้างอิง ไม่ใช่การรับประกันผลตอบแทนในอนาคต ควรศึกษาข้อมูลและปรึกษาผู้เชี่ยวชาญก่อนลงทุน",
                        size="xxs", color=C_GRAY, margin="sm"),
                ], bg="#FFF8E1"),
            ],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Business Logic
# ═══════════════════════════════════════════════════════════════════════════════

def extract_number(text: str):
    """แปลงข้อความเป็นตัวเลข รองรับ comma, ทศนิยม, ช่องว่าง"""
    cleaned = text.strip().replace(",", "").replace(" ", "")
    m = re.fullmatch(r'\d+(\.\d+)?', cleaned)
    return float(m.group()) if m else None


def validate(step: int, val: float, data: dict) -> str | None:
    """ตรวจสอบความถูกต้องของข้อมูล พร้อม cross-field validation"""
    key = QUESTIONS[step]["key"]

    # ── Range check ───────────────────────────────────────────────────────────
    if key in LIMITS:
        lo, hi, msg = LIMITS[key]
        if not (lo <= val <= hi):
            return f"❌ {msg}\n(คุณพิมพ์: {val:,.0f})"

    # ── Cross-field checks ────────────────────────────────────────────────────
    if key == "retire_age":
        if val <= data.get("current_age", 0):
            return f"❌ อายุเกษียณ ({val:.0f} ปี) ต้องมากกว่าอายุปัจจุบัน ({data['current_age']} ปี) ครับ"
        if val - data.get("current_age", 0) < 5:
            return "⚠️ ระยะเวลาสะสมน้อยมาก\nควรมีระยะเวลาลงทุนอย่างน้อย 5 ปีครับ"

    if key == "life_expectancy":
        if val <= data.get("retire_age", 0):
            return f"❌ อายุขัย ({val:.0f} ปี) ต้องมากกว่าอายุเกษียณ ({data['retire_age']} ปี) ครับ"
        if val - data.get("retire_age", 0) < 5:
            return "⚠️ ระยะเวลาหลังเกษียณน้อยมาก\nควรวางแผนอย่างน้อย 5 ปีหลังเกษียณครับ"

    if key == "monthly_dca":
        net = data.get("monthly_income", 0) - data.get("fixed_expense", 0) - data.get("variable_expense", 0)
        if net > 0 and val > net:
            return (f"⚠️ DCA ที่ตั้ง ({val:,.0f} บาท) มากกว่าเงินเหลือสุทธิ\n"
                    f"({net:,.0f} บาท/เดือน)\n\nต้องการดำเนินการต่อหรือไม่?\n"
                    f"กรุณาพิมพ์ยอดที่ไม่เกิน {net:,.0f} บาท")

    return None


def calculate(data: dict) -> list:
    """คำนวณและสร้าง Flex Messages ทั้ง 4 ใบ"""
    age     = data["current_age"]
    retire  = data["retire_age"]
    life    = data["life_expectancy"]
    fixed   = data["fixed_expense"]
    var_ex  = data["variable_expense"]
    income  = data["monthly_income"]
    risk    = data["risk_level"]
    invest  = data["current_investment"]
    dca     = data["monthly_dca"]

    n_accum  = retire - age
    n_retire = life - retire
    total_exp = fixed + var_ex
    net_flow  = income - total_exp
    save_pct  = (net_flow / income * 100) if income > 0 else 0.0

    rcfg = RISK_CONFIG[risk]
    r    = rcfg["return"]

    # ── ค่าใช้จ่ายหลังเกษียณ (ปรับเงินเฟ้อ) ────────────────────────────────
    inflation_f  = (1 + INFLATION) ** n_accum
    exp_retire_m = total_exp * inflation_f
    exp_retire_y = exp_retire_m * 12

    # ── เป้าหมาย ─────────────────────────────────────────────────────────────
    fund_4pct = exp_retire_y / 0.04
    real_r    = (1 + r) / (1 + INFLATION) - 1
    fund_pv   = (
        exp_retire_y * n_retire
        if real_r == 0
        else exp_retire_y * ((1 - (1 + real_r) ** -n_retire) / real_r)
    )
    target = max(fund_4pct, fund_pv)

    # ── FV ───────────────────────────────────────────────────────────────────
    fv_exist = invest * ((1 + r) ** n_accum)
    fv_dca_v = (
        dca * 12 * (((1 + r) ** n_accum - 1) / r)
        if r > 0
        else dca * 12 * n_accum
    )
    projected  = fv_exist + fv_dca_v
    withdrawal_y = projected * 0.04
    withdrawal_m = withdrawal_y / 12

    # ── Milestone rows ────────────────────────────────────────────────────────
    rows_data = []
    steps = max(2, min(4, n_accum))
    for i in range(1, steps):
        cp  = age + round(n_accum * i / steps)
        yrs = cp - age
        ve  = invest * ((1 + r) ** yrs)
        vd  = dca * 12 * (((1 + r) ** yrs - 1) / r) if r > 0 else dca * 12 * yrs
        rows_data.append((cp, ve, vd, ve + vd))
    rows_data.append((retire, fv_exist, fv_dca_v, projected))

    flex1 = build_profile_flex(data, n_accum, n_retire, total_exp, net_flow, save_pct,
                                exp_retire_m, exp_retire_y, withdrawal_m, withdrawal_y, rcfg, r)
    flex2 = build_goal_flex(target, fund_4pct, fund_pv, n_retire, r,
                             projected, fv_exist, fv_dca_v, rows_data)
    flex3 = build_bigpicture_flex(projected, target, dca, n_accum, r, net_flow)
    flex4 = build_scenario_flex(rcfg)

    return [flex1, flex2, flex3, flex4]


# ═══════════════════════════════════════════════════════════════════════════════
#  LINE Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def make_flex(alt: str, flex_dict: dict) -> FlexMessage:
    return FlexMessage(alt_text=alt, contents=FlexContainer.from_dict(flex_dict))

def reply_flex(api, token, alt, flex_dict):
    api.reply_message(ReplyMessageRequest(
        reply_token=token, messages=[make_flex(alt, flex_dict)]))

def reply_text(api, token, text):
    api.reply_message(ReplyMessageRequest(
        reply_token=token, messages=[TextMessage(text=text)]))

def push_flex_list(api, uid, flex_list, alts):
    msgs = [make_flex(alts[i], flex_list[i]) for i in range(len(flex_list))]
    for i in range(0, len(msgs), 5):
        api.push_message(PushMessageRequest(to=uid, messages=msgs[i:i+5]))


# ═══════════════════════════════════════════════════════════════════════════════
#  LINE Event Handler
# ═══════════════════════════════════════════════════════════════════════════════

RESET_CMDS = {"เริ่มใหม่", "reset", "ใหม่", "สวัสดี", "หวัดดี",
              "hi", "hello", "help", "ช่วยด้วย", "menu", "เมนู"}
START_CMDS = {"เริ่ม", "begin", "คำนวณ", "ลอง", "start", "เริ่มต้น", "go"}

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    uid  = event.source.user_id if isinstance(event.source, UserSource) else str(event.source)
    text = event.message.text.strip()
    cmd  = text.lower()

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        try:
            # ── Reset / Welcome ───────────────────────────────────────────
            if cmd in RESET_CMDS:
                sessions.pop(uid, None)
                reply_flex(api, event.reply_token,
                           "ยินดีต้อนรับสู่ KrungsriRetire",
                           build_welcome_flex())
                return

            # ── Start ─────────────────────────────────────────────────────
            if cmd in START_CMDS:
                sessions[uid] = {"step": 0, "data": {}}
                reply_flex(api, event.reply_token,
                           f"คำถามข้อ 1: {QUESTIONS[0]['label']}",
                           build_question_flex(0))
                return

            # ── No session → show welcome ─────────────────────────────────
            if uid not in sessions:
                reply_flex(api, event.reply_token,
                           "ยินดีต้อนรับสู่ KrungsriRetire",
                           build_welcome_flex())
                return

            sess = sessions[uid]
            step = sess["step"]
            data = sess["data"]

            # ── Parse number ──────────────────────────────────────────────
            val = extract_number(text)
            if val is None or val < 0:
                q = QUESTIONS[step]
                reply_text(api, event.reply_token,
                           f"⚠️ กรุณากรอกเป็นตัวเลขที่ถูกต้องนะครับ\n\n"
                           f"📌 {q['label']}\n"
                           f"💡 {q['hint']}\n\n"
                           f"{q['example']}")
                return

            # ── Validate ──────────────────────────────────────────────────
            err = validate(step, val, data)
            if err:
                reply_text(api, event.reply_token, err)
                return

            # ── Save answer ───────────────────────────────────────────────
            key = QUESTIONS[step]["key"]
            int_keys = {"current_age", "retire_age", "life_expectancy", "risk_level"}
            data[key] = int(val) if key in int_keys else float(val)

            step += 1
            sess["step"] = step

            # ── Next question ─────────────────────────────────────────────
            if step < TOTAL_STEPS:
                reply_flex(api, event.reply_token,
                           f"คำถามข้อ {QUESTIONS[step]['step_label']}: {QUESTIONS[step]['label']}",
                           build_question_flex(step))
                return

            # ── All done → calculate ──────────────────────────────────────
            del sessions[uid]
            reply_text(api, event.reply_token,
                       "⏳ กำลังวิเคราะห์และจัดทำรายงาน...\n"
                       "กรุณารอสักครู่นะครับ 📊")
            results = calculate(data)
            push_flex_list(api, uid, results, [
                "📋 รายงานแผนเกษียณ (1/4) — ข้อมูลส่วนตัว",
                "🎯 รายงานแผนเกษียณ (2/4) — เป้าหมายและการเติบโต",
                "🔭 รายงานแผนเกษียณ (3/4) — Big Picture & Action Plan",
                "🔮 รายงานแผนเกษียณ (4/4) — Scenario & ผลิตภัณฑ์",
            ])

        except Exception as e:
            logger.exception(f"Unhandled error for uid={uid}: {e}")
            reply_text(api, event.reply_token,
                       "⚠️ เกิดข้อผิดพลาดบางอย่างครับ\n"
                       "กรุณาพิมพ์ 'เริ่มใหม่' เพื่อลองใหม่อีกครั้ง")


# ═══════════════════════════════════════════════════════════════════════════════
#  FastAPI
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="KrungsriRetire Bot", version="4.0")

@app.get("/")
def root():
    return {"status": "ok", "bot": "KrungsriRetire", "version": "4.0",
            "active_sessions": len(sessions)}

@app.get("/health")
def health():
    return {"status": "ok", "active_sessions": len(sessions)}

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    sig  = request.headers.get("X-Line-Signature", "")
    background_tasks.add_task(_process, body.decode("utf-8"), sig)
    return JSONResponse(content={"status": "ok"})

def _process(body: str, sig: str):
    try:
        handler.handle(body, sig)
    except InvalidSignatureError:
        logger.warning("Invalid LINE signature — ตรวจสอบ CHANNEL_SECRET")
    except Exception as e:
        logger.exception(f"Webhook processing error: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
