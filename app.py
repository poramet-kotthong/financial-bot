"""
📊 Retirement Planning Bot — LINE Bot วางแผนเกษียณ
FastAPI + LINE SDK v3 + Flex Messages  |  v5.0
อ้างอิงผลิตภัณฑ์ลงทุน: กองทุนกรุงศรี (Krungsri Asset Management)
"""

import os, re, logging
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
logger = logging.getLogger("retirement-planning-bot")

CHANNEL_SECRET       = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
configuration        = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler              = WebhookHandler(CHANNEL_SECRET)

sessions: dict[str, dict] = {}
INFLATION = 0.03   # อัตราเงินเฟ้อ 3%/ปี (ค่าเฉลี่ยประวัติศาสตร์ไทย)

# ── Validation Limits ──────────────────────────────────────────────────────────
LIMITS = {
    "current_age":        (15,  80,          "อายุควรอยู่ระหว่าง 15–80 ปี"),
    "retire_age":         (30,  90,          "อายุเกษียณควรอยู่ระหว่าง 30–90 ปี"),
    "life_expectancy":    (50,  120,         "อายุขัยควรอยู่ระหว่าง 50–120 ปี"),
    "fixed_expense":      (0,   10_000_000,  "ค่าใช้จ่ายคงที่ไม่ควรเกิน 10,000,000 บาท/เดือน"),
    "variable_expense":   (0,   10_000_000,  "ค่าใช้จ่ายแปรผันไม่ควรเกิน 10,000,000 บาท/เดือน"),
    "monthly_income":     (0,   50_000_000,  "รายรับไม่ควรเกิน 50,000,000 บาท/เดือน"),
    "risk_level":         (1,   3,           "กรุณาพิมพ์ระดับความเสี่ยง 1, 2 หรือ 3 เท่านั้น"),
    "current_investment": (0,   1_000_000_000, "เงินออมสะสมไม่ควรเกิน 1,000,000,000 บาท"),
    "monthly_dca":        (0,   10_000_000,  "DCA ต่อเดือนไม่ควรเกิน 10,000,000 บาท"),
}

# ── ผลิตภัณฑ์อ้างอิง (กองทุนกรุงศรี) ─────────────────────────────────────────
PRODUCTS = {
    "saving":    {"name": "เงินฝากออมทรัพย์",        "short": "ออมทรัพย์",     "rate": 1.0,  "tag": "ความเสี่ยงต่ำ",       "color": "#4CAF50"},
    "fixed_1y":  {"name": "เงินฝากประจำ 12 เดือน",   "short": "ฝากประจำ 1ปี",  "rate": 2.0,  "tag": "ความเสี่ยงต่ำ",       "color": "#66BB6A"},
    "kf_money":  {"name": "KF-MONEYA (ตลาดเงิน)",    "short": "KF-MONEYA",     "rate": 3.0,  "tag": "ความเสี่ยงต่ำ-กลาง",  "color": "#29B6F6"},
    "kf_fixed":  {"name": "KF-FIXEDPLUS (ตราสารหนี้)","short": "KF-FIXEDPLUS",  "rate": 3.5,  "tag": "ความเสี่ยงปานกลาง",   "color": "#26C6DA"},
    "kf_rmf":    {"name": "KF-RMFA (RMF ตราสารหนี้)", "short": "KF-RMFA",       "rate": 4.0,  "tag": "ลดหย่อนภาษี",         "color": "#FF9800"},
    "kf_bal":    {"name": "KF-BALANCED (กองทุนผสม)",  "short": "KF-BALANCED",   "rate": 6.0,  "tag": "ความเสี่ยงปานกลาง",   "color": "#EF5350"},
    "kf_ssf":    {"name": "KF-SSFPLUS (SSF ผสม)",     "short": "KF-SSFPLUS",    "rate": 6.0,  "tag": "ลดหย่อนภาษี SSF",    "color": "#FFA726"},
    "kf_star":   {"name": "KF-STAR (ผสมเน้นหุ้น)",   "short": "KF-STAR",       "rate": 6.5,  "tag": "ความเสี่ยงสูง",       "color": "#EC407A"},
    "kf_growth": {"name": "KF-GROWTH (หุ้นไทย)",     "short": "KF-GROWTH",     "rate": 8.0,  "tag": "ความเสี่ยงสูง",       "color": "#F44336"},
    "kf_rmfg":   {"name": "KF-RMFG (RMF หุ้น)",      "short": "KF-RMFG",       "rate": 8.0,  "tag": "ลดหย่อนภาษี RMF",   "color": "#C62828"},
    "kf_gtech":  {"name": "KF-GTECH (หุ้นเทคฯโลก)",  "short": "KF-GTECH",      "rate": 9.0,  "tag": "ความเสี่ยงสูงมาก",   "color": "#7B1FA2"},
}

# ── Risk Config ────────────────────────────────────────────────────────────────
RISK_CONFIG = {
    1: {
        "name": "🟢 ต่ำ (Conservative)",
        "return": 0.04, "color": "#2E7D32", "bg": "#E8F5E9",
        "alloc": [
            ("เงินฝากออมทรัพย์/ประจำ", 40, "#4CAF50"),
            ("กองทุนตลาดเงิน/ตราสารหนี้", 50, "#29B6F6"),
            ("กองทุนผสม", 10, "#FF9800"),
        ],
        "products": ["saving", "fixed_1y", "kf_money", "kf_fixed"],
        "desc": "รักษาเงินต้น ผลตอบแทนมั่นคง เหมาะกับผู้ใกล้เกษียณ",
        "icon": "🛡️",
    },
    2: {
        "name": "🟡 ปานกลาง (Moderate)",
        "return": 0.06, "color": "#E65100", "bg": "#FFF8E1",
        "alloc": [
            ("เงินฝากประจำ", 20, "#66BB6A"),
            ("กองทุนตราสารหนี้", 30, "#29B6F6"),
            ("กองทุนผสม/หุ้น", 50, "#FF9800"),
        ],
        "products": ["fixed_1y", "kf_fixed", "kf_bal", "kf_star", "kf_ssf", "kf_rmf"],
        "desc": "สมดุลการเติบโตและความมั่นคง เหมาะกับนักลงทุนทั่วไป",
        "icon": "⚖️",
    },
    3: {
        "name": "🔴 สูง (Aggressive)",
        "return": 0.08, "color": "#B71C1C", "bg": "#FFEBEE",
        "alloc": [
            ("เงินฝากประจำ", 5, "#66BB6A"),
            ("กองทุนตราสารหนี้", 15, "#29B6F6"),
            ("กองทุนผสม", 20, "#FF9800"),
            ("กองทุนหุ้น", 60, "#F44336"),
        ],
        "products": ["kf_bal", "kf_growth", "kf_gtech", "kf_rmfg"],
        "desc": "เน้นผลตอบแทนระยะยาว ยอมรับความผันผวนสูงได้",
        "icon": "🚀",
    },
}

QUESTIONS = [
    {"key": "current_age",        "step_label": "1/8", "emoji": "🎂",
     "label": "อายุปัจจุบันของคุณ",
     "hint": "ระบุเป็นจำนวนปีเต็ม (15–80 ปี)",
     "example": "เช่น พิมพ์: 30", "unit": "ปี"},
    {"key": "retire_age",         "step_label": "2/8", "emoji": "🏖️",
     "label": "อายุที่ต้องการเกษียณ",
     "hint": "ต้องมากกว่าอายุปัจจุบัน และห่างกันอย่างน้อย 5 ปี (30–90 ปี)",
     "example": "เช่น พิมพ์: 55", "unit": "ปี"},
    {"key": "life_expectancy",    "step_label": "3/8", "emoji": "⏳",
     "label": "อายุขัยที่วางแผน",
     "hint": "คนไทยเฉลี่ย 80 ปี  |  WHO แนะนำ 85 ปี (50–120 ปี)",
     "example": "เช่น พิมพ์: 85", "unit": "ปี"},
    {"key": "fixed_expense",      "step_label": "4/8", "emoji": "🏠",
     "label": "ค่าใช้จ่ายคงที่ต่อเดือน",
     "hint": "ผ่อนบ้าน/รถ  เบี้ยประกัน  ค่างวดต่างๆ\n(ไม่มีพิมพ์ 0)",
     "example": "เช่น พิมพ์: 15000", "unit": "บาท/เดือน"},
    {"key": "variable_expense",   "step_label": "5/8", "emoji": "🛍️",
     "label": "ค่าใช้จ่ายแปรผันต่อเดือน",
     "hint": "อาหาร  ค่าเดินทาง  ช้อปปิ้ง  สาธารณูปโภค",
     "example": "เช่น พิมพ์: 12000", "unit": "บาท/เดือน"},
    {"key": "monthly_income",     "step_label": "6/8", "emoji": "💰",
     "label": "รายรับทั้งหมดต่อเดือน",
     "hint": "รวมเงินเดือน โบนัส (เฉลี่ย) และรายได้เสริมทุกทาง",
     "example": "เช่น พิมพ์: 50000", "unit": "บาท/เดือน"},
    {"key": "risk_level",         "step_label": "7/8", "emoji": "⚖️",
     "label": "ระดับความเสี่ยงที่รับได้",
     "hint": "เลือก 1, 2 หรือ 3 ตามระดับความเสี่ยงที่รับได้",
     "example": "พิมพ์: 1, 2 หรือ 3", "unit": ""},
    {"key": "current_investment", "step_label": "8.1/8", "emoji": "🏦",
     "label": "เงินออม/ลงทุนที่มีอยู่แล้ว",
     "hint": "รวมเงินฝาก  กองทุน  หุ้น  ทุกประเภท\n(ยังไม่มีพิมพ์ 0)",
     "example": "เช่น พิมพ์: 200000", "unit": "บาท"},
    {"key": "monthly_dca",        "step_label": "8.2/8", "emoji": "📅",
     "label": "เงินที่จะลงทุนเพิ่มทุกเดือน (DCA)",
     "hint": "จำนวนที่วางแผนลงทุนสม่ำเสมอต่อเดือน\n(ยังไม่มีแผนพิมพ์ 0)",
     "example": "เช่น พิมพ์: 5000", "unit": "บาท/เดือน"},
]
TOTAL_STEPS = len(QUESTIONS)

# ── Color Palette ──────────────────────────────────────────────────────────────
C_GREEN  = "#006B3F"
C_GOLD   = "#C9A84C"
C_LIGHT  = "#F1F8E9"
C_WHITE  = "#FFFFFF"
C_DARK   = "#1A1A2E"
C_GRAY   = "#757575"
C_POS    = "#2E7D32"
C_NEG    = "#C62828"
C_BLUE   = "#1565C0"
C_LBLUE  = "#E3F2FD"
C_WARN   = "#E65100"
C_PURPLE = "#4A148C"

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

def row_item(label, value, val_color=None, bold=False, lf=5, vf=4):
    return {
        "type": "box", "layout": "horizontal", "margin": "sm",
        "contents": [
            txt(label, color=C_GRAY, flex=lf),
            txt(value, color=val_color or C_DARK,
                weight="bold" if bold else "regular",
                flex=vf, align="end"),
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

def prog_bar(ratio, w=10, color=C_GREEN):
    filled = max(0, min(w, round(ratio * w)))
    return {
        "type": "box", "layout": "horizontal", "spacing": "xs",
        "contents": [
            {
                "type": "box", "layout": "vertical", "flex": 1,
                "height": "6px", "cornerRadius": "3px",
                "backgroundColor": color if i < filled else "#E0E0E0",
                "contents": [],
            }
            for i in range(w)
        ],
    }

def chip(contents, bg=C_LIGHT, radius="10px"):
    return {
        "type": "box", "layout": "vertical",
        "backgroundColor": bg, "cornerRadius": radius,
        "paddingAll": "12px", "margin": "md",
        "contents": contents,
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
                    "type": "box", "layout": "horizontal", "spacing": "md",
                    "contents": [
                        txt("📊", size="xxl", flex=0, color=C_WHITE),
                        {
                            "type": "box", "layout": "vertical", "flex": 1,
                            "contents": [
                                txt("Retirement Planning Bot", size="xl",
                                    color=C_WHITE, weight="bold"),
                                txt("ผู้ช่วยวางแผนเกษียณอัจฉริยะ",
                                    size="xs", color=C_GOLD, margin="xs"),
                            ],
                        },
                    ],
                },
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "20px",
            "spacing": "md",
            "contents": [
                txt("ระบบจะช่วยคุณ", size="sm", weight="bold", color=C_DARK),
                {
                    "type": "box", "layout": "vertical", "spacing": "sm",
                    "contents": [
                        _check_row("คำนวณเงินเป้าหมายเกษียณที่แม่นยำ"),
                        _check_row("วางแผนออมและลงทุน DCA ระยะยาว"),
                        _check_row("วิเคราะห์ด้วย 4% Rule & PV Annuity"),
                        _check_row("แนะนำพอร์ตลงทุนตามระดับความเสี่ยง"),
                        _check_row("อ้างอิงผลิตภัณฑ์กองทุนกรุงศรี"),
                    ],
                },
                divider("lg"),
                chip([
                    {
                        "type": "box", "layout": "horizontal",
                        "contents": [
                            _stat_box("8", "คำถาม"),
                            {"type": "separator", "color": "#DDDDDD"},
                            _stat_box("4", "รายงาน"),
                            {"type": "separator", "color": "#DDDDDD"},
                            _stat_box("~2", "นาที"),
                        ],
                    },
                ]),
            ],
        },
        "footer": {
            "type": "box", "layout": "vertical", "paddingAll": "16px",
            "backgroundColor": C_LIGHT, "spacing": "sm",
            "contents": [
                {
                    "type": "button", "style": "primary", "color": C_GREEN,
                    "height": "sm",
                    "action": {"type": "message", "label": "🚀 เริ่มวางแผนเลย!", "text": "เริ่ม"},
                },
                txt("พิมพ์ 'เริ่มใหม่' เพื่อรีเซ็ตได้ตลอดเวลา",
                    size="xxs", color=C_GRAY, align="center", margin="sm"),
            ],
        },
    }

def _check_row(text):
    return {
        "type": "box", "layout": "horizontal", "spacing": "sm",
        "contents": [
            txt("✅", size="xs", flex=0),
            txt(text, size="sm", color=C_GRAY, flex=1),
        ],
    }

def _stat_box(num, label):
    return {
        "type": "box", "layout": "vertical", "flex": 1, "alignItems": "center",
        "contents": [
            txt(num, size="xl", color=C_GREEN, weight="bold", align="center"),
            txt(label, size="xxs", color=C_GRAY, align="center"),
        ],
    }


def build_question_flex(step_idx):
    q     = QUESTIONS[step_idx]
    ratio = step_idx / TOTAL_STEPS
    pct   = int(ratio * 100)

    body_contents = [
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
    ]

    # ── ข้อ 7: Risk Level — แสดง card พร้อมผลิตภัณฑ์อ้างอิง ─────────────────
    if q["key"] == "risk_level":
        for lvl, cfg in RISK_CONFIG.items():
            # ดึงชื่อผลิตภัณฑ์ 2 ตัวแรกมาแสดง
            prod_names = [PRODUCTS[p]["short"] for p in cfg["products"][:2]]
            prod_text  = "  •  ".join(prod_names) + ("  ..." if len(cfg["products"]) > 2 else "")
            body_contents.append({
                "type": "box", "layout": "vertical",
                "backgroundColor": cfg["bg"],
                "cornerRadius": "10px", "paddingAll": "12px", "margin": "sm",
                "contents": [
                    # หัว: หมายเลข + ชื่อระดับ + ผลตอบแทน
                    {
                        "type": "box", "layout": "horizontal",
                        "contents": [
                            {
                                "type": "box", "layout": "vertical", "flex": 0,
                                "width": "32px", "height": "32px",
                                "backgroundColor": cfg["color"],
                                "cornerRadius": "8px", "alignItems": "center",
                                "justifyContent": "center",
                                "contents": [
                                    txt(str(lvl), size="lg", color=C_WHITE,
                                        weight="bold", align="center"),
                                ],
                            },
                            {
                                "type": "box", "layout": "vertical", "flex": 1,
                                "margin": "md",
                                "contents": [
                                    txt(cfg["name"], size="sm",
                                        color=cfg["color"], weight="bold"),
                                    txt(f'ผลตอบแทนคาด ~{cfg["return"]*100:.0f}%/ปี',
                                        size="xs", color=C_GRAY),
                                ],
                            },
                        ],
                    },
                    # คำอธิบาย
                    txt(cfg["desc"], size="xs", color=C_GRAY, margin="sm"),
                    # สัดส่วนพอร์ต (mini bar)
                    {
                        "type": "box", "layout": "horizontal",
                        "margin": "sm", "cornerRadius": "4px",
                        "contents": [
                            {
                                "type": "box", "layout": "vertical",
                                "flex": pct_val, "height": "6px",
                                "backgroundColor": bar_color,
                                "contents": [],
                            }
                            for _, pct_val, bar_color in cfg["alloc"]
                        ],
                    },
                    # ผลิตภัณฑ์อ้างอิง
                    {
                        "type": "box", "layout": "horizontal",
                        "margin": "xs", "spacing": "xs",
                        "contents": [
                            txt("📦", size="xxs", flex=0),
                            txt(f"อ้างอิง: {prod_text}", size="xxs",
                                color=C_GRAY, flex=1),
                        ],
                    },
                ],
            })
        body_contents.append(
            chip([txt("✏️  " + q["example"], size="sm", color=C_GREEN)])
        )

    else:
        # ── คำถามปกติ ──────────────────────────────────────────────────────────
        body_contents.append(
            chip([txt("✏️  " + q["example"], size="sm", color=C_GREEN)])
        )
        if q.get("unit"):
            body_contents.append({
                "type": "box", "layout": "horizontal", "margin": "xs",
                "contents": [
                    txt("หน่วย:", size="xs", color=C_GRAY, flex=0),
                    txt(q["unit"], size="xs", color=C_GREEN,
                        weight="bold", flex=1, margin="sm"),
                ],
            })

    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": C_GREEN, "paddingAll": "15px",
            "contents": [
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        txt(f"ขั้นตอนที่ {q['step_label']}",
                            size="xs", color=C_GOLD, flex=1),
                        txt(f"{pct}%", size="xs", color=C_WHITE, align="end"),
                    ],
                },
                {**prog_bar(ratio), "margin": "sm"},
            ],
        },
        "body": {
            "type": "box", "layout": "vertical",
            "paddingAll": "18px",
            "contents": body_contents,
        },
    }


def build_profile_flex(d, n_accum, n_retire, total_exp, net_flow, save_pct,
                       exp_retire_m, exp_retire_y,
                       withdrawal_m, withdrawal_y, rcfg, r):
    fmt = lambda n, dec=0: f"{n:,.{dec}f}"
    s   = min(max(save_pct, 0), 100)

    if save_pct >= 30:    sc, ss = C_POS,  "✅ ยอดเยี่ยม!"
    elif save_pct >= 20:  sc, ss = C_POS,  "✅ ดีมาก"
    elif save_pct >= 10:  sc, ss = C_WARN, "⚠️ พอใช้"
    elif save_pct >= 0:   sc, ss = C_NEG,  "❌ ควรเพิ่ม"
    else:                 sc, ss = C_NEG,  "❌ รายจ่ายเกินรายรับ!"

    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": C_GREEN, "paddingAll": "18px",
            "contents": [
                txt("📋 รายงานแผนเกษียณ  1/4", size="lg",
                    color=C_WHITE, weight="bold"),
                txt("ข้อมูลส่วนตัว & สถานะการเงิน",
                    size="xs", color=C_GOLD, margin="xs"),
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "18px",
            "contents": [

                # ── ส่วน 1: ข้อมูลส่วนบุคคล ──────────────────────────
                sec_header("👤", "ข้อมูลส่วนบุคคล & เป้าหมาย"),
                divider(),
                row_item("อายุปัจจุบัน",     f"{d['current_age']} ปี"),
                row_item("เกษียณที่อายุ",
                         f"{d['retire_age']} ปี  ({n_accum} ปีข้างหน้า)",
                         C_GREEN, True),
                row_item("วางแผนถึงอายุ",
                         f"{d['life_expectancy']} ปี  (หลังเกษียณ {n_retire} ปี)"),
                row_item("ระดับความเสี่ยง",   rcfg["name"], rcfg["color"], True),
                row_item("ผลตอบแทนคาดการณ์", f"{r*100:.0f}%/ปี", C_GREEN),
                row_item("อัตราเงินเฟ้อ",     "3%/ปี (สมมติฐาน)"),

                # ── ส่วน 2: สถานะการเงิน ─────────────────────────────
                sec_header("💼", "สถานะการเงินปัจจุบัน"),
                divider(),
                row_item("รายรับต่อเดือน",          f"฿{fmt(d['monthly_income'])}", C_POS, True),
                row_item("ค่าใช้จ่ายคงที่/เดือน",   f"฿{fmt(d['fixed_expense'])}"),
                row_item("ค่าใช้จ่ายแปรผัน/เดือน",  f"฿{fmt(d['variable_expense'])}"),
                row_item("รวมค่าใช้จ่าย/เดือน",     f"฿{fmt(total_exp)}"),
                row_item("เงินเหลือสุทธิ/เดือน",
                         f"฿{fmt(net_flow)}",
                         C_POS if net_flow >= 0 else C_NEG, True),
                # อัตราการออม + progress bar
                {
                    "type": "box", "layout": "vertical", "margin": "sm",
                    "contents": [
                        {
                            "type": "box", "layout": "horizontal",
                            "contents": [
                                txt("อัตราการออม", size="sm",
                                    color=C_GRAY, flex=5),
                                txt(f"{fmt(save_pct,1)}%  {ss}",
                                    size="sm", color=sc,
                                    weight="bold", flex=4, align="end"),
                            ],
                        },
                        {**prog_bar(s / 100, color=sc), "margin": "xs"},
                    ],
                },
                row_item("เงินออมสะสมปัจจุบัน",  f"฿{fmt(d['current_investment'])}"),
                row_item("DCA วางแผน/เดือน",     f"฿{fmt(d['monthly_dca'])}", C_GREEN),

                # ── ส่วน 3: ค่าใช้จ่ายหลังเกษียณ ─────────────────────
                sec_header("📊", f"ค่าใช้จ่ายหลังเกษียณ (อายุ {d['retire_age']} ปี)"),
                divider(),
                txt("*ปรับตามเงินเฟ้อ 3%/ปี", size="xxs", color=C_GRAY),
                row_item("ค่าใช้จ่าย/เดือน", f"฿{fmt(exp_retire_m)}", C_NEG, True),
                row_item("ค่าใช้จ่าย/ปี",    f"฿{fmt(exp_retire_y)}"),
                chip([
                    txt("💧 ถอนได้ตามแผน 4% Rule",
                        size="xs", color=C_GRAY, weight="bold"),
                    {
                        "type": "box", "layout": "horizontal", "margin": "sm",
                        "contents": [
                            txt(f"฿{fmt(withdrawal_m)}/เดือน",
                                size="sm", color=C_POS, weight="bold", flex=1),
                            txt(f"฿{fmt(withdrawal_y)}/ปี",
                                size="sm", color=C_POS, flex=1, align="end"),
                        ],
                    },
                ]),
            ],
        },
    }


def build_goal_flex(age, retire, target, fund_4pct, fund_pv,
                    n_retire, r, projected, fv_exist, fv_dca_v, rows_data):
    """
    rows_data: list of (checkpoint_age, yrs_from_now, fv_total)
    """
    fmt = lambda n, d=0: f"{n:,.{d}f}"

    # ── สร้าง milestone rows ──────────────────────────────────────────────────
    milestone_items = []
    for cp_age, yrs, tot in rows_data:
        ratio   = min(tot / target, 1.0) if target > 0 else 0
        pct_val = int(ratio * 100)
        # จำนวนช่องที่เติมสี = สัดส่วนตามมูลค่าจริง (ไม่มีค่าขั้นต่ำ)
        filled  = round(ratio * 10)   # ใช้ 10 ช่อง

        is_done  = ratio >= 1.0
        age_color = C_POS if is_done else C_DARK
        bar_color = C_POS if is_done else C_GREEN

        milestone_items.append({
            "type": "box", "layout": "vertical", "margin": "md",
            "contents": [
                # แถวบน: อายุ + ยอดเงิน + %
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        # อายุ (ป้าย)
                        {
                            "type": "box", "layout": "vertical", "flex": 0,
                            "backgroundColor": bar_color + "22",
                            "cornerRadius": "6px",
                            "paddingStart": "8px", "paddingEnd": "8px",
                            "paddingTop": "3px", "paddingBottom": "3px",
                            "contents": [
                                txt(f"อายุ {cp_age} ปี", size="xxs",
                                    color=bar_color, weight="bold"),
                            ],
                        },
                        txt(f"฿{fmt(tot)}", size="xs",
                            color=age_color, weight="bold",
                            flex=1, align="end"),
                        txt(f"{pct_val}%", size="xs",
                            color=bar_color, weight="bold",
                            flex=0, margin="sm"),
                    ],
                },
                # Progress bar
                {
                    "type": "box", "layout": "horizontal",
                    "margin": "xs", "spacing": "xs",
                    "contents": [
                        {
                            "type": "box", "layout": "vertical", "flex": 1,
                            "height": "8px", "cornerRadius": "4px",
                            "backgroundColor": bar_color if i < filled else "#E0E0E0",
                            "contents": [],
                        }
                        for i in range(10)
                    ],
                },
                # ปีที่ออม
                txt(f"สะสมมาแล้ว {yrs} ปี",
                    size="xxs", color=C_GRAY, margin="xs"),
            ],
        })

    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": C_BLUE, "paddingAll": "18px",
            "contents": [
                txt("🎯 รายงานแผนเกษียณ  2/4", size="lg",
                    color=C_WHITE, weight="bold"),
                txt("เป้าหมายและการจำลองการเติบโตของเงินทุน",
                    size="xs", color="#BBDEFB", margin="xs"),
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "18px",
            "contents": [

                # ── เงินเป้าหมาย ──────────────────────────────────
                sec_header("🏆", "เงินเป้าหมายที่ต้องมี", C_BLUE),
                divider(),
                row_item("วิธี 4% Rule",               f"฿{fmt(fund_4pct)}"),
                row_item(f"วิธี PV Annuity ({n_retire} ปี)", f"฿{fmt(fund_pv)}"),
                txt("*ใช้ค่าที่สูงกว่าเพื่อความปลอดภัย",
                    size="xxs", color=C_GRAY, margin="xs"),
                chip([
                    {
                        "type": "box", "layout": "horizontal",
                        "contents": [
                            txt("🎯 เป้าหมายสุดท้าย", size="sm",
                                color=C_GREEN, weight="bold", flex=5),
                            txt(f"฿{fmt(target)}", size="md",
                                color=C_GREEN, weight="bold",
                                flex=4, align="end"),
                        ],
                    },
                ]),

                # ── Timeline การเติบโต ────────────────────────────
                sec_header("📈", f"จำลองการเติบโต ({r*100:.0f}%/ปี)", C_BLUE),
                txt(f"เริ่มต้น: อายุ {age} ปี  →  เกษียณ: อายุ {retire} ปี",
                    size="xs", color=C_GRAY, margin="xs"),
                divider(),
                *milestone_items,

                # ── สรุปยอด ณ วันเกษียณ ─────────────────────────
                divider("md"),
                chip([
                    txt("💰 คาดการณ์ ณ วันเกษียณ",
                        size="sm", color=C_BLUE, weight="bold"),
                    row_item("เงินก้อนเดิมเติบโต", f"฿{fmt(fv_exist)}"),
                    row_item("DCA สะสมทั้งหมด",    f"฿{fmt(fv_dca_v)}"),
                    divider(),
                    {
                        "type": "box", "layout": "horizontal", "margin": "sm",
                        "contents": [
                            txt("รวมทั้งหมด", size="md",
                                weight="bold", color=C_DARK, flex=1),
                            txt(f"฿{fmt(projected)}", size="md",
                                weight="bold", color=C_BLUE,
                                flex=1, align="end"),
                        ],
                    },
                ], bg=C_LBLUE),
            ],
        },
    }


def build_bigpicture_flex(projected, target, dca, n_accum, r):
    fmt       = lambda n, d=0: f"{n:,.{d}f}"
    gap       = projected - target
    success   = gap >= 0
    color     = C_POS if success else C_NEG
    hdr_bg    = "#1B5E20" if success else "#B71C1C"
    emoji     = "🎉" if success else "⚠️"
    status    = ("ยอดเยี่ยม! แผนของคุณบรรลุเป้าหมาย"
                 if success else
                 "เป้าหมายยังขาดอยู่ ลองปรับแผน")

    extra = []
    if not success:
        fv_unit = (12 * (((1 + r) ** n_accum - 1) / r)
                   if r > 0 else 12 * n_accum)
        need_dca = abs(gap) / fv_unit if fv_unit > 0 else 0
        extra = [
            row_item("DCA ที่ต้องเพิ่ม/เดือน",
                     f"฿{fmt(need_dca)}", C_NEG, True),
        ]

    tips_yes = [
        "✅ รักษาวินัย DCA ต่อเนื่องทุกเดือน",
        "✅ Rebalance พอร์ตปีละ 1 ครั้ง",
        "✅ เพิ่ม DCA ทุกครั้งที่รายได้ขึ้น",
        "✅ ทบทวนแผนทุก 1–2 ปี",
    ]
    tips_no = [
        "⚡ เพิ่ม DCA ตามยอดที่คำนวณด้านบน",
        "⚡ ลดค่าใช้จ่ายที่ไม่จำเป็น",
        "⚡ พิจารณาเพิ่มรายได้ทางเสริม",
        "⚡ ปรับระดับความเสี่ยงสูงขึ้น (ถ้าทำได้)",
        "⚡ ทบทวนแผนทุก 1–2 ปี",
    ]
    tips = tips_yes if success else tips_no

    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": hdr_bg, "paddingAll": "18px",
            "contents": [
                txt("🔭 รายงานแผนเกษียณ  3/4", size="lg",
                    color=C_WHITE, weight="bold"),
                txt("Big Picture & Action Plan",
                    size="xs", color="#FFD54F", margin="xs"),
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "18px",
            "contents": [

                # ── ผลลัพธ์หลัก ──────────────────────────────────
                chip([
                    txt(f"{emoji} {status}", size="md",
                        weight="bold", color=color, align="center"),
                    txt(f"{'+' if success else '-'}฿{fmt(abs(gap))}",
                        size="xxl", weight="bold", color=color,
                        align="center", margin="md"),
                    txt("ส่วนต่างจากเป้าหมาย",
                        size="xs", color=C_GRAY, align="center"),
                ], bg=color + "11"),

                # ── สรุปตัวเลข ────────────────────────────────────
                sec_header("📌", "สรุปตัวเลขสำคัญ", C_DARK),
                divider(),
                row_item("เงินที่จะมี ณ วันเกษียณ",
                         f"฿{fmt(projected)}", C_BLUE, True),
                row_item("เงินเป้าหมาย", f"฿{fmt(target)}"),
                *extra,

                # ── Action Plan ───────────────────────────────────
                sec_header("💡", "Action Plan", C_DARK),
                divider(),
                chip([
                    *[txt(t, size="sm", color=C_DARK, margin="xs")
                      for t in tips],
                ]),
            ],
        },
    }


def build_scenario_flex(rcfg):
    fmt = lambda n, d=0: f"{n:,.{d}f}"

    # Allocation bar
    alloc_bar = [
        {
            "type": "box", "layout": "vertical",
            "flex": pct_val, "backgroundColor": bar_color,
            "height": "14px", "contents": [],
        }
        for _, pct_val, bar_color in rcfg["alloc"]
    ]

    # Legend
    legend = [
        {
            "type": "box", "layout": "horizontal", "flex": 0,
            "spacing": "xs", "margin": "xs",
            "contents": [
                {"type": "box", "layout": "vertical",
                 "width": "10px", "height": "10px",
                 "backgroundColor": c, "cornerRadius": "2px", "contents": []},
                txt(f"{n} {p}%", size="xxs", color=C_GRAY, flex=0),
            ],
        }
        for n, p, c in rcfg["alloc"]
    ]

    # Product cards
    prod_cards = []
    for pid in rcfg["products"]:
        p = PRODUCTS.get(pid)
        if not p:
            continue
        rate_pct = int((p["rate"] / 9.0) * 10)
        prod_cards.append({
            "type": "box", "layout": "vertical", "margin": "sm",
            "backgroundColor": "#FAFAFA", "cornerRadius": "10px",
            "paddingAll": "10px",
            "contents": [
                # ชื่อ + rate
                {
                    "type": "box", "layout": "horizontal",
                    "contents": [
                        {
                            "type": "box", "layout": "vertical", "flex": 0,
                            "width": "4px", "backgroundColor": p["color"],
                            "cornerRadius": "2px", "contents": [],
                        },
                        {
                            "type": "box", "layout": "vertical",
                            "flex": 1, "margin": "sm",
                            "contents": [
                                txt(p["name"], size="xs",
                                    color=C_DARK, weight="bold"),
                                txt(p["tag"], size="xxs", color=C_GRAY),
                            ],
                        },
                        txt(f"{p['rate']}%", size="sm",
                            color=p["color"], weight="bold", flex=0),
                    ],
                },
                # Rate bar
                {
                    "type": "box", "layout": "horizontal",
                    "margin": "xs", "spacing": "xs",
                    "contents": [
                        {
                            "type": "box", "layout": "vertical", "flex": 1,
                            "backgroundColor": p["color"] if i < rate_pct else "#E0E0E0",
                            "height": "4px", "cornerRadius": "2px", "contents": [],
                        }
                        for i in range(10)
                    ],
                },
            ],
        })

    return {
        "type": "bubble", "size": "mega",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": rcfg["color"], "paddingAll": "18px",
            "contents": [
                txt("🔮 รายงานแผนเกษียณ  4/4", size="lg",
                    color=C_WHITE, weight="bold"),
                txt("แผนการลงทุนและผลิตภัณฑ์ที่แนะนำ",
                    size="xs", color=C_WHITE, margin="xs"),
            ],
        },
        "body": {
            "type": "box", "layout": "vertical", "paddingAll": "18px",
            "contents": [

                # ── ระดับความเสี่ยง ──────────────────────────────
                sec_header(rcfg["icon"],
                           f"ระดับความเสี่ยง: {rcfg['name']}",
                           rcfg["color"]),
                txt(rcfg["desc"], size="sm", color=C_GRAY, margin="sm"),
                divider(),

                # ── สัดส่วนพอร์ต ─────────────────────────────────
                sec_header("📊", "สัดส่วนพอร์ตที่แนะนำ", C_DARK),
                {
                    "type": "box", "layout": "horizontal",
                    "margin": "sm", "cornerRadius": "8px",
                    "contents": alloc_bar,
                },
                {
                    "type": "box", "layout": "horizontal",
                    "margin": "xs", "flexWrap": "wrap", "spacing": "sm",
                    "contents": legend,
                },
                divider(),

                # ── ผลิตภัณฑ์แนะนำ ──────────────────────────────
                sec_header("🏦", "ผลิตภัณฑ์อ้างอิง (กองทุนกรุงศรี)", C_DARK),
                *prod_cards,

                # ── หมายเหตุ ─────────────────────────────────────
                chip([
                    txt("⚠️ หมายเหตุ", size="xs", color=C_WARN, weight="bold"),
                    txt("ผลิตภัณฑ์ที่แสดงเป็นเพียงข้อมูลอ้างอิงเพื่อการศึกษา "
                        "ผลตอบแทนเป็นค่าประมาณการ ไม่ใช่การรับประกัน "
                        "ควรศึกษาข้อมูลและปรึกษาผู้เชี่ยวชาญก่อนลงทุน",
                        size="xxs", color=C_GRAY, margin="sm"),
                ], bg="#FFF8E1"),
            ],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Business Logic
# ═══════════════════════════════════════════════════════════════════════════════

def extract_number(text: str):
    cleaned = text.strip().replace(",", "").replace(" ", "")
    m = re.fullmatch(r'\d+(\.\d+)?', cleaned)
    return float(m.group()) if m else None


def validate(step: int, val: float, data: dict) -> str | None:
    key = QUESTIONS[step]["key"]

    if key in LIMITS:
        lo, hi, msg = LIMITS[key]
        if not (lo <= val <= hi):
            return f"❌ {msg}\n(คุณพิมพ์: {val:,.0f})"

    if key == "retire_age":
        if val <= data.get("current_age", 0):
            return (f"❌ อายุเกษียณ ({val:.0f} ปี) ต้องมากกว่า"
                    f"อายุปัจจุบัน ({data['current_age']} ปี)")
        if val - data.get("current_age", 0) < 5:
            return "⚠️ ควรมีระยะเวลาสะสมอย่างน้อย 5 ปีครับ"

    if key == "life_expectancy":
        if val <= data.get("retire_age", 0):
            return (f"❌ อายุขัย ({val:.0f} ปี) ต้องมากกว่า"
                    f"อายุเกษียณ ({data['retire_age']} ปี)")
        if val - data.get("retire_age", 0) < 5:
            return "⚠️ ควรวางแผนหลังเกษียณอย่างน้อย 5 ปีครับ"

    if key == "monthly_dca":
        net = (data.get("monthly_income", 0)
               - data.get("fixed_expense", 0)
               - data.get("variable_expense", 0))
        if net > 0 and val > net:
            return (f"⚠️ DCA ที่ตั้ง ({val:,.0f} บาท) มากกว่า"
                    f"เงินเหลือสุทธิ ({net:,.0f} บาท/เดือน)\n"
                    f"กรุณาพิมพ์ยอดที่ไม่เกิน {net:,.0f} บาท")

    return None


def calculate(data: dict) -> list:
    age    = data["current_age"]
    retire = data["retire_age"]
    life   = data["life_expectancy"]
    fixed  = data["fixed_expense"]
    var_ex = data["variable_expense"]
    income = data["monthly_income"]
    risk   = data["risk_level"]
    invest = data["current_investment"]
    dca    = data["monthly_dca"]

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

    # ── เงินเป้าหมาย ─────────────────────────────────────────────────────────
    fund_4pct = exp_retire_y / 0.04
    real_r    = (1 + r) / (1 + INFLATION) - 1
    fund_pv   = (
        exp_retire_y * n_retire if real_r == 0
        else exp_retire_y * ((1 - (1 + real_r) ** -n_retire) / real_r)
    )
    target = max(fund_4pct, fund_pv)

    # ── Future Value ──────────────────────────────────────────────────────────
    fv_exist = invest * ((1 + r) ** n_accum)
    fv_dca_v = (
        dca * 12 * (((1 + r) ** n_accum - 1) / r)
        if r > 0 else dca * 12 * n_accum
    )
    projected    = fv_exist + fv_dca_v
    withdrawal_y = projected * 0.04
    withdrawal_m = withdrawal_y / 12

    # ── Milestone rows ────────────────────────────────────────────────────────
    # สร้าง checkpoint ที่กระจายสม่ำเสมอตลอดระยะเวลาสะสม
    # rows_data: (checkpoint_age, years_elapsed, total_fv)
    rows_data = []
    num_mid = min(3, max(1, n_accum // 5))   # 1–3 จุดระหว่างทาง
    checkpoints = []
    for i in range(1, num_mid + 1):
        cp_age = age + round(n_accum * i / (num_mid + 1))
        checkpoints.append(cp_age)
    checkpoints.append(retire)   # จุดสุดท้าย = วันเกษียณเสมอ

    for cp_age in checkpoints:
        yrs = cp_age - age
        ve  = invest * ((1 + r) ** yrs)
        vd  = (dca * 12 * (((1 + r) ** yrs - 1) / r)
               if r > 0 else dca * 12 * yrs)
        rows_data.append((cp_age, yrs, ve + vd))

    flex1 = build_profile_flex(
        data, n_accum, n_retire, total_exp, net_flow, save_pct,
        exp_retire_m, exp_retire_y, withdrawal_m, withdrawal_y, rcfg, r)
    flex2 = build_goal_flex(
        age, retire, target, fund_4pct, fund_pv, n_retire, r,
        projected, fv_exist, fv_dca_v, rows_data)
    flex3 = build_bigpicture_flex(projected, target, dca, n_accum, r)
    flex4 = build_scenario_flex(rcfg)

    return [flex1, flex2, flex3, flex4]


# ═══════════════════════════════════════════════════════════════════════════════
#  LINE Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def make_flex(alt: str, flex_dict: dict) -> FlexMessage:
    return FlexMessage(alt_text=alt,
                       contents=FlexContainer.from_dict(flex_dict))

def reply_flex(api, token, alt, flex_dict):
    api.reply_message(ReplyMessageRequest(
        reply_token=token, messages=[make_flex(alt, flex_dict)]))

def reply_text(api, token, text):
    api.reply_message(ReplyMessageRequest(
        reply_token=token, messages=[TextMessage(text=text)]))

def push_flex_list(api, uid, flex_list, alts):
    msgs = [make_flex(alts[i], flex_list[i]) for i in range(len(flex_list))]
    for i in range(0, len(msgs), 5):
        api.push_message(PushMessageRequest(
            to=uid, messages=msgs[i:i+5]))


# ═══════════════════════════════════════════════════════════════════════════════
#  LINE Event Handler
# ═══════════════════════════════════════════════════════════════════════════════

RESET_CMDS = {"เริ่มใหม่","reset","ใหม่","สวัสดี","หวัดดี",
              "hi","hello","help","ช่วยด้วย","menu","เมนู"}
START_CMDS = {"เริ่ม","begin","คำนวณ","ลอง","start","เริ่มต้น","go"}

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    uid  = (event.source.user_id
            if isinstance(event.source, UserSource)
            else str(event.source))
    text = event.message.text.strip()
    cmd  = text.lower()

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        try:
            # ── Reset / Welcome ───────────────────────────────────
            if cmd in RESET_CMDS:
                sessions.pop(uid, None)
                reply_flex(api, event.reply_token,
                           "ยินดีต้อนรับสู่ Retirement Planning Bot",
                           build_welcome_flex())
                return

            # ── Start ─────────────────────────────────────────────
            if cmd in START_CMDS:
                sessions[uid] = {"step": 0, "data": {}}
                reply_flex(api, event.reply_token,
                           f"คำถามข้อ 1: {QUESTIONS[0]['label']}",
                           build_question_flex(0))
                return

            # ── No session ────────────────────────────────────────
            if uid not in sessions:
                reply_flex(api, event.reply_token,
                           "ยินดีต้อนรับสู่ Retirement Planning Bot",
                           build_welcome_flex())
                return

            sess = sessions[uid]
            step = sess["step"]
            data = sess["data"]

            # ── Parse number ──────────────────────────────────────
            val = extract_number(text)
            if val is None or val < 0:
                q = QUESTIONS[step]
                reply_text(api, event.reply_token,
                           f"⚠️ กรุณากรอกเป็นตัวเลขที่ถูกต้องนะครับ\n\n"
                           f"📌 {q['label']}\n"
                           f"💡 {q['hint']}\n\n"
                           f"{q['example']}")
                return

            # ── Validate ──────────────────────────────────────────
            err = validate(step, val, data)
            if err:
                reply_text(api, event.reply_token, err)
                return

            # ── Save answer ───────────────────────────────────────
            key      = QUESTIONS[step]["key"]
            int_keys = {"current_age","retire_age","life_expectancy","risk_level"}
            data[key] = int(val) if key in int_keys else float(val)
            step += 1
            sess["step"] = step

            # ── Next question ─────────────────────────────────────
            if step < TOTAL_STEPS:
                reply_flex(api, event.reply_token,
                           f"คำถามข้อ {QUESTIONS[step]['step_label']}: "
                           f"{QUESTIONS[step]['label']}",
                           build_question_flex(step))
                return

            # ── All done → calculate ──────────────────────────────
            del sessions[uid]
            reply_text(api, event.reply_token,
                       "⏳ กำลังวิเคราะห์และจัดทำรายงาน...\n"
                       "กรุณารอสักครู่นะครับ 📊")
            results = calculate(data)
            push_flex_list(api, uid, results, [
                "📋 รายงานแผนเกษียณ 1/4 — ข้อมูลส่วนตัว",
                "🎯 รายงานแผนเกษียณ 2/4 — เป้าหมายและการเติบโต",
                "🔭 รายงานแผนเกษียณ 3/4 — Big Picture & Action Plan",
                "🔮 รายงานแผนเกษียณ 4/4 — แผนการลงทุน",
            ])

        except Exception as e:
            logger.exception(f"Error uid={uid}: {e}")
            reply_text(api, event.reply_token,
                       "⚠️ เกิดข้อผิดพลาดบางอย่างครับ\n"
                       "กรุณาพิมพ์ 'เริ่มใหม่' เพื่อลองใหม่อีกครั้ง")


# ═══════════════════════════════════════════════════════════════════════════════
#  FastAPI
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Retirement Planning Bot", version="5.0")

@app.get("/")
def root():
    return {"status": "ok", "bot": "Retirement Planning Bot",
            "version": "5.0", "active_sessions": len(sessions)}

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
        logger.exception(f"Webhook error: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0",
                port=int(os.environ.get("PORT", 8000)))
