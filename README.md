# 日本房地產海外查詢站（SCLAW）

這是一套可直接落地的內容生產與查詢系統，目標是服務全球華人（重點：中國、香港、東南亞）查詢「外地購買日本房地產」資訊。

核心能力：
- 抓取日本公開來源（官方 + 主流平台首頁連結標題）
- 自動翻譯（日文/其他語言 -> 簡體中文）並轉繁體
- 內容重整（避免原文照貼）
- 產生 SEO 欄位與 JSON-LD Schema
- 提供站內查詢 API / 前端查詢介面
- 匯出 WordPress 可匯入 CSV（適用 WP All Import）

---

## 1) 快速啟動

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

執行資料管線：

```bash
python scripts/run_pipeline.py
python scripts/export_wordpress_csv.py
```

啟動查詢站：

```bash
uvicorn app:app --reload
```

開啟 `http://127.0.0.1:8000`

---

## 2) 專案結構

- `src/config.py`：來源站設定、目標市場、站點參數
- `src/crawler.py`：爬取來源首頁連結與標題（保守抓取）
- `src/text_utils.py`：翻譯、繁簡轉換、SEO 欄位、Schema 生成
- `src/db.py`：SQLite + FTS5 全文索引
- `scripts/run_pipeline.py`：爬取 + 清洗 + 翻譯 + 落庫
- `scripts/export_wordpress_csv.py`：輸出 WordPress 匯入檔
- `app.py`：FastAPI 搜尋 API + 前端頁面 + sitemap
- `config/market_seo_settings.json`：AI + SEO 需求確認表（可調權重）

---

## 3) WordPress 結構化匯入

匯出後檔案：
- `exports/wordpress_posts.csv`

建議欄位映射：
- `post_title` -> WP 文章標題
- `post_name` -> slug
- `post_excerpt` -> Meta description
- `post_content` -> 文章內容（已含來源連結）
- `schema_json_ld` -> 自訂欄位（可用 SEO 外掛讀取）
- `region_code` / `keyword_type` -> 自訂 taxonomy 或自訂欄位

---

## 4) SEO + Geo 實作要點（已內建）

- 雙語內容：繁體 + 簡體
- SearchAction Schema：首頁可被搜尋引擎理解為可查詢站
- Article Schema：每筆內容產生 JSON-LD
- sitemap：`/sitemap.xml`
- 區域欄位：`region_code`（可對應中國、香港、東南亞分頁）
- 關鍵字策略：`forecast`（流量）/ `howto`（轉化）

---

## 5) 版權與法律（重要）

本系統採用「摘要重整」策略：
- 不直接複製原文全文
- 不抓取他站圖片
- 保留來源連結與出處
- 以制度說明、市場解讀、流程教學等二次整理內容為主

建議在正式上線前，加入：
- 來源網站 robots 與 ToS 檢查
- 法務審閱內容重製政策
- 自動審核規則（相似度上限）

---

## 6) 你提供的連結說明

你提供的 Google Meet / share 連結可能需授權，本系統先以公開可抓取站點建立流程。
後續可加上：
- 手動貼入會議逐字稿 / 私有文件
- 再進行翻譯、重整、SEO 化輸出

---

## 7) 下一步建議

1. 接入可授權 API（例如日本實價資料或商業資料源）
2. 加入排程（Windows Task Scheduler / cron）
3. 新增地區落地頁（/zh-hant/hk, /zh-hans/cn）
4. 將 `SITE_URL` 改為正式網域並提交 Search Console

---

## 8) Windows 自動排程（已提供）

### 8.1 一次性建立排程

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_scheduler.ps1 -TaskName "SCLAW_Every2Hours_Pipeline" -IntervalHours 2 -StartTime 00:00 -Force
```

說明：
- 每 2 小時自動執行一次（全天定時更新）
- `-Force` 會覆蓋同名舊任務

### 8.2 立即手動跑一次（驗證）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_daily.ps1
```

### 8.3 乾跑模式（只看命令不執行）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_daily.ps1 -DryRun
```

### 8.4 日誌位置

- `logs/pipeline_yyyyMMdd_HHmmss.log`

### 8.5 失敗重試機制

- 參數：`-MaxRetry`（預設 2 次）
- 參數：`-RetryWaitSeconds`（預設 30 秒）
- 範例：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_daily.ps1 -MaxRetry 3 -RetryWaitSeconds 60
```

---

## 9) Geo 落地頁 SEO（已提供）

已新增地區 SEO 路由：

- 香港（繁）：`/zh-hant/hk`
- 香港（简）：`/zh-hans/hk`
- 中國（繁）：`/zh-hant/cn`
- 中國（简）：`/zh-hans/cn`
- 東南亞（繁）：`/zh-hant/sea`
- 東南亞（简）：`/zh-hans/sea`

功能說明：
- 地區頁自動拉取該地區內容清單（東南亞為新加坡/馬來西亞/泰國聚合）
- 每頁含 `CollectionPage + ItemList` JSON-LD
- `sitemap.xml` 已自動納入上述地區頁 URL

---

## 12) 查詢條件與分類（最新）

已支援以下查詢條件：
- 關鍵字全文查詢
- 地區（region）
- 關鍵字類型（forecast/howto）
- 目標（intent_target：房地產/投資/稅務/貸款/政策）
- 主題分類（topic_category）
- 來源狀態（access_status：public/restricted）

已提供 API：
- `/api/search`：多條件查詢
- `/api/guidance`：最新資訊指引（顯示最近更新項目與查詢建議）
- `/api/source-map`：公司背景 + 資料來源分類地圖
- `/api/config/sources`：來源清單 / 新增來源網址
- `/api/config/sources/toggle`：來源啟用/停用
- `/api/config/sources/priority`：來源抓取權重（priority）
- `/api/config/crawl-settings`：爬文配置（每來源抓取筆數、更新間隔小時）
- `/api/config/crawl-now`：立即爬取指定來源
- `/api/source-cases`：案例清單（可點進查看數據內容）
- `/api/source-health`：來源健康儀表板（案例數、public/restricted、最近檢查）
- `/api/manual-summary`：單一網址重點翻譯 API

已提供頁面：
- `/company`：公司背景與服務流程頁
- `/guide/tw-buy-japan-property`：SEO 文章示範頁（台灣人買日本房完整流程）
- `/case/{source_item_id}`：來源案例詳情頁（原始資料 + 整理內容）
- `/manual-summary`：手動來源網址 -> 翻譯重點小網頁

配置檔：
- `config/source_registry.json`：可彈性增加來源網址
- `config/crawl_settings.json`：可調整爬文數量與更新間隔

排程同步：
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync_scheduler_from_config.ps1 -Force
```

手動來源連結若需登入（如部分 Google 連結）會標記為 `restricted`，
可公開頁面（如 at home）會標記為 `public` 並定時更新。

---

## 10) 兩個交付版本（你要的）

### A. 本機執行版（含環境，一鍵可跑）

已提供：
- `run_local.ps1`（會自動建立 `.venv`、安裝套件、可選初始化資料）
- `run_local.bat`（雙擊即可啟動）

啟動方式：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File run_local.ps1 -InitData
```

或直接雙擊：

- `run_local.bat`

### B. 掛網部署版（含品牌樣式）

已提供：`Dockerfile`、`docker-compose.yml`（Nginx／HTTP）、`docker-compose.prod.yml`（Caddy／HTTPS）、`deploy/`、`static/site.css`、`.env.example`。

**完整部署環境說明（網域、HTTPS、環境變數、維運）請見：[docs/DEPLOY.md](docs/DEPLOY.md)。**

快速指令（HTTP 內網或無 TLS）：

```bash
copy .env.example .env
docker compose up -d --build
```

停止：`docker compose down`。對外埠 `80`，`data`／`exports`／`logs` 持久化。

---

## 11) 域名設計與正式上線（HTTPS）

請改讀 **[docs/DEPLOY.md](docs/DEPLOY.md)**（DNS、`SITE_URL`／`DOMAIN`、Caddy、後台「站台／DNS」、常見問題）。

### 11.1 上線前：資料/搜尋穩定性（HOMES 圖片防重）

HOMES（`homes.co.jp`）頁面常混入「推薦物件」縮圖，若未過濾會造成 `/case/{id}` 主圖跨案重複。上線前建議跑一次：

```bash
python scripts/clean_homes_image_urls_by_tokens.py --clear-when-no-match --limit 200000
python scripts/clean_homes_listing_media_json_by_tokens.py --clear-when-no-match --limit 200000
python scripts/preflight_release.py --json
```

`preflight_release.py` 的輸出 `ok=true` 代表：
- SQLite FTS5 索引與 trigger 正常（搜尋穩定）
- HOMES 的 `image_urls` / `listing_media_json` 不再有「非本物件 token」的汙染（不會再出現跨案錯圖）

可選：若清理後部分 HOMES 案件顯示「暫無物件主圖」，可用 Playwright（搭配 `data/playwright_storage_state.json` cookies）補回正確相簿：

```bash
python scripts/repair_homes_media.py --limit 200 --channel chrome --storage-state data/playwright_storage_state.json
```

最常用生產啟動：

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

一鍵寫入 `.env` 網域相關欄位（根網域勿加 `https://`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/configure_domain.ps1 -Domain manuvip.com
```

---

## 13) 版本備份（每次完成可執行）

已提供版本化備份腳本：

- `scripts/backup_version.ps1`

功能：

- 每次執行自動遞增版本號（`v0001`, `v0002`...）
- 產生壓縮備份到 `backups/`
- 寫入版本狀態：`backups/version_state.json`
- 寫入備份紀錄：`backups/backup_manifest.csv`

預設備份不含大型資料夾（`.venv`, `data`, `exports`, `logs`, `backups`）以提高速度。

### 13.1 基本備份

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup_version.ps1 -Label "after-ui-update"
```

### 13.2 含資料備份（完整）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup_version.ps1 -Label "full-backup" -IncludeData
```
