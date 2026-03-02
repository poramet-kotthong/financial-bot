import os
import re
import logging
import uvicorn
import matplotlib.pyplot as plt

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage, ImageMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent, UserSource
from linebot.v3.exceptions import InvalidSignatureError

# ── Logging ─────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("financial-bot")

# ── Config ──────────────────────────────────
CHANNEL_SECRET       = os.getenv("LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
DOMAIN_URL           = os.getenv("DOMAIN_URL", "https://your-domain.com")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler       = WebhookHandler(CHANNEL_SECRET)

# ── FastAPI ─────────────────────────────────
app = FastAPI(title="Retirement Bot")
os.makedirs("images", exist_ok=True)
app.mount("/images", StaticFiles(directory="images"), name="images")

sessions = {}

# ── Portfolio ───────────────────────────────
PORTFOLIO = {
    1: {"name":"ต่ำ","avg_return":0.04},
    2: {"name":"กลาง","avg_return":0.06},
    3: {"name":"สูง","avg_return":0.08},
}

# ── Questions ───────────────────────────────
QUESTIONS = [
    {"key":"current_age","q":"อายุปัจจุบัน"},
    {"key":"retire_age","q":"อายุที่อยากเกษียณ"},
    {"key":"retire_years","q":"จะใช้ชีวิตหลังเกษียณกี่ปี"},
    {"key":"monthly_income","q":"รายได้ต่อเดือน"},
    {"key":"fixed_expense","q":"ค่าใช้จ่ายคงที่"},
    {"key":"variable_expense","q":"ค่าใช้จ่ายผันแปร"},
    {"key":"current_investment","q":"เงินออมปัจจุบัน"},
    {"key":"inflation_rate","q":"เงินเฟ้อ (แนะนำ 3)"},
    {"key":"risk_level","q":"ความเสี่ยง 1=ต่ำ 2=กลาง 3=สูง"}
]

# ── Helpers ─────────────────────────────────
def extract_number(text):
    text = text.replace(",", "")
    match = re.search(r'\d+(\.\d+)?', text)
    return float(match.group()) if match else None

def reply(api, token, msg):
    api.reply_message(ReplyMessageRequest(
        reply_token=token,
        messages=[TextMessage(text=msg)]
    ))

def push(api, user, msg):
    api.push_message(PushMessageRequest(
        to=user,
        messages=[TextMessage(text=msg)]
    ))

def push_image(api, user, url):
    api.push_message(PushMessageRequest(
        to=user,
        messages=[ImageMessage(
            original_content_url=url,
            preview_image_url=url
        )]
    ))

# ── Graph ───────────────────────────────────
def generate_graph(data, r):
    age    = int(data["current_age"])
    retire = int(data["retire_age"])
    invest = float(data["current_investment"])
    infl   = float(data["inflation_rate"])/100
    exp    = float(data["fixed_expense"])+float(data["variable_expense"])

    years = retire - age

    wealth=[]
    need=[]
    ages=[]

    for y in range(years+1):
        w = invest*(1+r)**y
        n = exp*(1+infl)**y*12
        wealth.append(w)
        need.append(n)
        ages.append(age+y)

    plt.figure()
    plt.plot(ages, wealth)
    plt.plot(ages, need)
    plt.title("Retirement Growth")

    path=f"images/growth_{age}_{retire}.png"
    plt.savefig(path)
    plt.close()

    return path

# ── Calculate ───────────────────────────────
def calculate(data):

    age=int(data["current_age"])
    retire=int(data["retire_age"])
    income=float(data["monthly_income"])
    fixed=float(data["fixed_expense"])
    var=float(data["variable_expense"])
    invest=float(data["current_investment"])
    infl=float(data["inflation_rate"])/100
    risk=int(data["risk_level"])

    exp=fixed+var
    years=retire-age

    exp_future=exp*(1+infl)**years
    target=exp_future*12*int(data["retire_years"])

    r=PORTFOLIO[risk]["avg_return"]

    return target,r

# ── LINE Handler ────────────────────────────
@handler.add(MessageEvent, message=TextMessageContent)
def handle(event):

    user = event.source.user_id
    text = event.message.text.strip()

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)

        if text.lower()=="เริ่ม":
            sessions[user]={"step":0,"data":{}}
            reply(api,event.reply_token,QUESTIONS[0]["q"])
            return

        if user not in sessions:
            reply(api,event.reply_token,"พิมพ์ 'เริ่ม'")
            return

        session=sessions[user]
        step=session["step"]
        val=extract_number(text)

        if val is None:
            reply(api,event.reply_token,"กรอกตัวเลข")
            return

        session["data"][QUESTIONS[step]["key"]]=val
        session["step"]+=1

        if session["step"]<len(QUESTIONS):
            reply(api,event.reply_token,
                  QUESTIONS[session["step"]]["q"])
            return

        data=session["data"]
        del sessions[user]

        target,r=calculate(data)
        graph=generate_graph(data,r)

        reply(api,event.reply_token,
              f"เงินที่ต้องมี ≈ {target:,.0f} บาท")

        url=f"{DOMAIN_URL}/{graph}"
        push_image(api,user,url)

# ── Webhook ─────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    body=await request.body()
    signature=request.headers.get("X-Line-Signature","")
    background_tasks.add_task(handler.handle, body.decode(), signature)
    return {"status":"ok"}

@app.get("/")
def root():
    return {"status":"running"}

# ── Run ─────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
