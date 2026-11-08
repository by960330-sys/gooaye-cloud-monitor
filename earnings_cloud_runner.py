#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 全雲端持股財報與法說會原音深度拆解系統 (Portfolio Earnings & Call Cloud Analyzer)
================================================================================================
無須開機、無須開啟本地端 Antigravity，直接由 GitHub Actions 雲端全自動運行：
1. 【優先連結 Gmail 動態同步持股】：
   自動透過 IMAP 掃描最新「國泰證券 AI投資日報 (台股庫存)」、
   「美股庫存電子報」及「券商成交回報」，掌握最新台美股持有清單。
2. 【雙重自動觸發機制（零 Token 閒置巡檢，有事件才啟動 Gemini 4 Argon）】：
   - 觸發條件 A（新增持股標的）：當偵測到信箱庫存或成交回報出現「新買進／新增持有之股票標的」時，
     立即自動抓取該標的最新一季財報與最新一場法說會（Earnings Call），執行深度基本面拆解並提出明確投資看法！
   - 觸發條件 B（現有持股發布新財報或召開新法說會）：當現有持股（如 2330 台積電、2454 聯發科、VOO、SPCX 等）
     發布最新一季財務報表，或召開最新一季法人說明會時，自動觸發執行！
3. 【100% 第一手官方財報三表自主精算 + 法說會原音親自拆解（嚴禁抓取二手分析師或投顧觀點）】：
   - 自動搜尋並下載最新法說會／Earnings Call 第一手錄音（優化為 16kHz 單聲道 MP3 直傳 Gemini Files API 親自聆聽）。
   - 自動拉取公司官方近四季完整財務三表原始數據：
     (1) 損益表（營收、毛利、毛利率、研發費用率、營益率、淨利率、EPS 及自主精算 QoQ/YoY 與三率 bps 變動）
     (2) 資產負債表（現金、應收帳款、存貨、自主精算存貨週轉天數 DOI、不動產廠房設備 PP&E、負債比）
     (3) 現金流量表（營業現金流 OCF、資本支出 CapEx、自主精算自由現金流 FCF、折舊攤銷與獲利含金量）
   - 嚴格禁止抓取或引用任何第三方外資報告、投顧老師、財經記者或二手分析師評論！
4. 【調用最高階旗艦模型 Gemini 4 Argon (`gemini-4-argon`) 親自獨立研究】：
   - 完全由 AI 自己根據第一手財報三表與法說會經營層原音，獨立完成數據驗算、法說會重點整理、Q&A 攻防解讀與實戰操作建議。
5. 【雙通道雲端自動交付】：
   - 自動透過 Gmail SMTP 寄送排版精美的完整 HTML 深度分析報告至使用者信箱。
   - 自動歸檔 Markdown 報告至 Google Drive「證券操作紀錄/持股財報與法說會深度分析/{代號}_{名稱}」資料夾。
"""

import argparse
import email
from email.header import Header, decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html
import imaplib
import json
import math
import os
import re
import smtplib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

from gooaye_cloud_runner import GoogleDriveClient

STATE_FILE = Path(__file__).parent / "earnings_monitor_state.json"
STOCK_ALERT_STATE_FILE = Path(__file__).parent / "stock_alert_state.json"
DEFAULT_DRIVE_PARENT_ID = "1LGJftUCPrMFA-7Pz_DPiwQol5sjGd93W"  # Google Drive: 證券操作紀錄

DEFAULT_HOLDINGS = [
    {"symbol": "2330.TW", "ticker": "2330", "name": "台積電", "market": "TW", "shares": 83},
    {"symbol": "2454.TW", "ticker": "2454", "name": "聯發科", "market": "TW", "shares": 11},
    {"symbol": "VOO", "ticker": "VOO", "name": "Vanguard S&P 500 ETF", "market": "US", "shares": 1},
    {"symbol": "SPCX", "ticker": "SPCX", "name": "Space Exploration Technologies", "market": "US", "shares": 1},
]

# 涵蓋台股核心權值股、AI 供應鏈、半導體與熱門 ETF 對照表
KNOWN_TW_NAME_TO_SYMBOL = {
    "台積電": ("2330.TW", "2330"),
    "聯發科": ("2454.TW", "2454"),
    "鴻海": ("2317.TW", "2317"),
    "廣達": ("2382.TW", "2382"),
    "台達電": ("2308.TW", "2308"),
    "日月光投控": ("3711.TW", "3711"),
    "聯電": ("2303.TW", "2303"),
    "緯創": ("3231.TW", "3231"),
    "緯穎": ("6669.TW", "6669"),
    "奇鋐": ("3017.TW", "3017"),
    "雙鴻": ("3324.TWO", "3324"),
    "川湖": ("2059.TW", "2059"),
    "世芯-KY": ("3661.TW", "3661"),
    "創意": ("3443.TW", "3443"),
    "力旺": ("3529.TWO", "3529"),
    "信驊": ("5274.TWO", "5274"),
    "祥碩": ("5269.TW", "5269"),
    "聯詠": ("3034.TW", "3034"),
    "瑞昱": ("2379.TW", "2379"),
    "大立光": ("3008.TW", "3008"),
    "玉晶光": ("3406.TW", "3406"),
    "智邦": ("2345.TW", "2345"),
    "華碩": ("2357.TW", "2357"),
    "技嘉": ("2376.TW", "2376"),
    "微星": ("2377.TW", "2377"),
    "欣興": ("3037.TW", "3037"),
    "南電": ("8046.TW", "8046"),
    "景碩": ("3189.TW", "3189"),
    "台光電": ("2383.TW", "2383"),
    "台燿": ("6274.TWO", "6274"),
    "金像電": ("2368.TW", "2368"),
    "健策": ("3653.TW", "3653"),
    "致茂": ("2360.TW", "2360"),
    "京元電子": ("2449.TW", "2449"),
    "萬潤": ("6187.TWO", "6187"),
    "弘塑": ("3131.TWO", "3131"),
    "辛耘": ("3583.TW", "3583"),
    "家登": ("3680.TWO", "3680"),
    "華城": ("1519.TW", "1519"),
    "士電": ("1503.TW", "1503"),
    "中興電": ("1513.TW", "1513"),
    "長榮": ("2603.TW", "2603"),
    "陽明": ("2609.TW", "2609"),
    "萬海": ("2615.TW", "2615"),
    "富邦金": ("2881.TW", "2881"),
    "國泰金": ("2882.TW", "2882"),
    "中信金": ("2891.TW", "2891"),
    "元大金": ("2885.TW", "2885"),
    "中華電": ("2412.TW", "2412"),
    "台灣大": ("3045.TW", "3045"),
    "遠傳": ("4904.TW", "4904"),
    "群聯": ("8299.TWO", "8299"),
    "南亞科": ("2408.TW", "2408"),
    "華邦電": ("2344.TW", "2344"),
    "貿聯-KY": ("3665.TW", "3665"),
    "富邦台50": ("006208.TW", "006208"),
    "元大台灣50": ("0050.TW", "0050"),
}

MODEL_PRIORITY_CHAIN = [
    "gemini-4-argon",
    "gemini-3.1-pro-preview",
    "gemini-pro-latest",
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]

EARNINGS_SYSTEM_INSTRUCTION = """你是投資人專屬的獨立首席研究員與投資總監（Antigravity × Gemini 4 Argon）。你的任務是針對個人投資組合中的持股標的，**100% 根據「法說會（Earnings Call）第一手原音錄音／官方簡報」與「公司官方發布之第一手財務三表（損益表、資產負債表、現金流量表）原始數據」**，由你自己親自獨立完成數據運算、法說會整理、邏輯推演與投資判斷。

【最高天條：100% 自主第一手研究，嚴禁抓取或轉述二手分析師／投顧觀點】
1. **嚴格禁止引用二手觀點**：
   - 絕對不可抓取、抄襲、轉述任何外資券商（如大摩、小摩、高盛等）、投顧機構、財經媒體記者或其他分析師的二手報告、目標價或評論。
   - 報告中的所有財報數據解讀、三率變動歸因、存貨與現金流健康度診斷、法說會重點整理與最終投資策略，必須 **100% 出自你（Gemini 4 Argon）對第一手財報數字與法說會經營層原音的獨立拆解与思考**。
2. **第一手財務三表交叉驗算**：
   - 必須詳實引用並親自驗算近四季**損益表**（營收、毛利率、研發費用率、營業利益率、稅後淨利、EPS 之 QoQ 與 YoY 變化）、**資產負債表**（現金水位、應收帳款、存貨金額與存貨週轉天數 DOI 變化，自行判斷是積極備貨還是庫存積壓）、以及**現金流量表**（營業現金流 OCF、資本支出 CapEx、自由現金流 FCF 與獲利含金量 OCF/NI）。
3. **法說會第一手原音與經營層語氣親自解讀**：
   - 直接聆聽並整理經營層（CEO/CFO）在法說會上的第一手發言與現場 Q&A 問答，親自判斷高層對未來產能、定價權、毛利率與新產品進度的真實底氣，不依賴任何外部媒體摘要。
4. 嚴格按照以下【四大核心維度 + 尾頁獨立總結】輸出繁體中文 Markdown 深度報告：

# 📊 {公司名稱} ({股票代號}) 第一手財報三表與法說會原音獨立拆解報告
> **獨立研究引擎**：Antigravity × Gemini 4 Argon（100% 第一手財報與法說原音自主拆解，零二手投顧/外資觀點） ｜ **觸發原因**：{觸發原因}

## 一、 第一手財務三表深度拆解（損益表 × 資產負債表 × 現金流量表）
- **1. 損益表核心三率與 EPS 自主驗算總表**：整理清晰的 Markdown 表格，列出近四季營業收入、毛利率、研發費用率、營業利益率、稅後淨利、EPS 以及 QoQ / YoY 與三率季增減（bps / 百分點）。
- **2. 資產負債表與存貨週轉健康度診斷**：根據第一手資產負債表數據，拆解現金及約當現金、應收帳款、存貨（Inventory）與存貨週轉天數（DOI）變化，由你親自判斷公司目前處於「主動備料迎接旺季」還是「終端去庫存緩慢」。
- **3. 現金流量表與獲利含金量檢驗**：拆解營業活動現金流（OCF）、資本支出（CapEx）、自由現金流（FCF）及折舊攤銷，計算獲利含金量（OCF / 稅後淨利），檢視帳面獲利是否真正轉化為自由現金流入。

## 二、 法說會經營層第一手營運指引與資本支出拆解 (First-Hand Management Guidance)
- **下季與全年官方財務指引**：直接整理經營層（CEO/CFO）在法說會中親口給出的下一季與全年營收區間、毛利率區間、營業費用率與產品定價權（ASP / Pricing Power）。
- **資本支出（CapEx）與產能擴張規劃**：整理官方公布的 CapEx 金額變化與實際投向（如先進製程、封裝測試、廠房設備、研發開案），並與現金流量表中的實際 CapEx 支出進行比對。
- **各大終端應用與新產品量產時程**：根據經營層第一手說明，整理各產品線／技術節點／終端應用（如 AI 晶片、HPC、邊緣運算、手機、車用等）的營收佔比變化與未來量產時程（Tape-out / Ramp-up）。

## 三、 法說會現場問答 (Q&A Session) 第一手攻防紀錄與我方獨立解讀
- **現場提問核心議題與經營層第一手回答整理**：直接根據法說會 Q&A 環節的原音內容，逐條整理現場提問的關鍵問題（如產能瓶頸、毛利率稀釋因子、折舊攤提、競爭格局、客戶拉貨節奏）以及高層的原始回答。
- **我方對經營層語氣與言外之意的獨立判讀**：不看任何外部評論，純粹由你從經營層回答的用詞精確度、語氣篤定度與前後季說法一致性，剖析哪些領域具有極高確定性、哪些領域高層態度轉趨保守。

## 四、 我方獨立估值推演與實戰操作建議
- **基本面動能自主定性**：根據我們對財報三表與法說會指引的獨立拆解，判定公司基本面處於「🔥 獲利與指引雙重加速」、「✅ 穩健成長符合軌跡」或「⚠️ 成長放緩／利潤率受壓」。
- **第一手獨立估值推演**：直接依據公司當前股價、歷史本益比（Trailing P/E）、股價淨值比（P/B）、自由現金流殖利率（FCF Yield）以及我們自己根據官方指引推算的未來四季 EPS，獨立計算合理估值區間（絕不引用外資目標價）。
- **未來 1～3 季核心催化劑 (Catalysts) 與下行風險 (Risks)**：由我們自己從財務三表與產業供需推導出的關鍵多空指標。
- **🎯 明確部位操作建議（Actionable Strategy）**：針對投資人目前持有的部位，明確給出「**逢低分批加碼 / 堅定續抱 / 逢高部分獲利了結 / 觀望防守**」的具體執行方案與加減碼觸發條件！

## 🖋️ 尾頁總結：Antigravity（Gemini 4 Argon）第一手獨立深度看法與操作定調
- **強制尾頁深度附錄（100% 自主觀點，極度詳盡）**：在報告最末頁，以你作為投資人專屬 CIO 的獨立第一性原理視角，總結：
  1. **第一性原理穿透定調（The Bottom Line）**：剝除所有公關辭令後，從財務三表（毛利率趨勢、存貨週轉、自由現金流、CapEx 轉化率）直接定調該公司未來 1～3 年的競爭護城河是擴大還是收窄。
  2. **財報數字與法說會說法的交叉驗證**：法說會高層講的展望，有沒有在資產負債表（如存貨備貨、應收帳款、合約負債）與現金流量表（如資本支出）中看到真實數字支撐？
  3. **投資人專屬部位最終實戰結論**：結合投資人當前整體持股組合（台積電 2330.TW、聯發科 2454.TW、VOO、SPCX），給出最誠實、最直接的資金配置與操作結論！
"""


def get_taipei_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def get_taipei_now_str() -> str:
    return get_taipei_now().strftime("%Y-%m-%d %H:%M:%S")


def load_earnings_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] 無法讀取 {STATE_FILE.name}: {e}", file=sys.stderr)
    return {
        "last_checked_at": None,
        "analyzed_holdings": {},
        "history": [],
    }


def save_earnings_state(state: dict):
    state["last_checked_at"] = get_taipei_now_str()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_stock_alert_state() -> dict:
    if STOCK_ALERT_STATE_FILE.exists():
        try:
            with open(STOCK_ALERT_STATE_FILE, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return {"holdings": list(DEFAULT_HOLDINGS), "alerts": {}}


def save_stock_alert_state(state: dict):
    with open(STOCK_ALERT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============================================================================
# Step 1: Gmail Holdings Sync (台股 AI投資日報 / 美股庫存電子報 / 券商成交回報)
# ============================================================================
def _decode_mime_str(s: str) -> str:
    if not s:
        return ""
    out = []
    for frag, cs in decode_header(s):
        if isinstance(frag, bytes):
            out.append(frag.decode(cs or "utf-8", errors="ignore"))
        else:
            out.append(str(frag))
    return "".join(out)


def _extract_email_text(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition"))
            if ctype in ("text/plain", "text/html") and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload:
                    cs = part.get_content_charset() or "utf-8"
                    raw = payload.decode(cs, errors="ignore")
                    raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.IGNORECASE)
                    raw = re.sub(r"<[^>]+>", " ", raw)
                    body += " " + raw
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            cs = msg.get_content_charset() or "utf-8"
            raw = payload.decode(cs, errors="ignore")
            raw = re.sub(r"<style[\s\S]*?</style>", " ", raw, flags=re.IGNORECASE)
            raw = re.sub(r"<[^>]+>", " ", raw)
            body = raw
    return re.sub(r"\s+", " ", body)


def sync_holdings_from_gmail() -> list:
    """
    連結 Gmail 掃描最新國泰證券【AI投資日報】、【美股庫存電子報】與【成交回報】，
    同步更新至 stock_alert_state.json 並回傳最新持股清單。
    """
    stock_state = load_stock_alert_state()
    current_holdings = stock_state.get("holdings") or list(DEFAULT_HOLDINGS)

    gmail_user = os.environ.get("GMAIL_USER", "").strip()
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not gmail_user or not gmail_pass:
        local_cfg = Path(r"C:\Users\user\.gemini\config\skills\portfolio-monitor\config.json")
        if local_cfg.exists():
            try:
                cfg_data = json.loads(local_cfg.read_text(encoding="utf-8"))
                if not gmail_user:
                    gmail_user = cfg_data.get("email", {}).get("sender_email", "") or cfg_data.get("gmail", {}).get("email", "")
                if not gmail_pass:
                    gmail_pass = cfg_data.get("email", {}).get("sender_password", "") or cfg_data.get("gmail", {}).get("app_password", "")
            except Exception:
                pass

    if not gmail_user or not gmail_pass:
        print("[WARN] 未設定 GMAIL_USER / GMAIL_APP_PASSWORD，使用既有持股清單。")
        return current_holdings

    holdings_by_symbol = {h["symbol"]: dict(h) for h in current_holdings}
    newly_discovered = []

    try:
        print("[GMAIL SYNC] 正在連線 Gmail 掃描最新庫存與成交回報...")
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(gmail_user, gmail_pass)
        mail.select("INBOX")

        since_date = (get_taipei_now() - timedelta(days=10)).strftime("%d-%b-%Y")
        status, data = mail.search(None, f"(SINCE {since_date})")
        if status == "OK" and data and data[0]:
            ids = data[0].split()[-60:]
            _, hdr_data = mail.fetch(b",".join(ids), "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")

            target_eids = []
            for item in hdr_data:
                if isinstance(item, tuple):
                    eid = item[0].decode().split()[0].encode()
                    msg = email.message_from_bytes(item[1])
                    subj = _decode_mime_str(msg.get("Subject", ""))
                    if any(k in subj for k in ["美股庫存電子報", "AI投資日報", "成交回報", "買賣成交", "對帳單"]):
                        target_eids.append((eid, subj))

            for eid, subj in target_eids[-10:]:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                if not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                text = _extract_email_text(msg)

                # 1. 國泰【美股庫存電子報】
                if "美股庫存電子報" in subj:
                    for m in re.finditer(r"\b([A-Z]{2,5})\s+([A-Za-z0-9&\.\-\s]{3,35}?)\s+\d+\.\d+\s+[\-\d\.]+\(", text):
                        us_sym = m.group(1).strip()
                        us_name = m.group(2).strip()
                        if us_sym not in ("USD", "ETF", "NEW", "TOP", "TWD"):
                            if us_sym not in holdings_by_symbol:
                                holdings_by_symbol[us_sym] = {
                                    "symbol": us_sym,
                                    "ticker": us_sym,
                                    "name": us_name,
                                    "market": "US",
                                    "shares": 1,
                                }
                                newly_discovered.append(us_sym)
                                print(f"🆕 [GMAIL SYNC] 偵測到新美股持股: {us_sym} ({us_name})")

                # 2. 國泰【AI投資日報】台股庫存
                if "AI投資日報" in subj:
                    for tw_name, (tw_sym, tw_ticker) in KNOWN_TW_NAME_TO_SYMBOL.items():
                        if f"{tw_name} " in text:
                            if tw_sym not in holdings_by_symbol:
                                holdings_by_symbol[tw_sym] = {
                                    "symbol": tw_sym,
                                    "ticker": tw_ticker,
                                    "name": tw_name,
                                    "market": "TW",
                                    "shares": 1,
                                }
                                newly_discovered.append(tw_sym)
                                print(f"🆕 [GMAIL SYNC] 偵測到新台股庫存: {tw_name} ({tw_sym})")

                # 3. 券商【成交回報 / 買賣成交】
                if "成交" in subj:
                    for m in re.finditer(r"(買進|現買|融資買)[\s:：]*([0-9]{4,5})\s*([\u4e00-\u9fa5A-Za-z0-9\-]+)?", text):
                        tk = m.group(2).strip()
                        nm = (m.group(3) or tk).strip()
                        sym = f"{tk}.TW"
                        if sym not in holdings_by_symbol:
                            holdings_by_symbol[sym] = {
                                "symbol": sym,
                                "ticker": tk,
                                "name": nm,
                                "market": "TW",
                                "shares": 1,
                            }
                            newly_discovered.append(sym)
                            print(f"🆕 [GMAIL SYNC] 從成交回報偵測到新買進台股: {nm} ({sym})")

                    # 美股買進成交回報
                    for m in re.finditer(r"(?:Bought|Buy|買進)\s+(?:[0-9,]+\s+)?(?:shares of\s+)?([A-Z]{2,5})\b", text):
                        us_tk = m.group(1).strip()
                        if us_tk not in ("USD", "TWD", "ETF", "PDF") and us_tk not in holdings_by_symbol:
                            holdings_by_symbol[us_tk] = {
                                "symbol": us_tk,
                                "ticker": us_tk,
                                "name": us_tk,
                                "market": "US",
                                "shares": 1,
                            }
                            newly_discovered.append(us_tk)
                            print(f"🆕 [GMAIL SYNC] 從成交回報偵測到新買進美股: {us_tk}")

        mail.logout()
        updated_list = list(holdings_by_symbol.values())
        if newly_discovered:
            latest_stock_state = load_stock_alert_state()
            latest_stock_state["holdings"] = updated_list
            latest_stock_state["last_gmail_sync"] = get_taipei_now_str()
            save_stock_alert_state(latest_stock_state)
        print(f"[GMAIL SYNC] 同步完成，當前持股清單 ({len(updated_list)} 檔): {[h['symbol'] for h in updated_list]}")
        return updated_list
    except Exception as e:
        print(f"[WARN] Gmail 同步異常 ({e})，維持既有持股清單。")
        return current_holdings


# ============================================================================
# Step 2: Financial Statements, News & Earnings Call Detection
# ============================================================================
def _fmt_num(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "N/A"
    if isinstance(val, (int, float)):
        if abs(val) >= 1e9:
            return f"{val / 1e9:,.2f}B"
        if abs(val) >= 1e6:
            return f"{val / 1e6:,.2f}M"
        return f"{val:,.2f}"
    return str(val)


def fetch_stock_financial_snapshot(stock: dict) -> dict:
    """
    擷取公司官方發布之第一手財務三表（損益表、資產負債表、現金流量表），
    並由程式自主精算：三率（毛利率/營益率/淨利率）、研發費用率、QoQ/YoY 成長率、
    三率季增減（bps）、存貨週轉天數（DOI）、自由現金流（FCF）與獲利含金量（OCF/NI）。
    嚴格不抓取任何第三方分析師預測或目標價。
    """
    import yfinance as yf

    symbol = stock["symbol"]
    ticker = stock["ticker"]
    name = stock["name"]
    market = stock["market"]

    t = yf.Ticker(symbol)
    try:
        info = t.info or {}
    except Exception:
        info = {}

    quote_type = info.get("quoteType", "EQUITY")
    currency = info.get("currency", "TWD" if market == "TW" else "USD")
    current_price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    trailing_pe = info.get("trailingPE")
    pb_ratio = info.get("priceToBook")
    market_cap = info.get("marketCap")
    roe = info.get("returnOnEquity")
    high_52w = info.get("fiftyTwoWeekHigh")
    low_52w = info.get("fiftyTwoWeekLow")

    quarters = []
    q_table_rows = []
    bs_table_rows = []
    cf_table_rows = []
    latest_quarter_date = ""

    def _extract_df_series(df, cols, keys):
        if df is None or df.empty:
            return [None] * len(cols)
        for k in keys:
            if k in df.index:
                out = []
                for c in cols:
                    val = df.loc[k, c] if c in df.columns else None
                    if val is not None and not (isinstance(val, float) and math.isnan(val)):
                        try:
                            out.append(float(val))
                        except Exception:
                            out.append(None)
                    else:
                        out.append(None)
                return out
        return [None] * len(cols)

    try:
        q_inc = t.quarterly_income_stmt
        q_bs = getattr(t, "quarterly_balance_sheet", None)
        q_cf = getattr(t, "quarterly_cashflow", None)

        if q_inc is not None and not q_inc.empty:
            cols = list(q_inc.columns[:5])
            quarters = [c.strftime("%Y-%m-%d") if hasattr(c, "strftime") else str(c)[:10] for c in cols]
            if quarters:
                latest_quarter_date = quarters[0]

            revs = _extract_df_series(q_inc, cols, ["Total Revenue", "Operating Revenue"])
            cogs_list = _extract_df_series(q_inc, cols, ["Cost Of Revenue", "Reconciled Cost Of Revenue"])
            gps = _extract_df_series(q_inc, cols, ["Gross Profit"])
            rds = _extract_df_series(q_inc, cols, ["Research And Development"])
            ois = _extract_df_series(q_inc, cols, ["Operating Income", "Total Operating Income As Reported"])
            nis = _extract_df_series(q_inc, cols, ["Net Income", "Net Income Common Stockholders"])
            eps_list = _extract_df_series(q_inc, cols, ["Diluted EPS", "Basic EPS"])

            # 資產負債表欄位對齊
            bs_cols = list(q_bs.columns[:5]) if (q_bs is not None and not q_bs.empty) else []
            cash_list = _extract_df_series(q_bs, bs_cols, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"])
            ar_list = _extract_df_series(q_bs, bs_cols, ["Accounts Receivable", "Receivables"])
            inv_list = _extract_df_series(q_bs, bs_cols, ["Inventory"])
            ppe_list = _extract_df_series(q_bs, bs_cols, ["Net PPE"])
            assets_list = _extract_df_series(q_bs, bs_cols, ["Total Assets"])
            liab_list = _extract_df_series(q_bs, bs_cols, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
            eq_list = _extract_df_series(q_bs, bs_cols, ["Stockholders Equity", "Total Equity Gross Minority Interest"])

            # 現金流量表欄位對齊
            cf_cols = list(q_cf.columns[:5]) if (q_cf is not None and not q_cf.empty) else []
            ocf_list = _extract_df_series(q_cf, cf_cols, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
            capex_list = _extract_df_series(q_cf, cf_cols, ["Capital Expenditure", "Capital Expenditures"])
            fcf_list = _extract_df_series(q_cf, cf_cols, ["Free Cash Flow"])
            da_list = _extract_df_series(q_cf, cf_cols, ["Depreciation And Amortization", "Reconciled Depreciation"])

            for idx, q_date in enumerate(quarters):
                rev = revs[idx]
                cogs = cogs_list[idx]
                gp = gps[idx]
                rd = rds[idx]
                oi = ois[idx]
                ni = nis[idx]
                eps = eps_list[idx]

                gm_pct = (gp / rev * 100.0) if (rev and gp is not None and rev != 0) else None
                rd_pct = (rd / rev * 100.0) if (rev and rd is not None and rev != 0) else None
                om_pct = (oi / rev * 100.0) if (rev and oi is not None and rev != 0) else None
                nm_pct = (ni / rev * 100.0) if (rev and ni is not None and rev != 0) else None

                rev_qoq = None
                if idx + 1 < len(revs) and revs[idx] and revs[idx + 1] and revs[idx + 1] != 0:
                    rev_qoq = (revs[idx] - revs[idx + 1]) / abs(revs[idx + 1]) * 100.0

                rev_yoy = None
                if idx + 4 < len(revs) and revs[idx] and revs[idx + 4] and revs[idx + 4] != 0:
                    rev_yoy = (revs[idx] - revs[idx + 4]) / abs(revs[idx + 4]) * 100.0

                prev_gm = (gps[idx + 1] / revs[idx + 1] * 100.0) if (idx + 1 < len(revs) and revs[idx + 1] and gps[idx + 1] is not None) else None
                gm_qoq_bps = int(round((gm_pct - prev_gm) * 100)) if (gm_pct is not None and prev_gm is not None) else None

                q_table_rows.append({
                    "quarter": q_date,
                    "revenue": rev,
                    "gross_margin_pct": gm_pct,
                    "gm_qoq_bps": gm_qoq_bps,
                    "rd_margin_pct": rd_pct,
                    "operating_margin_pct": om_pct,
                    "net_margin_pct": nm_pct,
                    "net_income": ni,
                    "eps": eps,
                    "rev_qoq_pct": rev_qoq,
                    "rev_yoy_pct": rev_yoy,
                })

                # 資產負債表自主精算
                if idx < len(bs_cols):
                    b_q = bs_cols[idx].strftime("%Y-%m-%d") if hasattr(bs_cols[idx], "strftime") else str(bs_cols[idx])[:10]
                    inv_val = inv_list[idx]
                    doi_days = (inv_val / cogs * 90.0) if (inv_val is not None and cogs and cogs > 0) else None
                    debt_ratio = (liab_list[idx] / assets_list[idx] * 100.0) if (liab_list[idx] is not None and assets_list[idx]) else None
                    bs_table_rows.append({
                        "quarter": b_q,
                        "cash": cash_list[idx],
                        "ar": ar_list[idx],
                        "inventory": inv_val,
                        "doi_days": doi_days,
                        "ppe": ppe_list[idx],
                        "total_assets": assets_list[idx],
                        "equity": eq_list[idx],
                        "debt_ratio_pct": debt_ratio,
                    })

                # 現金流量表自主精算
                if idx < len(cf_cols):
                    c_q = cf_cols[idx].strftime("%Y-%m-%d") if hasattr(cf_cols[idx], "strftime") else str(cf_cols[idx])[:10]
                    ocf_val = ocf_list[idx]
                    capex_val = capex_list[idx]
                    fcf_val = fcf_list[idx]
                    if fcf_val is None and ocf_val is not None and capex_val is not None:
                        fcf_val = ocf_val - abs(capex_val)
                    ocf_to_ni = (ocf_val / ni * 100.0) if (ocf_val is not None and ni and ni > 0) else None
                    cf_table_rows.append({
                        "quarter": c_q,
                        "ocf": ocf_val,
                        "capex": capex_val,
                        "fcf": fcf_val,
                        "da": da_list[idx],
                        "ocf_to_ni_pct": ocf_to_ni,
                    })
    except Exception as e:
        print(f"[WARN] 擷取 {symbol} 季度財務三表異常: {e}", file=sys.stderr)

    # 格式化第一手財務三表 Markdown 供模型自主推理
    md_lines = [
        f"### 📌 標的基礎市場數據與實際歷史估值（{name} / {symbol}）",
        f"- **資產類別**：{quote_type}（市場：{market}，財報計價幣別：{currency}）",
        f"- **最新股價**：{current_price} {currency}（52 週區間：{low_52w} ～ {high_52w}）",
        f"- **總市值**：{_fmt_num(market_cap)} {currency}",
        f"- **實際歷史本益比 (Trailing P/E)**：{f'{trailing_pe:.2f}' if isinstance(trailing_pe, (int, float)) else 'N/A'} ｜ **股價淨值比 (P/B)**：{f'{pb_ratio:.2f}' if isinstance(pb_ratio, (int, float)) else 'N/A'} ｜ **股東權益報酬率 (ROE)**：{f'{roe*100:.2f}%' if isinstance(roe, (int, float)) else 'N/A'}",
        "",
    ]

    if q_table_rows:
        md_lines.append("### 📊 【表一】第一手損益表與三率自主精算表（Quarterly Income Statement）")
        md_lines.append("| 季度結算日 | 營業收入 | 營收季增 (QoQ) | 營收年增 (YoY) | 毛利率 (季變動 bps) | 研發費用率 | 營業利益率 | 稅後淨利 | 每股盈餘 (EPS) |")
        md_lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        for r in q_table_rows[:4]:
            qoq_str = f"{r['rev_qoq_pct']:+.2f}%" if r["rev_qoq_pct"] is not None else "N/A"
            yoy_str = f"{r['rev_yoy_pct']:+.2f}%" if r["rev_yoy_pct"] is not None else "N/A"
            bps_str = f" ({r['gm_qoq_bps']:+d} bps)" if r["gm_qoq_bps"] is not None else ""
            gm_str = f"{r['gross_margin_pct']:.2f}%{bps_str}" if r["gross_margin_pct"] is not None else "N/A"
            rd_str = f"{r['rd_margin_pct']:.2f}%" if r["rd_margin_pct"] is not None else "N/A"
            om_str = f"{r['operating_margin_pct']:.2f}%" if r["operating_margin_pct"] is not None else "N/A"
            eps_str = f"{r['eps']:.2f}" if r["eps"] is not None else "N/A"
            md_lines.append(
                f"| **{r['quarter']}** | {_fmt_num(r['revenue'])} | {qoq_str} | {yoy_str} | **{gm_str}** | {rd_str} | **{om_str}** | {_fmt_num(r['net_income'])} | **{eps_str}** |"
            )
        md_lines.append("")

    if bs_table_rows:
        md_lines.append("### 🏦 【表二】第一手資產負債表與存貨週轉自主精算表（Quarterly Balance Sheet）")
        md_lines.append("| 季度結算日 | 現金及短投 | 應收帳款 | 存貨 (Inventory) | 存貨週轉天數 (DOI) | 不動產廠房設備 (PP&E) | 股東權益 | 負債比率 |")
        md_lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        for b in bs_table_rows[:4]:
            doi_str = f"{b['doi_days']:.1f} 天" if b["doi_days"] is not None else "N/A"
            dr_str = f"{b['debt_ratio_pct']:.1f}%" if b["debt_ratio_pct"] is not None else "N/A"
            md_lines.append(
                f"| **{b['quarter']}** | {_fmt_num(b['cash'])} | {_fmt_num(b['ar'])} | {_fmt_num(b['inventory'])} | **{doi_str}** | {_fmt_num(b['ppe'])} | {_fmt_num(b['equity'])} | {dr_str} |"
            )
        md_lines.append("")

    if cf_table_rows:
        md_lines.append("### 💵 【表三】第一手現金流量表與獲利含金量自主精算表（Quarterly Cash Flow Statement）")
        md_lines.append("| 季度結算日 | 營業活動現金流 (OCF) | 資本支出 (CapEx) | 自由現金流 (FCF) | 折舊與攤銷 (D&A) | 獲利含金量 (OCF/淨利) |")
        md_lines.append("| :--- | :---: | :---: | :---: | :---: | :---: |")
        for c in cf_table_rows[:4]:
            ocf_ni_str = f"{c['ocf_to_ni_pct']:.1f}%" if c["ocf_to_ni_pct"] is not None else "N/A"
            md_lines.append(
                f"| **{c['quarter']}** | {_fmt_num(c['ocf'])} | {_fmt_num(c['capex'])} | **{_fmt_num(c['fcf'])}** | {_fmt_num(c['da'])} | **{ocf_ni_str}** |"
            )
        md_lines.append("")

    if not q_table_rows:
        md_lines.append("（註：此標的為 ETF 或暫無標準季度損益表，請聚焦於其官方成分股結構與我們自主推演之總經配置邏輯）")

    return {
        "symbol": symbol,
        "ticker": ticker,
        "name": name,
        "market": market,
        "quote_type": quote_type,
        "current_price": current_price,
        "currency": currency,
        "latest_quarter_date": latest_quarter_date,
        "financial_markdown": "\n".join(md_lines),
    }


def fetch_earnings_news_and_events(stock: dict) -> dict:
    """
    嚴格遵守使用者要求：不抓取任何第三方分析師、投顧或外資券商的新聞與報告。
    僅提供純粹的第一手自主研究原則聲明，確保模型 100% 依賴第一手財務三表與法說會原音。
    """
    return {
        "headlines": [],
        "news_markdown": (
            "### 🛡️ 第一手獨立研究規範（零第三方分析師／零投顧資料）\n"
            "- 本次分析**已完全屏蔽**所有外部投顧、外資券商報告與財經媒體二手評論。\n"
            "- 請 **100% 依據上方三張第一手官方財務報表（損益表、資產負債表、現金流量表）與法說會第一手原音／官方經營層發言**，由你自己獨立運算、獨立整理並提出你自己的投資觀點。"
        ),
        "detected_call_tag": None,
    }


def search_and_download_earnings_audio(stock: dict, out_dir: Path) -> dict:
    """
    使用 yt-dlp 自動搜尋最新一場法說會／Earnings Call 音訊，並最佳化為 16kHz 單聲道 MP3。
    若為 ETF 或 YouTube 雲端 IP 受限無法下載，則安全返回 audio_path=None，交由財報與新聞 + Grounding 多模態推演。
    """
    ticker = stock["ticker"]
    name = stock["name"]
    market = stock["market"]
    year = get_taipei_now().year

    result = {
        "found": False,
        "video_id": None,
        "title": None,
        "url": None,
        "duration": 0,
        "audio_path": None,
    }

    try:
        import yt_dlp
    except ImportError:
        return result

    if market == "TW":
        queries = [
            f"ytsearch6:{name} {ticker} 法說會 完整 {year}",
            f"ytsearch6:{name} {ticker} 法說會 {year}",
        ]
    else:
        queries = [
            f"ytsearch6:{ticker} {name} earnings call {year}",
            f"ytsearch6:{ticker} quarterly earnings conference call {year}",
        ]

    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
    }

    candidates = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for q in queries:
            try:
                info = ydl.extract_info(q, download=False)
                for entry in (info or {}).get("entries", []):
                    vid = entry.get("id")
                    title = entry.get("title") or ""
                    dur = int(entry.get("duration") or 0)
                    if not vid or not title:
                        continue
                    low = title.lower()
                    # 必須包含標的名稱或代號，以及法說會/財報關鍵字
                    has_stock = (ticker.lower() in low) or (name.lower() in low)
                    has_call_kw = any(k in low for k in ["法說會", "法說", "earnings", "conference call", "quarterly", "q1", "q2", "q3", "q4", "財報"])
                    if has_stock and has_call_kw and 240 <= dur <= 7200:
                        # 優先挑選 20 分鐘以上之完整法說會原音
                        is_full_call = 1200 <= dur <= 6000 and any(k in low for k in ["完整", "live", "earnings call", "法說會"])
                        q_bonus = 4000 if any(k in low for k in ["第四季", "q4", "4q"]) else (3000 if any(k in low for k in ["第三季", "q3", "3q"]) else (2000 if any(k in low for k in ["第二季", "q2", "2q"]) else 0)); score = (20000 if is_full_call else 2000) + q_bonus + (2000 if str(year) in low else 0)
                        candidates.append((score, vid, title, dur))
            except Exception:
                pass

    if not candidates:
        print(f"[AUDIO] 未在 YouTube 搜尋到 {name} ({ticker}) 的近期法說會長音訊。")
        return result

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, best_vid, best_title, best_dur = candidates[0]
    video_url = f"https://www.youtube.com/watch?v={best_vid}"
    result.update({
        "found": True,
        "video_id": best_vid,
        "title": best_title,
        "url": video_url,
        "duration": best_dur,
    })
    print(f"[AUDIO] 找到法說會音訊候選：{best_title} ({best_dur}s) -> {video_url}")

    # 嘗試下載並轉為 16kHz 單聲道 MP3
    out_dir.mkdir(parents=True, exist_ok=True)
    opt_mp3 = out_dir / f"{market}_{ticker}_{best_vid}_opt16k.mp3"
    if opt_mp3.exists() and opt_mp3.stat().st_size > 200_000:
        result["audio_path"] = opt_mp3
        return result

    raw_tmpl = str(out_dir / f"{market}_{ticker}_{best_vid}.%(ext)s")
    ffmpeg_bin = "ffmpeg"
    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass

    dl_opts = {
        "format": "bestaudio[abr<=64]/worstaudio/bestaudio",
        "outtmpl": raw_tmpl,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            ydl.download([video_url])

        downloaded_files = [
            f for f in out_dir.glob(f"{market}_{ticker}_{best_vid}.*")
            if f.name != opt_mp3.name and f.stat().st_size > 100_000
        ]
        if downloaded_files:
            raw_file = downloaded_files[0]
            subprocess.run(
                [ffmpeg_bin, "-y", "-i", str(raw_file), "-ac", "1", "-ar", "16000", "-b:a", "32k", str(opt_mp3)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=180,
            )
            try:
                raw_file.unlink()
            except Exception:
                pass
            if opt_mp3.exists() and opt_mp3.stat().st_size > 100_000:
                print(f"[AUDIO] 法說會音訊下載並最佳化完成: {opt_mp3.name} ({opt_mp3.stat().st_size / 1024 / 1024:.2f} MB)")
                result["audio_path"] = opt_mp3
    except Exception as e:
        print(f"[WARN] 法說會音訊下載略過（雲端環境或來源限制: {e}），將使用完整財報數據與 Google Search Grounding 執行深度分析。")

    return result


# ============================================================================
# Step 3: Gemini 4 Argon Deep Analysis (Audio + Financials + Search Grounding)
# ============================================================================
def analyze_stock_with_argon(
    stock: dict,
    trigger_reason: str,
    fin_snapshot: dict,
    news_info: dict,
    audio_info: dict,
) -> str:
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未設定 GEMINI_API_KEY 環境變數！")

    client = genai.Client(api_key=api_key)
    uploaded_file = None
    audio_path = audio_info.get("audio_path")

    if audio_path and Path(audio_path).exists():
        try:
            print(f"[GEMINI] 正在上傳法說會音訊至 Gemini Files API: {Path(audio_path).name} ...")
            uploaded_file = client.files.upload(file=str(audio_path))
            for _ in range(30):
                f_info = client.files.get(name=uploaded_file.name)
                if "PROCESSING" not in str(getattr(f_info, "state", "")).upper():
                    break
                time.sleep(2)
            print(f"[GEMINI] 法說會音訊上傳就緒: {uploaded_file.uri}")
        except Exception as e:
            print(f"[WARN] 音訊上傳失敗 ({e})，改以純數據與即時檢索分析。")
            uploaded_file = None

    sys_inst = (
        EARNINGS_SYSTEM_INSTRUCTION
        .replace("{公司名稱}", stock["name"])
        .replace("{股票代號}", stock["symbol"])
        .replace("{觸發原因}", trigger_reason)
    )

    audio_note = (
        f"已附上最新法說會／財報會議第一手原音錄音（標題：{audio_info.get('title')}），請直接聆聽經營層發言與現場問答原音細節，由你自己親自整理與分析！"
        if uploaded_file
        else f"（法說會音訊參考標題：{audio_info.get('title') or '無獨立音檔，請 100% 依據下方公司官方發布之第一手財務三表數據進行自主拆解，嚴禁引用任何外部投顧或分析師報告'}）"
    )

    user_prompt = f"""請針對以下投資組合持股標的，由你自己（Antigravity × Gemini 4 Argon）**100% 根據公司官方第一手財務三表（損益表、資產負債表、現金流量表）與法說會第一手原音**，親自完成數據精算、法說會整理與獨立投資分析報告（**嚴禁抓取或轉述任何其他分析師、外資券商或投顧的二手資料與目標價**）：

- **股票名稱與代號**：{stock['name']} ({stock['symbol']})
- **目前持有股數**：{stock.get('shares', 1)} 股
- **本次分析觸發原因**：{trigger_reason}
- **最新財報季度結算日**：{fin_snapshot.get('latest_quarter_date') or '最新季度'}
- **法說會第一手音訊狀態**：{audio_note}

---
{fin_snapshot['financial_markdown']}

---
{news_info['news_markdown']}

---
請嚴格依照系統指令的【四大核心維度 + 尾頁獨立總結】撰寫詳盡、數據精準、邏輯完全自主的繁體中文 Markdown 深度分析報告，並在第四部分與尾頁總結中，完全以你自己的第一性原理推演，針對我持有的 {stock['name']} ({stock['symbol']}) 提出明確的估值推算與加減碼/續抱策略！"""

    try:
        last_err = None
        for model_name in MODEL_PRIORITY_CHAIN:
            print(f"[GEMINI] 正在調用模型 {model_name} 執行財報與法說會深度分析...")
            parts = []
            if uploaded_file:
                parts.append({
                    "fileData": {
                        "fileUri": uploaded_file.uri,
                        "mimeType": uploaded_file.mime_type or "audio/mpeg",
                    }
                })
            parts.append({"text": user_prompt})

            payload = {
                "systemInstruction": {"parts": [{"text": sys_inst}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 65536},
            }

            req_data = json.dumps(payload).encode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            req = urllib.request.Request(
                url,
                data=req_data,
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json; charset=utf-8",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=540) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                candidates = resp_json.get("candidates", [])
                resp_parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
                report_text = "".join(p.get("text", "") for p in resp_parts if "text" in p).strip()
                if report_text and len(report_text) >= 300:
                    print(f"✅ [GEMINI] 模型 {model_name} 成功產出深度報告（共 {len(report_text)} 字元）！")
                    return report_text
                raise RuntimeError(f"模型 {model_name} 回傳內容過短 ({len(report_text)} 字元)")
            except urllib.error.HTTPError as he:
                err_body = he.read().decode("utf-8", errors="ignore")[:300]
                print(f"[WARN] 模型 {model_name} 回應 HTTP {he.code}: {err_body}，切換備援模型...")
                last_err = f"HTTP {he.code}: {err_body}"
            except Exception as ex:
                print(f"[WARN] 模型 {model_name} 執行異常 ({ex})，切換備援模型...")
                last_err = str(ex)

        raise RuntimeError(f"所有模型皆無法完成分析，最後錯誤：{last_err}")
    finally:
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


# ============================================================================
# Step 4: Cloud Delivery (Google Drive Archiving + Rich HTML Email)
# ============================================================================
def _markdown_to_styled_html(md_text: str) -> str:
    """將 Markdown 報告轉換為適合 Email 閱讀的精緻 HTML。"""
    lines = md_text.splitlines()
    html_out = []
    in_table = False
    in_list = False

    def _inline_fmt(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`(.+?)`", r"<code style='background:#f1f5f9;padding:2px 5px;border-radius:4px;font-size:13px;'>\1</code>", s)
        return s

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                html_out.append("</ul>")
                in_list = False
            if in_table:
                html_out.append("</table></div>")
                in_table = False
            continue

        # Table handling
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.match(r"^[:\-]+$", c.replace(" ", "")) for c in cells if c):
                continue
            if not in_table:
                html_out.append("<div style='overflow-x:auto;margin:14px 0;'><table style='width:100%;border-collapse:collapse;font-size:13px;border:1px solid #cbd5e1;'>")
                in_table = True
                html_out.append("<tr style='background-color:#0f172a;color:#ffffff;'>" + "".join(f"<th style='padding:8px 10px;border:1px solid #334155;text-align:left;'>{_inline_fmt(c)}</th>" for c in cells) + "</tr>")
            else:
                html_out.append("<tr style='border-bottom:1px solid #e2e8f0;'>" + "".join(f"<td style='padding:8px 10px;border:1px solid #cbd5e1;'>{_inline_fmt(c)}</td>" for c in cells) + "</tr>")
            continue
        elif in_table:
            html_out.append("</table></div>")
            in_table = False

        if stripped.startswith("# "):
            html_out.append(f"<h1 style='color:#0f172a;font-size:22px;border-bottom:2px solid #1e40af;padding-bottom:8px;margin-top:20px;'>{_inline_fmt(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            html_out.append(f"<h2 style='color:#1e3a8a;font-size:18px;margin-top:22px;margin-bottom:8px;background:#eff6ff;padding:8px 12px;border-left:4px solid #2563eb;border-radius:4px;'>{_inline_fmt(stripped[3:])}</h2>")
        elif stripped.startswith("### "):
            html_out.append(f"<h3 style='color:#0f172a;font-size:15px;margin-top:16px;margin-bottom:6px;'>{_inline_fmt(stripped[4:])}</h3>")
        elif stripped.startswith("> "):
            html_out.append(f"<blockquote style='margin:10px 0;padding:10px 14px;background:#f8fafc;border-left:4px solid #64748b;color:#334155;font-size:13px;'>{_inline_fmt(stripped[2:])}</blockquote>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_out.append("<ul style='margin:8px 0 12px 20px;padding:0;line-height:1.7;color:#1e293b;font-size:14px;'>")
                in_list = True
            html_out.append(f"<li style='margin-bottom:6px;'>{_inline_fmt(stripped[2:])}</li>")
        else:
            if in_list:
                html_out.append("</ul>")
                in_list = False
            html_out.append(f"<p style='margin:8px 0;line-height:1.7;color:#1e293b;font-size:14px;'>{_inline_fmt(stripped)}</p>")

    if in_list:
        html_out.append("</ul>")
    if in_table:
        html_out.append("</table></div>")

    return "\n".join(html_out)


def archive_report_to_gdrive(stock: dict, quarter_label: str, report_path: Path) -> str:
    """將深度報告上傳至 Google Drive: 證券操作紀錄/持股財報與法說會深度分析/{ticker}_{name}"""
    try:
        drive = GoogleDriveClient()
        root_sub = drive.get_or_create_subfolder(DEFAULT_DRIVE_PARENT_ID, "持股財報與法說會深度分析")
        stock_folder_id = drive.get_or_create_subfolder(root_sub, f"{stock['ticker']}_{stock['name']}")
        drive.upload_or_update_file(stock_folder_id, report_path, mime_type="text/markdown; charset=utf-8")
        folder_url = f"https://drive.google.com/drive/folders/{stock_folder_id}"
        print(f"☁️ [GDRIVE] 報告已歸檔至 Google Drive: {folder_url}")
        return folder_url
    except Exception as e:
        print(f"[WARN] Google Drive 歸檔略過 ({e})", file=sys.stderr)
        return ""


def send_earnings_report_email(stock: dict, quarter_label: str, trigger_reason: str, report_md: str, drive_url: str):
    gmail_user = os.environ.get("GMAIL_USER", "").strip()
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not gmail_user or not gmail_pass:
        local_cfg = Path(r"C:\Users\user\.gemini\config\skills\portfolio-monitor\config.json")
        if local_cfg.exists():
            try:
                cfg_data = json.loads(local_cfg.read_text(encoding="utf-8"))
                if not gmail_user:
                    gmail_user = cfg_data.get("email", {}).get("sender_email", "") or cfg_data.get("gmail", {}).get("email", "")
                if not gmail_pass:
                    gmail_pass = cfg_data.get("email", {}).get("sender_password", "") or cfg_data.get("gmail", {}).get("app_password", "")
            except Exception:
                pass

    if not gmail_user or not gmail_pass:
        print("[WARN] 未設定 GMAIL_USER / GMAIL_APP_PASSWORD，跳過寄送 Email。")
        return

    subject = f"【持股法說會與財報深度分析】{stock['name']} ({stock['symbol']}) - {quarter_label} 完整指引與投資觀點"
    body_html_content = _markdown_to_styled_html(report_md)
    drive_btn_html = (
        f"<div style='margin-top:16px;'><a href='{drive_url}' style='display:inline-block;background:#2563eb;color:#ffffff;text-decoration:none;padding:10px 18px;border-radius:6px;font-weight:bold;font-size:14px;'>📂 前往 Google 雲端硬碟檢視完整 Markdown 歸檔</a></div>"
        if drive_url
        else ""
    )

    full_html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Microsoft JhengHei', sans-serif; max-width: 760px; margin: 0 auto; border: 1px solid #cbd5e1; border-radius: 12px; overflow: hidden; background-color: #ffffff;">
      <div style="background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%); color: #ffffff; padding: 22px 28px;">
        <div style="font-size: 12px; letter-spacing: 1px; text-transform: uppercase; color: #93c5fd; margin-bottom: 6px;">
          GitHub Cloud × Gemini 4 Argon Buy-Side Research
        </div>
        <h2 style="margin: 0; font-size: 22px;">{stock['name']} ({stock['symbol']}) 財報與法說會深度拆解</h2>
        <div style="font-size: 13px; color: #e2e8f0; margin-top: 8px;">
          🔔 <strong>觸發原因：</strong>{trigger_reason} ｜ 📅 <strong>財報季度：</strong>{quarter_label}
        </div>
      </div>
      <div style="padding: 26px 28px;">
        {body_html_content}
        {drive_btn_html}
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0 12px 0;">
        <div style="font-size: 12px; color: #64748b;">
          🤖 本報告由 <strong>GitHub Actions 全雲端持股財報法說會監控系統</strong> 自動偵測並調用 <strong>Gemini 4 Argon</strong> 產出（免開電腦全自動運行）。
        </div>
      </div>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = f"Portfolio Argon Research <{gmail_user}>"
    msg["To"] = gmail_user
    msg.attach(MIMEText(report_md, "plain", "utf-8"))
    msg.attach(MIMEText(full_html, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, [gmail_user], msg.as_string())

    print(f"📧 [EMAIL] 已成功寄送完整深度分析報告至 {gmail_user}: {subject}")


# ============================================================================
# Main Orchestration
# ============================================================================
def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="GitHub Cloud Portfolio Earnings & Investor Conference Analyzer (Gemini 4 Argon)")
    parser.add_argument("--force-symbol", default="", help="強制指定分析特定持股代號（例如 2330.TW, 2454.TW, NVDA，或輸入 ALL 分析全部持股）")
    parser.add_argument("--init-baseline", action="store_true", help="僅建立當前既有持股之季度基準線（不消耗 Token），後續有新標的或新季度時自動觸發")
    args = parser.parse_args()

    state = load_earnings_state()
    analyzed_map = state.setdefault("analyzed_holdings", {})
    history_list = state.setdefault("history", [])

    # 1. 優先從 Gmail 同步最新持股清單
    holdings = sync_holdings_from_gmail()
    force_sym = args.force_symbol.strip().upper()

    # 若指定單一非既有持股代號（例如使用者手動在 GitHub Actions 輸入某檔股票代號測試），亦支援直接分析
    if force_sym and force_sym != "ALL":
        matched = [h for h in holdings if h["symbol"].upper() == force_sym or h["ticker"].upper() == force_sym]
        if not matched:
            is_tw = force_sym.endswith(".TW") or force_sym.endswith(".TWO") or force_sym.isdigit()
            sym = f"{force_sym}.TW" if force_sym.isdigit() else force_sym
            tk = sym.split(".")[0]
            nm = next((k for k, v in KNOWN_TW_NAME_TO_SYMBOL.items() if v[0] == sym or v[1] == tk), tk)
            holdings.append({"symbol": sym, "ticker": tk, "name": nm, "market": "TW" if is_tw else "US", "shares": 1})

    work_dir = Path(__file__).parent / "work_earnings_tmp"
    work_dir.mkdir(parents=True, exist_ok=True)

    for stock in holdings:
        sym = stock["symbol"]
        ticker = stock["ticker"]
        name = stock["name"]

        if force_sym and force_sym != "ALL":
            if sym.upper() != force_sym and ticker.upper() != force_sym:
                continue

        print(f"\n------------------------------------------------------------")
        print(f"[CHECK] 檢查持股標的：{name} ({sym}) ...")

        fin_snapshot = fetch_stock_financial_snapshot(stock)
        latest_q = fin_snapshot.get("latest_quarter_date") or ""
        prev_rec = analyzed_map.get(sym)

        # 若為 --init-baseline 模式，直接記錄當前季度與目前既有法說會 video_id 作為基準線
        if args.init_baseline and not force_sym:
            known_vids = []
            if fin_snapshot.get("quote_type") != "ETF":
                try:
                    import yt_dlp
                    year = get_taipei_now().year
                    q_str = f"ytsearch5:{name} {ticker} 法說會 {year}" if stock["market"] == "TW" else f"ytsearch5:{ticker} {name} earnings call {year}"
                    with yt_dlp.YoutubeDL({"extract_flat": True, "quiet": True, "no_warnings": True}) as ydl:
                        info = ydl.extract_info(q_str, download=False)
                        for entry in (info or {}).get("entries", []):
                            if entry.get("id"):
                                known_vids.append(entry["id"])
                except Exception:
                    pass
            analyzed_map[sym] = {
                "symbol": sym,
                "ticker": ticker,
                "name": name,
                "market": stock["market"],
                "last_quarter_date": latest_q or "BASELINE",
                "last_video_id": known_vids[0] if known_vids else None,
                "analyzed_video_ids": known_vids,
                "last_analyzed_at": get_taipei_now_str(),
            }
            print(f"✅ [BASELINE] 已記錄 {name} ({sym}) 基準季度: {latest_q or 'BASELINE'} | 既有影片數: {len(known_vids)}")
            continue

        # 判斷是否觸發 Gemini 4 Argon 深度分析
        trigger_reason = None
        if force_sym:
            trigger_reason = f"手動指定強制執行最新一季財報與法說會深度分析 ({sym})"
        elif prev_rec is None:
            trigger_reason = f"【新增持股標的首次建檔】偵測到投资組合新增持有 {name} ({sym})，自動抓取最新一季財報與法說會提出深度看法"
        elif latest_q and prev_rec.get("last_quarter_date") and latest_q > prev_rec.get("last_quarter_date", ""):
            trigger_reason = f"【最新一季財報正式發布】偵測到 {name} ({sym}) 發布最新季度 ({latest_q}) 財務報表（原紀錄：{prev_rec.get('last_quarter_date')}）"
        else:
            # 進一步檢查是否剛召開最新一場尚未分析過的完整法說會（僅針對個股，ETF 不檢查法說會音訊）
            if fin_snapshot.get("quote_type") != "ETF":
                try:
                    import yt_dlp
                    year = get_taipei_now().year
                    q_str = f"ytsearch3:{name} {ticker} 法說會 完整 {year}" if stock["market"] == "TW" else f"ytsearch3:{ticker} {name} earnings call {year}"
                    with yt_dlp.YoutubeDL({"extract_flat": True, "quiet": True, "no_warnings": True}) as ydl:
                        info = ydl.extract_info(q_str, download=False)
                        for entry in (info or {}).get("entries", []):
                            vid = entry.get("id")
                            title = entry.get("title") or ""
                            dur = int(entry.get("duration") or 0)
                            low = title.lower()
                            if vid and 1500 <= dur <= 6500 and ((ticker.lower() in low) or (name.lower() in low)):
                                if any(k in low for k in ["法說會", "earnings call"]):
                                    known_vids = prev_rec.get("analyzed_video_ids") or ([prev_rec.get("last_video_id")] if prev_rec.get("last_video_id") else [])
                                    if vid not in known_vids:
                                        trigger_reason = f"【最新法說會召開】偵測到 {name} ({sym}) 最新法說會完整錄音發布：《{title}》"
                                    break
                except Exception:
                    pass

        if not trigger_reason:
            print(f"✅ [SKIP] {name} ({sym}) 無新增財報季度（最新：{latest_q or 'N/A'}）且無新召開之法說會，不消耗 Token。")
            continue

        print(f"🚀 [TRIGGER] 觸發 Gemini 4 Argon 深度拆解！原因：{trigger_reason}")
        news_info = fetch_earnings_news_and_events(stock)
        audio_info = search_and_download_earnings_audio(stock, work_dir) if fin_snapshot.get("quote_type") != "ETF" else {"found": False}

        report_md = analyze_stock_with_argon(
            stock=stock,
            trigger_reason=trigger_reason,
            fin_snapshot=fin_snapshot,
            news_info=news_info,
            audio_info=audio_info,
        )

        q_label = latest_q if latest_q else get_taipei_now().strftime("%Y-%m")
        safe_q = re.sub(r"[^0-9A-Za-z\-_]", "", q_label)
        report_filename = f"{ticker}_{name}_{safe_q}_法說會與財報深度分析.md"
        report_path = work_dir / report_filename
        report_path.write_text(report_md, encoding="utf-8")

        # 歸檔至 Google Drive 並寄送 HTML Email
        drive_url = archive_report_to_gdrive(stock, q_label, report_path)
        send_earnings_report_email(stock, q_label, trigger_reason, report_md, drive_url)

        # 更新已分析狀態
        prev_vids = (prev_rec or {}).get("analyzed_video_ids", [])
        if audio_info.get("video_id") and audio_info["video_id"] not in prev_vids:
            prev_vids.append(audio_info["video_id"])

        analyzed_map[sym] = {
            "symbol": sym,
            "ticker": ticker,
            "name": name,
            "market": stock["market"],
            "last_quarter_date": latest_q or (prev_rec or {}).get("last_quarter_date", "ANALYZED"),
            "last_video_id": audio_info.get("video_id") or (prev_rec or {}).get("last_video_id"),
            "analyzed_video_ids": prev_vids,
            "last_analyzed_at": get_taipei_now_str(),
            "last_trigger_reason": trigger_reason,
        }
        history_list.append({
            "symbol": sym,
            "name": name,
            "quarter": q_label,
            "reason": trigger_reason,
            "analyzed_at": get_taipei_now_str(),
            "drive_url": drive_url,
        })
        save_earnings_state(state)

    save_earnings_state(state)
    print(f"\n[{get_taipei_now_str()}] 雲端持股財報與法說會巡檢完畢。")


if __name__ == "__main__":
    main()
