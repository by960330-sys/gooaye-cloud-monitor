#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gooaye Podcast 全自動雲端監控、Gemini 4 Argon 買方深度分析與 Google Drive 結構化歸檔系統
====================================================================================
設計用於 GitHub Actions 無伺服器環境（亦支援本地執行）：
1. 自動巡邏 SoundOn 官方 RSS Feed 取得最新一集《股癌 Gooaye》。
2. 自動連線 Google Drive 檢查「Gooaye筆記整理 (1dJpinDTojk7X2EfVVGyjfg_wwzNvx01m)」下
   對應區間（如 EP701～EP710/EP709）是否已存在該集筆記，避免重複消耗 Token。
3. 發現新集數時，自動高速下載完整 MP3 音訊。
4. 透過 google-genai SDK (>=2.30.1) 將音訊上傳至 Files API，並調用旗艦模型
   `gemini-4-argon` 產出機構買方（Buy-Side）規格之四大維度深度研究報告。
5. 自動在 Google Drive 建立/定位 `EP{start}～EP{end}/EP{ep_num}` 資料夾，
   將 `股癌EP.{ep_num}.md` 報告與 `EP{ep_num}.mp3` 音訊直接上傳歸檔。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

RSS_URL = "https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml"
DEFAULT_ROOT_FOLDER_ID = "1dJpinDTojk7X2EfVVGyjfg_wwzNvx01m"  # Gooaye筆記整理
STATE_FILE = Path(__file__).parent / "monitor_state.json"
LOCAL_GDRIVE_TOKEN_PATH = Path(r"C:\Users\user\.gemini\antigravity\plugin_data\gdrive\token.json")

SYSTEM_INSTRUCTION = """你是一位頂尖機構法人（買方 Buy-Side）的首席科技與總經研究總監，擅長運用旗艦級模型（Gemini 4 Argon）的深度推理與產業架構推演能力，針對《股癌 Gooaye》Podcast 節目產出具備法人買方研究員規格的深度分析報告。

【核心篩選與分析準則】
1. 嚴格過濾雜訊：
   - 直接略過節目開頭的廠商業配與贊助廣告（如床墊、保健食品、VPN 等宣傳詞）。
   - 略過純粹的個人家庭日常閒聊、旅遊瑣事。
   - 略過聽眾 QA 中純屬感情糾紛、家庭婆媳、祝賀生日、求職祝福或無關財經的搞笑留言。
   - 若生活體驗涉及上市公司產品技術、產業趨勢、法規限制或商業模式觀察，請精準提取其中的產業面與商業邏輯。

2. 全面掃描並深度提取「觀眾提問 (Q&A)」中的股票與財經乾貨：
   - 絕不可忽略後半段 QA 環節。
   - 完整提取個股與產業基本面（市佔率消長、毛利率、新開案進度、漲價空間、競爭護城河、PE/EPS 估值）、籌碼面與市場結構、交易策略與抄底心法、資產配置與槓桿控管。

3. 報告結構（請嚴格按以下四大維度輸出繁體中文 Markdown 深度報告）：
   - 開頭標題：`# 股癌 EP.{集數}《{節目標題}》財經與產業深度分析報告`（註明分析模型為 Gemini 4 Argon 旗艦深度推理）
   - **第一部分：盤勢結構、總經與籌碼面分析**（全球主要市場位階、市場廣度與資金流向、籌碼面訊號與投資心理）
   - **第二部分：產業面與個股基本面深度拆解**（依產業板塊分類，詳列節目中提到的每一家公司/標的之基本面現況、法說會指引、供需缺口、技術演進與估值水位）
   - **第三部分：主持人的核心看法與市場觀點**（彙整核心警訊與長線趨勢判斷，並必須包含一張結構清晰的 **Markdown 對照總表**）
   - **第四部分：主持人的實戰操作策略與部位管理（含 QA 交易問答深度拆解）**（部位水位與加減碼邏輯、停利停損與風控原則、以及聽眾 QA 關鍵實戰問答逐題深度剖析）
"""


def get_taipei_now_str() -> str:
    tz_tw = timezone(timedelta(hours=8))
    return datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M:%S")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] 無法讀取狀態檔: {e}", file=sys.stderr)
    return {"last_processed_ep": 708, "processed_episodes": [706, 707, 708], "last_checked_at": None}


def save_state(state: dict):
    state["last_checked_at"] = get_taipei_now_str()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============================================================================
# Google Drive REST API v3 Client (Pure Python, OAuth2 Refresh Token)
# ============================================================================
class GoogleDriveClient:
    def __init__(self):
        token_json_str = os.environ.get("GDRIVE_TOKEN_JSON", "").strip()
        if token_json_str:
            tok_data = json.loads(token_json_str)
        elif LOCAL_GDRIVE_TOKEN_PATH.exists():
            with open(LOCAL_GDRIVE_TOKEN_PATH, "r", encoding="utf-8") as f:
                tok_data = json.load(f)
        else:
            raise RuntimeError("未設定 GDRIVE_TOKEN_JSON 環境變數，且找不到本地 token.json。")

        self.client_id = tok_data["client_id"]
        self.client_secret = tok_data["client_secret"]
        self.refresh_token = tok_data["token"]["refresh_token"]
        self.access_token = self._refresh_access_token()

    def _refresh_access_token(self) -> str:
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }).encode("utf-8")
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        return res["access_token"]

    def list_children(self, parent_id: str) -> list:
        q = urllib.parse.quote(f"'{parent_id}' in parents and trashed = false")
        url = f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id,name,mimeType)&pageSize=100"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.access_token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("files", [])

    def find_subfolder(self, parent_id: str, folder_name: str):
        for item in self.list_children(parent_id):
            if item.get("name") == folder_name and item.get("mimeType") == "application/vnd.google-apps.folder":
                return item["id"]
        return None

    def get_or_create_subfolder(self, parent_id: str, folder_name: str) -> str:
        existing_id = self.find_subfolder(parent_id, folder_name)
        if existing_id:
            return existing_id

        meta = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        data = json.dumps(meta).encode("utf-8")
        req = urllib.request.Request(
            "https://www.googleapis.com/drive/v3/files?fields=id",
            data=data,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            created = json.loads(resp.read().decode("utf-8"))
        return created["id"]

    def find_file_in_folder(self, folder_id: str, file_name: str):
        for item in self.list_children(folder_id):
            if item.get("name") == file_name and item.get("mimeType") != "application/vnd.google-apps.folder":
                return item["id"]
        return None

    def upload_or_update_file(self, folder_id: str, file_path: Path, mime_type: str = "application/octet-stream") -> dict:
        file_name = file_path.name
        existing_id = self.find_file_in_folder(folder_id, file_name)
        file_bytes = file_path.read_bytes()

        # Use resumable upload for reliability with both small markdown and large MP3 files
        if existing_id:
            init_url = f"https://www.googleapis.com/upload/drive/v3/files/{existing_id}?uploadType=resumable"
            method = "PATCH"
            meta = {"name": file_name}
        else:
            init_url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
            method = "POST"
            meta = {"name": file_name, "parents": [folder_id]}

        meta_bytes = json.dumps(meta).encode("utf-8")
        init_req = urllib.request.Request(
            init_url,
            data=meta_bytes,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json; charset=utf-8",
                "X-Upload-Content-Type": mime_type,
                "X-Upload-Content-Length": str(len(file_bytes)),
            },
            method=method,
        )
        with urllib.request.urlopen(init_req, timeout=60) as init_resp:
            upload_url = init_resp.headers.get("Location")

        put_req = urllib.request.Request(
            upload_url,
            data=file_bytes,
            headers={
                "Content-Length": str(len(file_bytes)),
                "Content-Type": mime_type,
            },
            method="PUT",
        )
        with urllib.request.urlopen(put_req, timeout=300) as put_resp:
            res_data = json.loads(put_resp.read().decode("utf-8"))

        return {
            "action": "updated" if existing_id else "uploaded",
            "file_name": file_name,
            "id": res_data.get("id", existing_id),
        }


# ============================================================================
# RSS Feed & Audio Download
# ============================================================================
def compute_range_name(ep_num: int) -> str:
    start = ((ep_num - 1) // 10) * 10 + 1
    end = start + 9
    return f"EP{start}～EP{end}"


def fetch_latest_rss() -> dict:
    req = urllib.request.Request(RSS_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml_data = resp.read()

    root = ET.fromstring(xml_data)
    for item in root.findall("./channel/item"):
        title = item.findtext("title") or ""
        pub_date = item.findtext("pubDate") or ""
        enclosure = item.find("enclosure")
        audio_url = enclosure.attrib.get("url") if enclosure is not None else None

        match = re.search(r"EP\s*(\d+)", title, re.IGNORECASE)
        if match and audio_url:
            ep_num = int(match.group(1))
            return {
                "episode_number": ep_num,
                "title": title.strip(),
                "pub_date": pub_date.strip(),
                "audio_url": audio_url.strip(),
            }
    raise RuntimeError("無法從 SoundOn RSS 解析出最新的股癌集數資訊。")


def fetch_specific_ep_rss(target_ep: int) -> dict:
    req = urllib.request.Request(RSS_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml_data = resp.read()

    root = ET.fromstring(xml_data)
    for item in root.findall("./channel/item"):
        title = item.findtext("title") or ""
        pub_date = item.findtext("pubDate") or ""
        enclosure = item.find("enclosure")
        audio_url = enclosure.attrib.get("url") if enclosure is not None else None
        match = re.search(r"EP\s*(\d+)", title, re.IGNORECASE)
        if match and int(match.group(1)) == target_ep and audio_url:
            return {
                "episode_number": target_ep,
                "title": title.strip(),
                "pub_date": pub_date.strip(),
                "audio_url": audio_url.strip(),
            }
    raise RuntimeError(f"在 RSS Feed 中找不到 EP.{target_ep}。")


def download_audio(audio_url: str, ep_num: int, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    mp3_path = out_dir / f"EP{ep_num}.mp3"
    if mp3_path.exists() and mp3_path.stat().st_size > 1_000_000:
        print(f"[INFO] 音訊檔案已存在: {mp3_path} ({mp3_path.stat().st_size / 1024 / 1024:.2f} MB)")
        return mp3_path

    print(f"[INFO] 正在下載 EP.{ep_num} 音訊: {audio_url}")
    req = urllib.request.Request(audio_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as resp, open(mp3_path, "wb") as out_f:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out_f.write(chunk)

    size_mb = mp3_path.stat().st_size / 1024 / 1024
    print(f"[INFO] 下載完成: {mp3_path} ({size_mb:.2f} MB)")
    return mp3_path


# ============================================================================
# Gemini 4 Argon Deep Analysis (with Automatic Multi-Model Fallback)
# ============================================================================
MODEL_PRIORITY_CHAIN = [
    "gemini-4-argon",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
]


def optimize_audio_for_gemini(mp3_path: Path) -> Path:
    """將音訊最佳化為 16kHz 單聲道 32kbps MP3（大幅降低上傳體積與推論延遲，保留完整語音細節）。"""
    opt_path = mp3_path.parent / f"{mp3_path.stem}_opt16k.mp3"
    if opt_path.exists() and opt_path.stat().st_size > 500_000:
        return opt_path

    ffmpeg_bin = "ffmpeg"
    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass

    import subprocess
    try:
        subprocess.run(
            [ffmpeg_bin, "-y", "-i", str(mp3_path), "-ac", "1", "-ar", "16000", "-b:a", "32k", str(opt_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        if opt_path.exists() and opt_path.stat().st_size > 500_000:
            print(f"[INFO] 音訊已最佳化為 16kHz 單聲道: {opt_path.name} ({opt_path.stat().st_size / 1024 / 1024:.2f} MB)")
            return opt_path
    except Exception as e:
        print(f"[WARN] 音訊最佳化略過，直接使用原檔 ({e})")
    return mp3_path


def analyze_audio_with_argon(mp3_path: Path, ep_num: int, title: str) -> str:
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未設定 GEMINI_API_KEY 環境變數！請在 GitHub Secrets 中設定 GEMINI_API_KEY。")

    upload_target = optimize_audio_for_gemini(mp3_path)
    client = genai.Client(api_key=api_key)

    print(f"[INFO] 正在上傳音訊至 Gemini Files API: {upload_target.name} ...")
    uploaded_file = client.files.upload(file=str(upload_target))
    print(f"[INFO] 音訊上傳成功，URI: {uploaded_file.uri}")

    # Wait briefly if file is still processing
    for _ in range(30):
        f_info = client.files.get(name=uploaded_file.name)
        state_str = str(getattr(f_info, "state", "")).upper()
        if "PROCESSING" not in state_str:
            break
        time.sleep(2)

    user_prompt = f"""請完整聆聽這集《股癌 Gooaye》Podcast 音訊：
- 集數：EP.{ep_num}
- 標題：{title}

請嚴格依照系統指令（financial-podcast-analyzer 買方研究總監規範）：
1. 過濾開頭業配廣告與無關財經之家庭日常閒聊。
2. 完整提取主軸討論與後半段「觀眾提問 (Q&A)」中所有關於股票、財經、產業趨勢、公司基本面（營收、毛利、市佔率、開案進度、法說會指引、估值）、籌碼面以及主持人的實戰交易策略與部位控管心法。
3. 產出結構嚴謹、邏輯透徹、具備機構法人買方深度研究報告水準的繁體中文 Markdown 報告（以 `# 股癌 EP.{ep_num} 財經與產業深度分析報告` 為開頭標題）。"""

    try:
        last_err = None
        for model_name in MODEL_PRIORITY_CHAIN:
            print(f"[INFO] 正在嘗試調用模型 {model_name} 進行深度多模態推理分析...")
            payload = {
                "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "fileData": {
                                    "fileUri": uploaded_file.uri,
                                    "mimeType": uploaded_file.mime_type or "audio/mpeg",
                                }
                            },
                            {"text": user_prompt},
                        ],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                },
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
                with urllib.request.urlopen(req, timeout=240) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                candidates = resp_json.get("candidates", [])
                parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
                report_text = "".join(p.get("text", "") for p in parts if "text" in p).strip()
                if report_text and len(report_text) >= 200:
                    print(f"[INFO] 模型 {model_name} 深度報告生成完成（共 {len(report_text)} 字元）！")
                    return report_text
                raise RuntimeError(f"模型 {model_name} 回傳內容過短 ({len(report_text)} 字元)")
            except urllib.error.HTTPError as he:
                err_body = he.read().decode("utf-8", errors="ignore")[:300]
                print(f"[WARN] 模型 {model_name} 回應 HTTP {he.code}: {err_body}，自動切換下一順位模型...")
                last_err = f"HTTP {he.code}: {err_body}"
            except Exception as ex:
                print(f"[WARN] 模型 {model_name} 執行異常 ({ex})，自動切換下一順位模型...")
                last_err = str(ex)

        raise RuntimeError(f"所有備援模型皆無法完成分析，最後錯誤：{last_err}")
    finally:
        try:
            client.files.delete(name=uploaded_file.name)
            print("[INFO] 已清理 Gemini 暫存音訊檔。")
        except Exception:
            pass


# ============================================================================
# Main Orchestration
# ============================================================================
def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gooaye Cloud Auto-Monitor & Gemini 4 Argon Analyzer")
    parser.add_argument("--force-ep", type=int, default=None, help="強制指定分析特定集數（例如 708）")
    parser.add_argument("--root-id", default=DEFAULT_ROOT_FOLDER_ID, help="Google Drive 根目錄 ID（預設：Gooaye筆記整理）")
    args = parser.parse_args()

    state = load_state()
    processed_list = state.get("processed_episodes", [])
    last_ep = state.get("last_processed_ep", 0)

    print(f"[{get_taipei_now_str()}] 啟動股癌 Podcast 雲端自動巡邏...")
    meta = fetch_specific_ep_rss(args.force_ep) if args.force_ep else fetch_latest_rss()
    ep_num = meta["episode_number"]
    title = meta["title"]
    print(f"[INFO] 偵測到集數：EP.{ep_num} | 標題：{title} | 發布時間：{meta['pub_date']}")

    drive = GoogleDriveClient()
    range_name = compute_range_name(ep_num)
    ep_folder_name = f"EP{ep_num}"
    report_filename = f"股癌EP.{ep_num}.md"

    # Check if already present in Google Drive (unless --force-ep is explicitly set)
    if not args.force_ep:
        range_folder_id = drive.find_subfolder(args.root_id, range_name)
        if range_folder_id:
            ep_folder_id = drive.find_subfolder(range_folder_id, ep_folder_name)
            if ep_folder_id:
                existing_report_id = drive.find_file_in_folder(ep_folder_id, report_filename) or drive.find_file_in_folder(ep_folder_id, f"股癌 EP.{ep_num}.md")
                if existing_report_id:
                    print(f"✅ Google Drive [Gooaye筆記整理/{range_name}/{ep_folder_name}] 中已存在 EP.{ep_num} 筆記 (ID: {existing_report_id})，無須重複執行。")
                    if ep_num not in processed_list:
                        processed_list.append(ep_num)
                    state["last_processed_ep"] = max(last_ep, ep_num)
                    state["processed_episodes"] = sorted(list(set(processed_list)))
                    save_state(state)
                    return

        if ep_num <= last_ep and ep_num in processed_list:
            print(f"✅ EP.{ep_num} 已在已處理清單中 (最後處理: EP.{last_ep})，無新集數。")
            save_state(state)
            return

    print(f"🎉 準備開始處理新集數：EP.{ep_num} - {title}")
    work_dir = Path(__file__).parent / "work_tmp"
    mp3_path = download_audio(meta["audio_url"], ep_num, work_dir)

    report_content = analyze_audio_with_argon(mp3_path, ep_num, title)
    report_path = work_dir / report_filename
    report_path.write_text(report_content, encoding="utf-8")

    print(f"[INFO] 正在將報告與音訊結構化歸檔至 Google Drive: Gooaye筆記整理 -> {range_name} -> {ep_folder_name} ...")
    range_folder_id = drive.get_or_create_subfolder(args.root_id, range_name)
    ep_folder_id = drive.get_or_create_subfolder(range_folder_id, ep_folder_name)

    rep_res = drive.upload_or_update_file(ep_folder_id, report_path, mime_type="text/markdown; charset=utf-8")
    aud_res = drive.upload_or_update_file(ep_folder_id, mp3_path, mime_type="audio/mpeg")

    if ep_num not in processed_list:
        processed_list.append(ep_num)
    state["last_processed_ep"] = max(last_ep, ep_num)
    state["processed_episodes"] = sorted(list(set(processed_list)))
    save_state(state)

    summary = {
        "status": "SUCCESS",
        "episode_number": ep_num,
        "title": title,
        "drive_path": f"Gooaye筆記整理/{range_name}/{ep_folder_name}",
        "ep_folder_url": f"https://drive.google.com/drive/folders/{ep_folder_id}",
        "report": rep_res,
        "audio": aud_res,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
