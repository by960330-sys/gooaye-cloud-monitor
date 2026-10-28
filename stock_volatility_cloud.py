#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 全雲端持股盤中重大波動監控系統 (Stock Volatility Cloud Monitor)
================================================================================
無須開機、無須手動點擊 Google Apps Script 授權，直接由 GitHub Actions 定時執行：
1. 【優先連結 Gmail 同步最新持股】：
   每次執行時，優先透過 IMAP 連線至 by960330@gmail.com，掃描最新「國泰證券 AI投資日報 (台股庫存)」、
   「美股庫存電子報」及「券商成交回報」，自動確認並更新當前實際持有的股票清單！
2. 【監控標的】：
   預設並動態同步：台積電 (2330.TW)、聯發科 (2454.TW)、VOO、SPCX（若信箱有新增成交或庫存異動自動納入）。
3. 【嚴格開盤時段檢測】：
   台股開盤時段 (週一至週五 09:00~13:45 台北時間) 與美股開盤時段 (美東 09:30~16:05)。
4. 【三階波動預警與防洗版機制（±3% / ±6% / ±10%）】：
   - 當日漲跌幅首次達 ±3%：寄送「≥ 3% 首次達標」HTML 警報信
   - 當日漲跌幅擴大達 ±6%：寄送「≥ 6% 擴大波動」HTML 警報信
   - 當日漲跌幅劇震達 ±10%：寄送「≥ 10% 劇烈波動」HTML 警報信
"""

import argparse
import email
from email.header import Header, decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import imaplib
import json
import os
import re
import smtplib
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

STATE_FILE = Path(__file__).parent / "stock_alert_state.json"

DEFAULT_HOLDINGS = [
    {"symbol": "2330.TW", "ticker": "2330", "name": "台積電", "market": "TW", "shares": 83},
    {"symbol": "2454.TW", "ticker": "2454", "name": "聯發科", "market": "TW", "shares": 11},
    {"symbol": "VOO", "ticker": "VOO", "name": "Vanguard S&P 500 ETF", "market": "US", "shares": 1},
    {"symbol": "SPCX", "ticker": "SPCX", "name": "Space Exploration Technologies", "market": "US", "shares": 1},
]

KNOWN_TW_NAME_TO_SYMBOL = {
    "台積電": ("2330.TW", "2330"),
    "聯發科": ("2454.TW", "2454"),
    "鴻海": ("2317.TW", "2317"),
    "廣達": ("2382.TW", "2382"),
    "富邦台50": ("006208.TW", "006208"),
    "元大台灣50": ("0050.TW", "0050"),
}


def get_taipei_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def is_tw_market_open() -> bool:
    now_tw = get_taipei_now()
    if now_tw.weekday() >= 5:
        return False
    hm = now_tw.hour * 60 + now_tw.minute
    return (9 * 60) <= hm <= (13 * 60 + 45)


def is_us_market_open() -> bool:
    try:
        ny_now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        ny_now = datetime.now(timezone(timedelta(hours=-4)))
    if ny_now.weekday() >= 5:
        return False
    hm = ny_now.hour * 60 + ny_now.minute
    return (9 * 60 + 25) <= hm <= (16 * 60 + 10)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return {"holdings": DEFAULT_HOLDINGS, "alerts": {}, "last_checked_at": None, "last_gmail_sync": None}


def save_state(state: dict):
    state["last_checked_at"] = get_taipei_now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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


def sync_holdings_from_gmail(state: dict) -> list:
    """
    優先連結 Gmail (by960330@gmail.com) 檢查最新國泰證券【AI投資日報】(台股庫存)、
    【美股庫存電子報】與【成交回報】，動態更新當前實際持股名單。
    """
    gmail_user = os.environ.get("GMAIL_USER", "by960330@gmail.com").strip()
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not gmail_pass:
        local_cfg = Path(r"C:\Users\user\.gemini\config\skills\portfolio-monitor\config.json")
        if local_cfg.exists():
            try:
                cfg_data = json.loads(local_cfg.read_text(encoding="utf-8"))
                gmail_pass = cfg_data.get("email", {}).get("sender_password", "")
            except Exception:
                pass

    current_holdings = state.get("holdings") or list(DEFAULT_HOLDINGS)
    if not gmail_pass:
        print("[WARN] 未設定 GMAIL_APP_PASSWORD，使用預設持股清單。")
        return current_holdings

    holdings_by_symbol = {h["symbol"]: dict(h) for h in current_holdings}
    verified_from_gmail = []

    try:
        print(f"[GMAIL SYNC] 正在連結 Gmail ({gmail_user}) 檢查最新券商庫存與成交回報...")
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(gmail_user, gmail_pass)
        mail.select("INBOX")

        since_date = (get_taipei_now() - timedelta(days=7)).strftime("%d-%b-%Y")
        status, data = mail.search(None, f'(SINCE {since_date})')
        if status == "OK" and data and data[0]:
            ids = data[0].split()[-50:]  # 檢查最近 50 封信之標題
            _, hdr_data = mail.fetch(b",".join(ids), "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")

            target_eids = []
            for item in hdr_data:
                if isinstance(item, tuple):
                    eid = item[0].decode().split()[0].encode()
                    msg = email.message_from_bytes(item[1])
                    subj = _decode_mime_str(msg.get("Subject", ""))
                    if any(k in subj for k in ["美股庫存電子報", "AI投資日報", "成交回報", "買賣成交"]):
                        target_eids.append((eid, subj))

            for eid, subj in target_eids[-6:]:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                if not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                text = _extract_email_text(msg)

                # 1. 解析國泰【美股庫存電子報】中的美股代號
                if "美股庫存電子報" in subj:
                    for m in re.finditer(r"\b([A-Z]{2,5})\s+([A-Za-z0-9&\.\-\s]{3,35}?)\s+\d+\.\d+\s+[\-\d\.]+\(", text):
                        us_sym = m.group(1).strip()
                        us_name = m.group(2).strip()
                        if us_sym not in ("USD", "ETF", "NEW", "TOP"):
                            if us_sym not in verified_from_gmail:
                                verified_from_gmail.append(us_sym)
                            if us_sym not in holdings_by_symbol:
                                holdings_by_symbol[us_sym] = {
                                    "symbol": us_sym,
                                    "ticker": us_sym,
                                    "name": us_name,
                                    "market": "US",
                                    "shares": 1,
                                }
                                print(f"[GMAIL SYNC] 從美股庫存電子報新增美股標的: {us_sym} ({us_name})")

                # 2. 解析國泰【AI投資日報】中的台股庫存
                if "AI投資日報" in subj:
                    for tw_name, (tw_sym, tw_ticker) in KNOWN_TW_NAME_TO_SYMBOL.items():
                        if f"{tw_name} " in text:
                            if tw_sym not in verified_from_gmail:
                                verified_from_gmail.append(tw_sym)
                            if tw_sym not in holdings_by_symbol:
                                holdings_by_symbol[tw_sym] = {
                                    "symbol": tw_sym,
                                    "ticker": tw_ticker,
                                    "name": tw_name,
                                    "market": "TW",
                                    "shares": 1,
                                }
                                print(f"[GMAIL SYNC] 從 AI投資日報新增台股庫存: {tw_name} ({tw_sym})")

                # 3. 解析台股成交回報 (買進/賣出)
                if "成交回報" in subj:
                    for m in re.finditer(r"(買進|賣出)[\s:：]*([0-9]{4,5})\s*([\u4e00-\u9fa5A-Za-z0-9]+)?", text):
                        act, tk, nm = m.group(1), m.group(2), (m.group(3) or tk).strip()
                        sym = f"{tk}.TW"
                        if "買" in act:
                            if sym not in verified_from_gmail:
                                verified_from_gmail.append(sym)
                            if sym not in holdings_by_symbol:
                                holdings_by_symbol[sym] = {
                                    "symbol": sym,
                                    "ticker": tk,
                                    "name": nm,
                                    "market": "TW",
                                    "shares": 1,
                                }
                                print(f"[GMAIL SYNC] 從成交回報偵測到新買進台股: {nm} ({sym})")

        mail.logout()
        updated_list = list(holdings_by_symbol.values())
        state["holdings"] = updated_list
        state["last_gmail_sync"] = get_taipei_now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[GMAIL SYNC] 信箱庫存驗證標的: {verified_from_gmail} | 最終監控持股 ({len(updated_list)} 檔): {[h['symbol'] for h in updated_list]}")
        return updated_list
    except Exception as e:
        print(f"[WARN] Gmail 同步過程發生例外 ({e})，維持既有持股清單: {[h['symbol'] for h in current_holdings]}")
        return current_holdings


def fetch_quote_yahoo(symbol: str) -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1d"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    result = (data.get("chart") or {}).get("result")
    if not result:
        raise RuntimeError(f"Yahoo Finance 未回傳 {symbol} 資料")

    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None or prev_close is None or prev_close <= 0:
        raise RuntimeError(f"{symbol} 缺少有效報價 (price={price}, prev_close={prev_close})")

    change_pct = ((price - prev_close) / prev_close) * 100.0
    return {
        "symbol": symbol,
        "currency": meta.get("currency", "TWD"),
        "price": float(price),
        "prev_close": float(prev_close),
        "change_pct": float(change_pct),
        "abs_change_pct": abs(float(change_pct)),
    }


def send_alert_email(stock: dict, quote: dict, tier_label: str):
    gmail_user = os.environ.get("GMAIL_USER", "by960330@gmail.com").strip()
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not gmail_pass:
        local_cfg = Path(r"C:\Users\user\.gemini\config\skills\portfolio-monitor\config.json")
        if local_cfg.exists():
            cfg_data = json.loads(local_cfg.read_text(encoding="utf-8"))
            gmail_pass = cfg_data.get("email", {}).get("sender_password", "")

    if not gmail_pass:
        raise RuntimeError("未設定 GMAIL_APP_PASSWORD 環境變數，無法發送預警信！")

    is_up = quote["change_pct"] >= 0
    direction = "📈 盤中大漲" if is_up else "📉 盤中大跌"
    sign = "+" if is_up else ""
    color = "#d93025" if is_up else "#188038"
    now_str = get_taipei_now().strftime("%Y-%m-%d %H:%M:%S")

    subject = f"【持股波動預警 {tier_label}】{stock['name']} ({stock['symbol']}) {direction} {sign}{quote['change_pct']:.2f}%"

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Microsoft JhengHei', sans-serif; max-width: 580px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.05);">
      <div style="background-color: {color}; color: #ffffff; padding: 18px 24px;">
        <h2 style="margin: 0; font-size: 20px;">{stock['name']} ({stock['symbol']}) 波動預警</h2>
        <div style="font-size: 14px; opacity: 0.9; margin-top: 4px;">觸發等級：{tier_label} | 持有股數：{stock.get('shares', '-')} 股</div>
      </div>
      <div style="padding: 24px; background-color: #ffffff;">
        <table style="width: 100%; border-collapse: collapse; font-size: 15px;">
          <tr style="border-bottom: 1px solid #f0f0f0;">
            <td style="padding: 10px 0; color: #666;">即時成交價：</td>
            <td style="padding: 10px 0; font-weight: bold; font-size: 20px; color: {color};">{quote['price']:,.2f} {quote['currency']}</td>
          </tr>
          <tr style="border-bottom: 1px solid #f0f0f0;">
            <td style="padding: 10px 0; color: #666;">昨日收盤價：</td>
            <td style="padding: 10px 0; font-weight: 500;">{quote['prev_close']:,.2f} {quote['currency']}</td>
          </tr>
          <tr style="border-bottom: 1px solid #f0f0f0;">
            <td style="padding: 10px 0; color: #666;">當日漲跌幅：</td>
            <td style="padding: 10px 0; font-weight: bold; font-size: 18px; color: {color};">
              {direction} {sign}{quote['change_pct']:.2f}%
            </td>
          </tr>
          <tr>
            <td style="padding: 10px 0; color: #666;">通報時間：</td>
            <td style="padding: 10px 0; color: #444;">{now_str} (Asia/Taipei)</td>
          </tr>
        </table>
        <div style="margin-top: 20px; padding: 12px; background-color: #f8f9fa; border-radius: 6px; font-size: 13px; color: #555;">
          💡 <strong>GitHub Cloud 全自動監控</strong>：當日漲跌幅首次達 <strong>±3%</strong>、擴大達 <strong>±6%</strong>、或劇震達 <strong>±10%</strong> 時，直接由雲端寄送警報信至 {gmail_user}。
        </div>
      </div>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = f"Stock Volatility Cloud <{gmail_user}>"
    msg["To"] = gmail_user
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, [gmail_user], msg.as_string())

    print(f"📧 已成功寄出預警郵件: {subject}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="GitHub Cloud Stock Volatility Monitor (3% / 6% / 10%)")
    parser.add_argument("--force", action="store_true", help="忽略開盤時段限制強制檢查全市場標的")
    args = parser.parse_args()

    state = load_state()
    alerts_map = state.setdefault("alerts", {})
    today_str = get_taipei_now().strftime("%Y-%m-%d")

    # 第一步：優先連結 Gmail 更新最新持股名單
    holdings = sync_holdings_from_gmail(state)

    tw_open = is_tw_market_open()
    us_open = is_us_market_open()
    print(f"[{get_taipei_now().strftime('%Y-%m-%d %H:%M:%S')}] 啟動 GitHub 雲端持股波動監測 (±3%/±6%/±10%) | 台股開盤={tw_open} | 美股開盤={us_open} | force={args.force}")

    for stock in holdings:
        market = stock["market"]
        is_open = tw_open if market == "TW" else us_open
        if not is_open and not args.force:
            print(f"[SKIP] {stock['name']} ({stock['symbol']}) 非開盤時段，跳過。")
            continue

        try:
            quote = fetch_quote_yahoo(stock["symbol"])
            abs_pct = quote["abs_change_pct"]
            rec_key = f"{stock['symbol']}:{today_str}"
            last_notified_abs = float(alerts_map.get(rec_key, 0.0))

            print(
                f"[QUOTE] {stock['name']} ({stock['symbol']}): 現價={quote['price']} | 昨收={quote['prev_close']} | "
                f"漲跌={quote['change_pct']:+.2f}% | 今日已通報最高波幅={last_notified_abs:.2f}%"
            )

            should_send = False
            tier_label = ""
            if abs_pct >= 10.0 and last_notified_abs < 10.0:
                should_send = True
                tier_label = "≥ 10% 劇烈波動"
            elif abs_pct >= 6.0 and last_notified_abs < 6.0:
                should_send = True
                tier_label = "≥ 6% 擴大波動"
            elif abs_pct >= 3.0 and last_notified_abs < 3.0:
                should_send = True
                tier_label = "≥ 3% 首次達標"

            if should_send:
                send_alert_email(stock, quote, tier_label)
                alerts_map[rec_key] = round(abs_pct, 2)
        except Exception as e:
            print(f"[ERROR] 檢查 {stock['symbol']} 發生錯誤: {e}", file=sys.stderr)

    save_state(state)


if __name__ == "__main__":
    main()
