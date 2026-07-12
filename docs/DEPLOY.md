# 部署環境說明

本文件說明正式環境（HTTPS、網域、Docker）如何設定與維運。開發本機請仍使用 `uvicorn app:app --reload` 或 `run_local.ps1`。

---

## 1. 架構概要

| 元件 | 檔案／映像 | 說明 |
|------|------------|------|
| 應用 | `Dockerfile` → `app` 服務 | FastAPI（Uvicorn），內部埠 `8000` |
| 正式反向代理 + TLS | `deploy/Caddyfile`，映像 `caddy:2.10` | 對外 `80` / `443`，自動簽發 Let’s Encrypt |
| 開發／內網反代 | `deploy/nginx/default.conf` | 搭配 `docker-compose.yml`，僅 HTTP `80` |

持久化目錄（勿刪除容器重建時之資料）：

- `data/`：SQLite 與站內資料
- `exports/`：匯出檔
- `logs/`：日誌

---

## 2. 環境變數（`.env`）

請複製範本後修改：

```bash
copy .env.example .env
```

與**對外網址／部署**直接相關的變數如下（完整列表見 `.env.example`）。

| 變數 | 用途 | 正式環境建議 |
|------|------|----------------|
| `SITE_URL` | 應用預設的公開根網址（canonical、sitemap、絕對連結後援） | `https://www.manuvip.com` |
| `DOMAIN` | 僅供 **Caddy** 使用：填**根網域**，勿含 `www` | `manuvip.com` |
| `SITE_NAME` | 站名 | 依品牌填寫 |
| `BRAND_NAME` | 品牌字樣（可空） | 選填 |
| `SCLAW_ENABLE_BACKGROUND_WORKERS` | 是否啟用 web server 進程內背景排程（FAQ refresh / smart-nav intel） | **正式環境建議 `0`** |
| `WEB_CONCURRENCY` | Uvicorn worker 數 | SQLite 場景預設 `1`；僅在壓測確認 CPU 與寫入負載足夠時才提高 |

說明：

- `SITE_URL` 在程式 `src/config.py` 中有預設值；Docker Compose 生產檔亦設有預設，但若 `.env` 內把 `SITE_URL=` 留空，容器內會變成空字串而覆蓋預設，請務必填寫或刪除該行。
- **後台「站台／DNS」**（`app_kv`）可覆寫主要網址與別名；若後台有設定，**canonical 以後台為準**，仍建議與 `SITE_URL`、Caddy 行為一致，避免 SEO 與重新導向打架。

---

## 3. 正式網域與 SEO（www 與裸域）

正式站預設策略：

- **主要／canonical**：`https://www.manuvip.com`
- **裸域**：`https://manuvip.com` → **301** 到對應的 `https://www.manuvip.com/...`

對應設定：

1. **`deploy/Caddyfile`**：`www.{DOMAIN}` 提供網站；`{DOMAIN}` 只做 301 到 `www`。
2. **`SITE_URL`** 與後台「主要網址」建議填 `https://www.manuvip.com`。
3. **後台「其他別名」**可填一行：`https://manuvip.com`（利於 CORS／站內邏輯辨識多來源；實際使用者多半會被 301 到 www）。

若未來要改為「裸域當主網址」，需同時調整：Caddy 路由、`SITE_URL`、後台主要網址，並統一重新導向方向。

---

## 4. 生產部署（Caddy + HTTPS）

### 4.0 上線前：資料/搜尋穩定性檢查（必做）

在要上線的那台機器、那份 `data/jp_real_estate.sqlite3` 上，先跑一次：

```bash
python scripts/clean_homes_image_urls_by_tokens.py --clear-when-no-match --limit 200000
python scripts/clean_homes_listing_media_json_by_tokens.py --clear-when-no-match --limit 200000
python scripts/preflight_release.py --json
```

`preflight_release.py` 若輸出 `ok=true`，代表：
- SQLite FTS5 索引與 trigger 正常（搜尋穩定）
- HOMES（`homes.co.jp`）案件圖片不再出現跨案汙染（不會再看到不同案件主圖一樣）

### 4.1 先準備 DNS

在網域註冊商設定（範例網域 `manuvip.com`）：

- `A`：`@` → 伺服器公網 IP  
- `A`：`www` → 同上  

確認伺服器防火牆放行 **80、443**。

### 4.2 寫入 `.env`

可手動編輯，或使用腳本（會寫入 `DOMAIN`、`SITE_URL=https://www.{根網域}` 等）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/configure_domain.ps1 -Domain manuvip.com
```

參數可傳 `www.manuvip.com`，腳本會自動去掉 `www.` 得到根網域給 Caddy 使用。

### 4.3 啟動

於專案根目錄：

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

- Caddy 會向 Let’s Encrypt 申請憑證並自動續期。
- 應用程式對外不直接暴露 `8000`，僅經由 Caddy。

### 4.4 更新映像後重啟

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

---

## 5. 非正式／內網（Nginx，無自動 TLS）

適合本機或已有外部終端 TLS 的情境：

```bash
docker compose --env-file .env up -d --build
```

- 對外預設 **HTTP `80`**（見 `docker-compose.yml` + `deploy/nginx/default.conf`）。
- 請自行在 `.env` 設定 `SITE_URL`，與實際瀏覽網址一致，以免 canonical、sitemap 錯誤。

---

## 6. 後台「站台／DNS」與環境變數的關係

後台可設定：

- **主要站台網址**：作為全站 canonical、絕對連結、sitemap、結構化資料等的首選根網址。
- **其他別名**：一行一個完整 `https://` 網址；會與 `SCLAW_CORS_ORIGINS`（若設定）、環境中的 `SITE_URL` 一併納入 CORS 允許來源計算。

若後台留空，則沿用 `SITE_URL`（見 `src/site_public_config.py`）。

---

## 7. 常見問題

**憑證申請失敗**  
確認 DNS 已生效、`80/443` 可從網際網路連線至本機，且 `DOMAIN` 與 DNS 一致。

**網站網址與 canonical 不一致**  
檢查後台「站台／DNS」、`SITE_URL`、`deploy/Caddyfile` 的 301 方向是否同一套。

**容器內網址變成 example.com 或錯誤網域**  
檢查 `.env` 是否被空字串覆蓋；生產 compose 已為 `SITE_URL`／`DOMAIN` 設預設，仍以明確填寫為佳。

---

## 8. 相關檔案索引

| 路徑 | 說明 |
|------|------|
| `docker-compose.prod.yml` | 生產：app + Caddy |
| `docker-compose.yml` | Nginx 反代（無自動 HTTPS） |
| `deploy/Caddyfile` | 正式站 TLS 與 www／apex 導向 |
| `deploy/nginx/default.conf` | 開發／HTTP 反代 |
| `scripts/configure_domain.ps1` | 一鍵寫入 `.env` 網域相關變數 |
| `.env.example` | 環境變數範本 |
| `src/config.py` | `SITE_URL` 等預設值 |
| `src/site_public_config.py` | 後台 DNS 與 CORS 合併邏輯 |
