# SRDL

Showroom 直播下載工具（Windows）。

## 功能特色

- 自動偵測直播狀態並開始下載
- 支援「立刻下載」與「排程下載」
- 支援 `LOM` 模式（讀取 `config.txt` 批次輪詢）
- 已解析過的網址會快取 `room_id`，避免重複打開直播頁面
- 自動讀取平台排程並換算本地時間
- 雙執行緒下載，含補抓機制降低片段遺漏
- 關台後自動輪詢，偵測重新開台
- 以 `ffmpeg` 封裝為 MP4

## 系統需求

- Windows
- Python 3.9+
- `ffmpeg`（需可在終端執行，或將 `ffmpeg.exe` 放在腳本同目錄）

## 安裝

1. 下載本專案（ZIP 或 `git clone`）
2. 開啟專案資料夾終端
3. 安裝相依套件：

```
pip install -r requirements.txt
```

## 使用方式

### 方法 A：直接雙擊

執行 `啟動SRDL.bat`

### 方法 B：終端啟動

```
python SRDL.py
```

## 操作流程

1. 輸入 Showroom 直播間網址（或輸入 `LOM`）
2. 選擇模式：
   - **立刻下載**：偵測到開台後立即開始
   - **排程下載**：自動讀取平台排程（或手動輸入時間），到時間前倒數等待
3. 程式偵測到開台後開始抓取 `.ts` 片段
4. 結束後自動合併為 `.mp4`

### LOM 模式

- 在啟動後輸入 `LOM`（不分大小寫）可進入清單輪詢模式。
- `config.txt` 每行一個網址，可使用空行與 `#` 註解。
- 程式會優先使用 `lom_cache.json` 內已保存的 `url_to_room` 對應；只有新網址才會重新解析 `room_id`。
- LOM 會每分鐘檢查一次每個 `room_id` 的開播狀態。
- 若同時多台開播，程式會自動並行下載（預設同時最多 3 台）。
- 輪詢會持續執行，直到手動按 `Ctrl+C` 停止。
- 會建立 `lom_cache.json`，保存 `url_to_room`、目前清單對應的 `rooms` 狀態，以及 `failed_rooms` 失敗清單。

> 目前併發上限寫在 `SRDL.py` 常數 `LOM_MAX_CONCURRENT_DOWNLOADS`，可依網路與硬碟效能調整。

## 輸出說明

- 檔案預設儲存於 `downloads/`
- 每場直播建立獨立資料夾，命名格式：`YYMMDDhhmm_直播間名稱`
- LOM 模式下載時，資料夾名稱會額外附上 `room_id`，避免不同直播間同名時互相衝突
- 合併成功後自動刪除 `.ts` 暫存片段，保留 `.mp4`
- 每場下載會產生 `log.log` 紀錄下載與錯誤資訊

## 注意事項

- 啟動後請勿關閉終端視窗
- 單網址模式仍可用原本方式下載單一直播間
- LOM 模式可在單一終端內自動並行下載多個直播間
- 網路不穩時工具會自動補抓遺漏片段
- 請遵守平台規範與相關法律，僅用於合法用途

## 相依套件

| 套件 | 用途 |
|------|------|
| `requests` | HTTP 請求（API、網頁、下載 ts 片段） |
| `beautifulsoup4` | 解析網頁取得 room_id 與標題 |
| `m3u8` | 解析 HLS 播放清單取得片段列表 |

## 專案檔案

- `SRDL.py`：主程式
- `啟動SRDL.bat`：Windows 快速啟動
- `config.txt`：LOM 模式輪詢清單（每行一個網址）
- `lom_cache.json`：LOM 輪詢快取（保存 `url_to_room`、`rooms`、`failed_rooms`）；首次執行前不存在，正常現象
- `requirements.txt`：Python 相依套件清單
- `downloads/`：下載與輸出目錄
- `__pycache__/`：Python 自動產生的編譯快取，刪除不影響功能（下次執行時會重新產生）
