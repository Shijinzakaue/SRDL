# SRDL

Showroom 直播下載工具（Windows）。

## 功能特色

- 自動偵測直播狀態並開始下載
- 支援「立刻下載」與「排程下載」
- 下載過程包含補抓機制，降低片段遺漏機率
- 關台後自動進入合併流程
- 以 `ffmpeg` 封裝為 MP4

## 系統需求

- Windows
- Python 3.9+
- `ffmpeg`（需可在終端執行）

> 你可以把 `ffmpeg.exe` 放在專案資料夾，或安裝後加入系統 PATH。

## 安裝

1. 下載本專案（ZIP 或 `git clone`）
2. 開啟專案資料夾終端
3. 安裝相依套件：

```bash
pip install -r requirements.txt
```

## 使用方式

### 方法 A：直接雙擊

- 執行 `啟動SRDL.bat`

### 方法 B：終端啟動

```bash
python SRDL.py
```

## 操作流程

1. 輸入 Showroom 直播網址
2. 選擇模式：
   - 立刻下載
   - 排程下載
3. 程式會在偵測到開台後開始抓取 `.ts` 片段
4. 結束後自動合併為 `.mp4`

## 輸出說明

- 下載與輸出檔案預設放在 `downloads/`
- 每場直播會建立獨立資料夾
- 正常合併完成後會保留 MP4

## 注意事項

- 啟動後請勿關閉執行中的終端視窗
- 可同時開多個終端視窗進行並行下載
- 網路不穩定時可能出現片段錯誤，工具會嘗試補抓
- 請遵守平台規範與相關法律，僅用於合法用途

## 卸載相依套件（可選）

```bash
pip uninstall flask requests beautifulsoup4 m3u8
```

## 專案檔案

- `SRDL.py`：主程式
- `啟動SRDL.bat`：Windows 快速啟動
- `requirements.txt`：Python 相依套件
- `downloads/`：下載與輸出目錄
