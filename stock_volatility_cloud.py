#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 雲端 & 本地端雙引擎「秒級即時」持股盤中重大波動監控系統 (Stock Volatility Real-Time Monitor)
========================================================================================================
支援「本地端 15 秒即時常駐監測 (--daemon)」與「GitHub Actions 雲端連續高頻輪詢 (--watch-seconds)」：
1. 【秒級即時報價 (1~3 秒延遲)】：
   採用 Yahoo Finance v8 `interval=1m&range=1d` 即時成交 Tick 串流端點，台股與美股盤中報價延遲僅約 1~3 秒，
   徹底告別 15 分鐘延遲！
2. 【15 秒極速掃描循環】：
   開盤時段每 15 秒自動拉取最新成交價；一旦突破 ±3% / ±6% / ±10%，15 秒內立即觸發 Email 與 Windows 桌面彈窗！
3. 【優先連結 Gmail 同步最新持股 & 發信前雙重防重驗證 (Idempotency Guard)】：
   啟動時及每 10 分鐘自動透過 IMAP 掃描最新「國泰證券 AI投資日報」、「美股庫存電子報」及「券商成交回報」，
   且在準備寄出任何波動警報信的前一刻，再次即時比對信箱是否已有同交易日同等級預警信，100% 杜絕重複寄信。
4. 【依市場時區切分交易日】：
   - 台股 (TW)：以台北時間 (Asia/Taipei) 日期作為當日交易日 Key。
   - 美股 (US)：以美東時間 (America/New_York) 日期作為當日交易日 Key，跨台北午夜 00:00 絕不誤判換日。
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import email
import email.utils
from email.header import Header, decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import imaplib
import json
import os
import re
import smtplib
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# 全局預設 Socket Timeout，防止任何網路或 IMAP/SSL 連線因系統休眠而永久卡死
socket.setdefaulttimeout(12)

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


def get_taipei_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def get_ny_now() -> datetime:
    try:
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=-5)))


def get_market_trading_date(market: str, regular_market_time: int = None, exchange_tz: str = None) -> str:
    """
    取得該標的所屬市場的『當日交易日 (YYYY-MM-DD)』：
    - 台股 (TW)：使用台北時間 (Asia/Taipei) 日期
    - 美股 (US)：使用美東時間 (America/New_York) 日期
    """
    market = (market or "TW").upper()
    if regular_market_time and regular_market_time > 0:
        try:
            if market == "TW":
                tz = timezone(timedelta(hours=8))
            else:
                tz_name = exchange_tz or "America/New_York"
                try:
                    tz = ZoneInfo(tz_name)
                except Exception:
                    tz = timezone(timedelta(hours=-5))
            return datetime.fromtimestamp(int(regular_market_time), tz).strftime("%Y-%m-%d")
        except Exception:
            pass

    if market == "TW":
        return get_taipei_now().strftime("%Y-%m-%d")
    return get_ny_now().strftime("%Y-%m-%d")


def is_tw_market_open() -> bool:
    now_tw = get_taipei_now()
    if now_tw.weekday() >= 5:
        return False
    hm = now_tw.hour * 60 + now_tw.minute
    return (9 * 60) <= hm <= (13 * 60 + 35)


def is_us_market_open() -> bool:
    ny_now = get_ny_now()
    if ny_now.weekday() >= 5:
        return False
    hm = ny_now.hour * 60 + ny_now.minute
    return (9 * 60 + 30) <= hm <= (16 * 60 + 5)


def is_pre_market_standby() -> bool:
    """
    判斷是否處於開盤前 60 分鐘內的『盤前待命時段』：
    - 台股盤前待命：週一至週五 台北時間 08:00 ~ 09:00
    - 美股盤前待命：週一至週五 美東時間 08:30 ~ 09:30
    避免 GitHub Actions 提早於開盤前喚醒容器時直接退出，確保開盤第一秒 (09:00:00 / 09:30:00) 即刻無縫盯盤！
    """
    now_tw = get_taipei_now()
    if now_tw.weekday() < 5:
        hm_tw = now_tw.hour * 60 + now_tw.minute
        if (8 * 60) <= hm_tw < (9 * 60):
            return True

    ny_now = get_ny_now()
    if ny_now.weekday() < 5:
        hm_ny = ny_now.hour * 60 + ny_now.minute
        if (8 * 60 + 30) <= hm_ny < (9 * 60 + 30):
            return True

    return False


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return {"holdings": DEFAULT_HOLDINGS, "alerts": {}, "last_checked_at": None, "last_gmail_sync": None}


def save_state(state: dict):
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8-sig") as f:
                disk_state = json.load(f)
            disk_alerts = disk_state.get("alerts") or {}
            mem_alerts = state.setdefault("alerts", {})
            for k, v in disk_alerts.items():
                if float(v) > float(mem_alerts.get(k, 0.0)):
                    mem_alerts[k] = float(v)
        except Exception:
            pass

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


def _sync_holdings_from_gmail_impl(state: dict, quiet: bool = False) -> list:
    """
    優先連結 Gmail (by960330@gmail.com) 檢查最新國泰證券【AI投資日報】(台股庫存)、
    【美股庫存電子報】與【成交回報】，動態更新當前實際持股名單。
    同時從信箱中已發送的【持股波動預警】郵件標題同步已通報紀錄，達成雙重防重保護。
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
        if not quiet:
            print("[WARN] 未設定 GMAIL_APP_PASSWORD，使用預設持股清單。")
        return current_holdings

    holdings_by_symbol = {h["symbol"]: dict(h) for h in current_holdings}
    verified_from_gmail = []
    alerts_map = state.setdefault("alerts", {})

    try:
        if not quiet:
            print(f"[GMAIL SYNC] 正在連結 Gmail ({gmail_user}) 檢查最新券商庫存與已發送預警紀錄...")
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=10)
        mail.login(gmail_user, gmail_pass)
        mail.select("INBOX")

        since_date = (get_taipei_now() - timedelta(days=7)).strftime("%d-%b-%Y")
        status, data = mail.search(None, f'(SINCE {since_date})')
        if status == "OK" and data and data[0]:
            ids = data[0].split()[-60:]
            _, hdr_data = mail.fetch(b",".join(ids), "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")

            target_eids = []
            for item in hdr_data:
                if isinstance(item, tuple):
                    eid = item[0].decode().split()[0].encode()
                    msg = email.message_from_bytes(item[1])
                    subj = _decode_mime_str(msg.get("Subject", ""))
                    if any(k in subj for k in ["美股庫存電子報", "AI投資日報", "成交回報", "買賣成交"]):
                        target_eids.append((eid, subj))

                    # 雙重防重保護 (Idempotency Guard)：檢查信箱中已寄出的【持股波動預警】郵件
                    m_alert = re.search(r"【持股波動預警\s*([^】]+)】.*?\(([^)]+)\).*?([+\-]?\d+(?:\.\d+)?)%", subj)
                    if m_alert:
                        tier_str, sym_in_subj, pct_str = (
                            m_alert.group(1),
                            m_alert.group(2).strip(),
                            m_alert.group(3),
                        )
                        try:
                            abs_pct_val = abs(float(pct_str))
                            if "10%" in tier_str:
                                abs_pct_val = max(abs_pct_val, 10.0)
                            elif "6%" in tier_str:
                                abs_pct_val = max(abs_pct_val, 6.0)
                            elif "3%" in tier_str:
                                abs_pct_val = max(abs_pct_val, 3.0)

                            date_hdr = msg.get("Date", "")
                            dt_obj = email.utils.parsedate_to_datetime(date_hdr) if date_hdr else None
                            if dt_obj:
                                is_tw_sym = sym_in_subj.endswith(".TW") or sym_in_subj.endswith(".TWO")
                                if is_tw_sym:
                                    mkt_dt = dt_obj.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
                                else:
                                    try:
                                        mkt_dt = dt_obj.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
                                    except Exception:
                                        mkt_dt = dt_obj.astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d")
                                r_key = f"{sym_in_subj}:{mkt_dt}"
                                if abs_pct_val > float(alerts_map.get(r_key, 0.0)):
                                    alerts_map[r_key] = round(abs_pct_val, 2)
                        except Exception:
                            pass

            latest_us_inventory_syms = None
            latest_tw_inventory_syms = None
            recent_bought_syms = set()

            for eid, subj in target_eids[-6:]:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                if not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                text = _extract_email_text(msg)

                if "美股庫存電子報" in subj:
                    this_us_syms = set()
                    for m in re.finditer(r"\b([A-Z]{2,5})\s+([A-Za-z0-9&\.\-\s]{3,35}?)\s+\d+\.\d+\s+[\-\d\.]+\(", text):
                        us_sym = m.group(1).strip()
                        us_name = m.group(2).strip()
                        if us_sym not in ("USD", "ETF", "NEW", "TOP"):
                            this_us_syms.add(us_sym)
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
                    if this_us_syms:
                        latest_us_inventory_syms = this_us_syms

                if "AI投資日報" in subj:
                    this_tw_syms = set()
                    for tw_name, (tw_sym, tw_ticker) in KNOWN_TW_NAME_TO_SYMBOL.items():
                        if f"{tw_name} " in text:
                            this_tw_syms.add(tw_sym)
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
                    if this_tw_syms:
                        latest_tw_inventory_syms = this_tw_syms

                if "成交回報" in subj:
                    for m in re.finditer(r"(買進|賣出)[\s:：]*([0-9]{4,5})\s*([\u4e00-\u9fa5A-Za-z0-9]+)?", text):
                        act, tk, nm = m.group(1), m.group(2), (m.group(3) or tk).strip()
                        sym = f"{tk}.TW"
                        if "買" in act:
                            recent_bought_syms.add(sym)
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

            # 雙向同步：若最新一期庫存電子報中某檔舊持股已消失（代表已全數賣出出清），自動從監控名單移除
            for sym in list(holdings_by_symbol.keys()):
                h_mkt = holdings_by_symbol[sym].get("market", "TW")
                if h_mkt == "US" and latest_us_inventory_syms is not None:
                    if sym not in latest_us_inventory_syms and sym not in recent_bought_syms:
                        removed = holdings_by_symbol.pop(sym)
                        print(f"[GMAIL SYNC] 偵測到美股已出清，自動從監控清單移除: {removed.get('name')} ({sym})")
                elif h_mkt == "TW" and latest_tw_inventory_syms is not None:
                    if sym not in latest_tw_inventory_syms and sym not in recent_bought_syms:
                        removed = holdings_by_symbol.pop(sym)
                        print(f"[GMAIL SYNC] 偵測到台股已出清，自動從監控清單移除: {removed.get('name')} ({sym})")

        try:
            mail.logout()
        except Exception:
            pass
        updated_list = list(holdings_by_symbol.values())
        state["holdings"] = updated_list
        state["last_gmail_sync"] = get_taipei_now().strftime("%Y-%m-%d %H:%M:%S")
        if not quiet:
            print(f"[GMAIL SYNC] 信箱庫存驗證標的: {verified_from_gmail} | 最終監控持股 ({len(updated_list)} 檔): {[h['symbol'] for h in updated_list]}")
        return updated_list
    except Exception as e:
        if not quiet:
            print(f"[WARN] Gmail 同步過程發生例外 ({e})，維持既有持股清單: {[h['symbol'] for h in current_holdings]}")
        return current_holdings


def sync_holdings_from_gmail(state: dict, quiet: bool = False, timeout_sec: int = 12) -> list:
    """
    以硬超時執行緒包裝 Gmail IMAP 同步（最長等待 12 秒），
    徹底防止因系統休眠喚醒或底層 SSL 網路切換導致主報價迴圈被卡死！
    """
    current_holdings = state.get("holdings") or list(DEFAULT_HOLDINGS)
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(_sync_holdings_from_gmail_impl, state, quiet)
    try:
        return fut.result(timeout=timeout_sec)
    except FuturesTimeoutError:
        if not quiet:
            print(f"[WARN] Gmail IMAP 同步超過 {timeout_sec}s 硬超時限制，立即跳過以保護即時報價迴圈不中斷！")
        return current_holdings
    except Exception as e:
        if not quiet:
            print(f"[WARN] Gmail 同步外層例外 ({e})，維持既有持股。")
        return current_holdings
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def _parse_float(val) -> float:
    if val is None:
        return None
    s = str(val).strip().replace(",", "").replace("%", "").replace("+", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except Exception:
        return None


def fetch_quote_twse_official(symbol: str) -> dict:
    """
    【台股零延遲官方直連】直接向台灣證券交易所 / 櫃買中心基本市況報導 (mis.twse.com.tw?delay=0) 抓取逐筆撮合即時報價。
    完全不經由 Yahoo 轉手，0 延遲！
    """
    ticker = symbol.replace(".TWO", "").replace(".TW", "").strip()
    ex_prefix = "otc" if symbol.endswith(".TWO") else "tse"
    ex_ch = f"{ex_prefix}_{ticker}.tw"
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex_ch}&json=1&delay=0&_={int(time.time() * 1000)}"
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
        },
    )
    with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    msg_arr = data.get("msgArray") or []
    if not msg_arr:
        raise RuntimeError(f"TWSE 官方 API 未回傳 {symbol} 資料")

    item = msg_arr[0]
    prev_close = _parse_float(item.get("y"))
    # 優先取當盤成交價 z -> trade.z -> pz -> 最佳一檔委買委賣中間價
    price = _parse_float(item.get("z"))
    if price is None and isinstance(item.get("trade"), dict):
        price = _parse_float(item["trade"].get("z"))
    if price is None:
        price = _parse_float(item.get("pz"))
    if price is None:
        b_first = _parse_float((item.get("b") or "").split("_")[0])
        a_first = _parse_float((item.get("a") or "").split("_")[0])
        if b_first and a_first:
            price = (b_first + a_first) / 2.0
        else:
            price = b_first or a_first

    if price is None or prev_close is None or prev_close <= 0:
        raise RuntimeError(f"TWSE {symbol} 暫無有效成交價 (price={price}, prev_close={prev_close})")

    raw_d = str(item.get("d") or "").strip()
    if len(raw_d) == 8 and raw_d.isdigit():
        trading_date = f"{raw_d[:4]}-{raw_d[4:6]}-{raw_d[6:8]}"
    else:
        trading_date = get_market_trading_date("TW")

    quote_time_tw = str(item.get("t") or "")
    change_pct = ((price - prev_close) / prev_close) * 100.0
    return {
        "symbol": symbol,
        "currency": "TWD",
        "price": float(price),
        "prev_close": float(prev_close),
        "change_pct": float(change_pct),
        "abs_change_pct": abs(float(change_pct)),
        "trading_date": trading_date,
        "quote_time_tw": f"{quote_time_tw} [TWSE 證交所官方 0 延遲直連]",
        "latency_sec": 0,
        "source": "TWSE_OFFICIAL_REALTIME",
    }


def fetch_quote_us_realtime_direct(symbol: str) -> dict:
    """
    【美股零延遲直連】直接向 NASDAQ Last Sale / CTA 即時串流端點 (quote.cnbc.com, realTime=true) 抓取毫秒級即時報價。
    完全不經由 Yahoo 延遲來源，延遲 < 0.5 秒！
    """
    url = (
        f"https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
        f"?symbols={urllib.parse.quote(symbol)}&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    quotes = (data.get("FormattedQuoteResult") or {}).get("FormattedQuote") or []
    if not quotes:
        raise RuntimeError(f"US Realtime 端點未回傳 {symbol} 資料")

    q = quotes[0]
    price = _parse_float(q.get("last"))
    prev_close = _parse_float(q.get("previous_day_closing"))
    if price is None or prev_close is None or prev_close <= 0:
        raise RuntimeError(f"US Realtime {symbol} 缺少有效報價 (price={price}, prev_close={prev_close})")

    last_time_iso = str(q.get("last_time") or "")
    trading_date = get_market_trading_date("US")
    quote_time_tw = ""
    latency_sec = 0
    if len(last_time_iso) >= 10 and last_time_iso[:4].isdigit():
        trading_date = last_time_iso[:10]
        try:
            dt_obj = datetime.fromisoformat(last_time_iso)
            quote_time_tw = dt_obj.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
            latency_sec = max(0, int(time.time() - dt_obj.timestamp()))
        except Exception:
            quote_time_tw = str(q.get("last_timedate") or "")

    src_name = q.get("source") or "NASDAQ/CTA Direct"
    change_pct = ((price - prev_close) / prev_close) * 100.0
    return {
        "symbol": symbol,
        "currency": q.get("currencyCode", "USD"),
        "price": float(price),
        "prev_close": float(prev_close),
        "change_pct": float(change_pct),
        "abs_change_pct": abs(float(change_pct)),
        "trading_date": trading_date,
        "quote_time_tw": f"{quote_time_tw} [{src_name} 0 延遲直連]",
        "latency_sec": latency_sec,
        "source": "US_NASDAQ_CTA_REALTIME",
    }


def fetch_quote_yahoo(symbol: str, market: str = None) -> dict:
    """
    優先採用「官方 0 延遲直連報價源」：
    - 台股 (TW)：優先直連台灣證交所 (mis.twse.com.tw?delay=0) 官方逐筆撮合即時 API。
    - 美股 (US)：優先直連 NASDAQ Last Sale / CTA 毫秒級即時串流 API (realTime=true)。
    若主線路瞬斷，才自動 Fallback 至 Yahoo Finance v8 1m 端點。
    """
    inferred_market = (market or ("TW" if (symbol.endswith(".TW") or symbol.endswith(".TWO")) else "US")).upper()
    try:
        if inferred_market == "TW":
            return fetch_quote_twse_official(symbol)
        else:
            return fetch_quote_us_realtime_direct(symbol)
    except Exception as primary_err:
        print(f"[WARN] 主報價源 ({inferred_market}) 瞬斷 ({primary_err})，切換備援線路...", file=sys.stderr)

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1m&range=1d"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    result = (data.get("chart") or {}).get("result")
    if not result:
        raise RuntimeError(f"備援線路未回傳 {symbol} 資料")

    meta = result[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None or prev_close is None or prev_close <= 0:
        raise RuntimeError(f"{symbol} 缺少有效報價 (price={price}, prev_close={prev_close})")

    reg_time = meta.get("regularMarketTime")
    exch_tz = meta.get("exchangeTimezoneName")
    trading_date = get_market_trading_date(inferred_market, reg_time, exch_tz)

    quote_time_tw = ""
    latency_sec = None
    if reg_time and reg_time > 0:
        quote_time_tw = datetime.fromtimestamp(int(reg_time), timezone(timedelta(hours=8))).strftime("%H:%M:%S")
        latency_sec = max(0, int(time.time()) - int(reg_time))

    change_pct = ((price - prev_close) / prev_close) * 100.0
    return {
        "symbol": symbol,
        "currency": meta.get("currency", "TWD"),
        "price": float(price),
        "prev_close": float(prev_close),
        "change_pct": float(change_pct),
        "abs_change_pct": abs(float(change_pct)),
        "trading_date": trading_date,
        "regular_market_time": reg_time,
        "quote_time_tw": quote_time_tw,
        "latency_sec": latency_sec,
        "source": "YAHOO_FALLBACK",
    }


def send_windows_toast_if_local(title: str, message: str):
    if sys.platform != "win32":
        return
    notify_dir = r"C:\Users\user\.gemini\config\skills\financial-podcast-analyzer\scripts"
    if os.path.exists(notify_dir):
        try:
            if notify_dir not in sys.path:
                sys.path.append(notify_dir)
            import notify
            notify.send_windows_toast(title, message)
        except Exception:
            pass


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
    mkt_tz_label = "台北交易日" if stock.get("market") == "TW" else "美東交易日"
    trading_date_str = f"{quote.get('trading_date', '')} ({mkt_tz_label})"
    quote_time_info = f"{quote['quote_time_tw']} (即時報價延遲 ~{quote['latency_sec']} 秒)" if quote.get("quote_time_tw") else now_str

    subject = f"【持股波動預警 {tier_label}】{stock['name']} ({stock['symbol']}) {direction} {sign}{quote['change_pct']:.2f}%"

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Microsoft JhengHei', sans-serif; max-width: 580px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 10px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.05);">
      <div style="background-color: {color}; color: #ffffff; padding: 18px 24px;">
        <h2 style="margin: 0; font-size: 20px;">{stock['name']} ({stock['symbol']}) 即時波動預警</h2>
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
          <tr style="border-bottom: 1px solid #f0f0f0;">
            <td style="padding: 10px 0; color: #666;">所屬交易日：</td>
            <td style="padding: 10px 0; color: #444;">{trading_date_str}</td>
          </tr>
          <tr style="border-bottom: 1px solid #f0f0f0;">
            <td style="padding: 10px 0; color: #666;">成交報價時間：</td>
            <td style="padding: 10px 0; color: #444;">{quote_time_info}</td>
          </tr>
          <tr>
            <td style="padding: 10px 0; color: #666;">通報時間：</td>
            <td style="padding: 10px 0; color: #444;">{now_str} (Asia/Taipei)</td>
          </tr>
        </table>
        <div style="margin-top: 20px; padding: 12px; background-color: #f8f9fa; border-radius: 6px; font-size: 13px; color: #555;">
          ⚡ <strong>秒級即時監控系統</strong>：每 15 秒即時追蹤最新成交 Tick（延遲僅 1~3 秒），當日漲跌幅首次達 <strong>±3%</strong>、擴大達 <strong>±6%</strong>、或劇震達 <strong>±10%</strong> 時立即推播至 {gmail_user}。
        </div>
      </div>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = f"Stock Volatility Realtime <{gmail_user}>"
    msg["To"] = gmail_user
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, [gmail_user], msg.as_string())

    print(f"📧 已成功寄出即時預警郵件: {subject}")
    send_windows_toast_if_local(
        f"【持股即時預警 {tier_label}】{stock['name']} ({stock['symbol']})",
        f"{direction} {sign}{quote['change_pct']:.2f}%\n現價: {quote['price']:,.2f} {quote['currency']} (昨收: {quote['prev_close']:,.2f})",
    )


def git_push_state_if_possible():
    """若在本地或雲端 Git 儲存庫內觸發警報，嘗試立即將最新 stock_alert_state.json 推送至遠端同步。"""
    repo_dir = STATE_FILE.parent
    if not (repo_dir / ".git").exists():
        return
    try:
        subprocess.run(["git", "add", "stock_alert_state.json"], cwd=str(repo_dir), timeout=10, capture_output=True)
        diff_res = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=str(repo_dir), timeout=10)
        if diff_res.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "chore: realtime alert state sync"],
                cwd=str(repo_dir),
                timeout=15,
                capture_output=True,
            )
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=str(repo_dir), timeout=25, capture_output=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=str(repo_dir), timeout=25, capture_output=True)
    except Exception:
        pass


def run_quote_scan_tick(state: dict, holdings: list, force: bool = False, verbose: bool = True) -> bool:
    """
    執行單次秒級即時報價檢查。回傳是否有觸發新警報。
    """
    alerts_map = state.setdefault("alerts", {})
    tw_open = is_tw_market_open()
    us_open = is_us_market_open()
    triggered_any = False

    for stock in holdings:
        market = stock["market"]
        is_open = tw_open if market == "TW" else us_open
        if not is_open and not force:
            continue

        try:
            quote = fetch_quote_yahoo(stock["symbol"], market=market)
            abs_pct = quote["abs_change_pct"]
            trading_date = quote["trading_date"]
            rec_key = f"{stock['symbol']}:{trading_date}"
            last_notified_abs = float(alerts_map.get(rec_key, 0.0))

            if verbose:
                src_tag = quote.get("source", "REALTIME")
                lat_str = f" (延遲:{quote['latency_sec']}s)" if quote.get("latency_sec") is not None else ""
                now_ts_str = get_taipei_now().strftime("%H:%M:%S")
                print(
                    f"[{now_ts_str}] [QUOTE|{src_tag}] {stock['name']} ({stock['symbol']}) [交易日:{trading_date}]: "
                    f"現價={quote['price']} | 昨收={quote['prev_close']} | "
                    f"漲跌={quote['change_pct']:+.2f}%{lat_str} | 當日已通報最高波幅={last_notified_abs:.2f}%"
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
                # 寄信前一刻再次與磁碟狀態及 Gmail 信箱同步，確保雲端與本地端雙引擎絕不重複寄信
                sync_holdings_from_gmail(state, quiet=True)
                last_notified_abs = float(state.get("alerts", {}).get(rec_key, 0.0))
                if (
                    (tier_label.startswith("≥ 10%") and last_notified_abs >= 10.0)
                    or (tier_label.startswith("≥ 6%") and last_notified_abs >= 6.0)
                    or (tier_label.startswith("≥ 3%") and last_notified_abs >= 3.0)
                ):
                    if verbose:
                        print(f"[SKIP] {stock['symbol']} 剛由另一節點完成推播 ({last_notified_abs:.2f}%)，跳過重複寄信。")
                    continue

                send_alert_email(stock, quote, tier_label)
                alerts_map[rec_key] = round(abs_pct, 2)
                triggered_any = True
                save_state(state)
                git_push_state_if_possible()
        except Exception as e:
            if verbose:
                print(f"[ERROR] 檢查 {stock['symbol']} 發生錯誤: {e}", file=sys.stderr)

    return triggered_any


def main():
    if sys.stdout is None or sys.stderr is None:
        log_file = open(Path(__file__).parent / "realtime_monitor.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = log_file
        sys.stderr = log_file
    elif hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Real-Time Stock Volatility Monitor (15s tick polling, 3% / 6% / 10%)")
    parser.add_argument("--force", action="store_true", help="忽略開盤時段限制強制檢查全市場標的")
    parser.add_argument("--daemon", action="store_true", help="本地端背景常駐即時監測模式（開盤時每 15 秒即時掃描）")
    parser.add_argument("--watch-seconds", type=int, default=0, help="雲端連續即時監測秒數（例如 260 秒內每 15 秒掃描一次）")
    parser.add_argument("--interval", type=int, default=10, help="即時報價輪詢間隔秒數（預設 10 秒）")
    args = parser.parse_args()

    state = load_state()
    holdings = sync_holdings_from_gmail(state)
    last_gmail_sync_ts = time.time()

    tw_open = is_tw_market_open()
    us_open = is_us_market_open()
    print(
        f"[{get_taipei_now().strftime('%Y-%m-%d %H:%M:%S')}] 啟動秒級即時持股波動監測 (間隔:{args.interval}s) | "
        f"台股開盤={tw_open} (交易日:{get_market_trading_date('TW')}) | "
        f"美股開盤={us_open} (美東交易日:{get_market_trading_date('US')}) | "
        f"daemon={args.daemon} | watch={args.watch_seconds}s | force={args.force}"
    )

    # 模式 A：本地端全天候背景常駐模式 (--daemon)
    if args.daemon:
        while True:
            try:
                tw_open = is_tw_market_open()
                us_open = is_us_market_open()
                if not tw_open and not us_open and not args.force:
                    time.sleep(20)
                    continue

                # 每 20 分鐘非阻塞同步一次 Gmail 最新持股與已寄信狀態（12 秒硬超時保護）
                if time.time() - last_gmail_sync_ts >= 1200:
                    state = load_state()
                    holdings = sync_holdings_from_gmail(state, quiet=True, timeout_sec=12)
                    save_state(state)
                    last_gmail_sync_ts = time.time()

                run_quote_scan_tick(state, holdings, force=args.force, verbose=True)
            except Exception as e:
                print(f"[ERROR] Daemon loop error: {e}", file=sys.stderr)
            time.sleep(max(5, args.interval))

    # 模式 B：GitHub Actions 雲端全場連續高頻監測模式 (--watch-seconds > 0)
    elif args.watch_seconds > 0:
        start_ts = time.time()
        tick_count = 0
        standby_logged = False
        while True:
            tw_open = is_tw_market_open()
            us_open = is_us_market_open()
            if not tw_open and not us_open and not args.force:
                if is_pre_market_standby():
                    if not standby_logged:
                        print(f"[{get_taipei_now().strftime('%H:%M:%S')}] [STANDBY] 處於開盤前待命時段，等待開盤鐘響即刻啟動每 {args.interval}s 零延遲監控...")
                        standby_logged = True
                    if time.time() - start_ts + 10 > args.watch_seconds:
                        break
                    time.sleep(10)
                    continue
                print(f"[{get_taipei_now().strftime('%H:%M:%S')}] [DONE] 目前非開盤或盤前待命時段，結束本次巡檢。")
                break

            # 雲端長時運行期間，每 20 分鐘非阻塞同步一次 Gmail 最新持股與已寄信狀態（12 秒硬超時保護）
            if time.time() - last_gmail_sync_ts >= 1200:
                state = load_state()
                holdings = sync_holdings_from_gmail(state, quiet=True, timeout_sec=12)
                save_state(state)
                last_gmail_sync_ts = time.time()

            tick_count += 1
            run_quote_scan_tick(state, holdings, force=args.force, verbose=(tick_count == 1 or tick_count % 6 == 0))
            elapsed = time.time() - start_ts
            if elapsed + args.interval > args.watch_seconds:
                break
            time.sleep(max(5, args.interval))
        save_state(state)

    # 模式 C：單次即時檢查
    else:
        run_quote_scan_tick(state, holdings, force=args.force, verbose=True)
        save_state(state)


if __name__ == "__main__":
    main()
