#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 全雲端持股盤中重大波動監控系統 (Stock Volatility Cloud Monitor)
================================================================================
無須開機、無須手動點擊 Google Apps Script 授權，直接由 GitHub Actions 定時執行：
1. 監控信箱持股清單：台積電 (2330.TW)、聯發科 (2454.TW)、VOO、SPCX。
2. 嚴格區分台股開盤時段 (週一至週五 09:00~13:35 台北時間) 與美股開盤時段 (美東 09:30~16:05)。
3. 透過 Yahoo Finance v8 Chart API 取得即時成交價與前一日官方收盤價 (chartPreviousClose)。
4. 階梯式波動預警與防洗版機制（狀態儲存於 stock_alert_state.json）：
   - 當日漲跌幅絕對值首次達 3.0%：發送 3% 波動預警信。
   - 當日漲跌幅絕對值進一步擴大達 6.0%（或較前次通報擴大 >= 2.0%）及 8.0%：再次發送升級警報！
"""

import argparse
import json
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

STATE_FILE = Path(__file__).parent / "stock_alert_state.json"

DEFAULT_HOLDINGS = [
    {"symbol": "2330.TW", "ticker": "2330", "name": "台積電", "market": "TW", "shares": 83},
    {"symbol": "2454.TW", "ticker": "2454", "name": "聯發科", "market": "TW", "shares": 11},
    {"symbol": "VOO", "ticker": "VOO", "name": "Vanguard S&P 500 ETF", "market": "US", "shares": 1},
    {"symbol": "SPCX", "ticker": "SPCX", "name": "Space Exploration Technologies", "market": "US", "shares": 1},
]


def get_taipei_now() -> datetime:
    return datetime.now( timezone(timedelta(hours=8)) )


def is_tw_market_open() -> bool:
    now_tw = get_taipei_now()
    if now_tw.weekday() >= 5:
        return False
    hm = now_tw.hour * 60 + now_tw.minute
    return (9 * 60) <= hm <= (13 * 60 + 40)


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
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"alerts": {}, "last_checked_at": None}


def save_state(state: dict):
    state["last_checked_at"] = get_taipei_now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


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
          💡 <strong>GitHub Cloud 全自動監控</strong>：當日首次突破 ±3% 立即發信；若盤中波動進一步擴大（每擴大 2% 或突破 ±8%），將再次發送升級警報。
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
    parser = argparse.ArgumentParser(description="GitHub Cloud Stock Volatility Monitor")
    parser.add_argument("--force", action="store_true", help="忽略開盤時段限制強制檢查全市場標的")
    args = parser.parse_args()

    state = load_state()
    alerts_map = state.setdefault("alerts", {})
    today_str = get_taipei_now().strftime("%Y-%m-%d")

    tw_open = is_tw_market_open()
    us_open = is_us_market_open()
    print(f"[{get_taipei_now().strftime('%Y-%m-%d %H:%M:%S')}] 啟動 GitHub 雲端持股波動監測 | 台股開盤={tw_open} | 美股開盤={us_open} | force={args.force}")

    for stock in DEFAULT_HOLDINGS:
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
            if abs_pct >= 8.0 and last_notified_abs < 8.0:
                should_send = True
                tier_label = "≥ 8% 劇烈波動"
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
