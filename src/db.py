import sqlite3
import threading
import os
from contextlib import contextmanager

from src.config import DATA_DIR, DB_PATH

_WAL_LOCK = threading.Lock()
_WAL_CONFIGURED = False


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    """降低並發寫入時 'database is locked'：WAL、busy 等待、連線層 timeout。"""
    global _WAL_CONFIGURED
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        # 毫秒；與 connect(timeout=…) 併用
        busy_ms = max(1000, min(60000, int(os.getenv("SCLAW_SQLITE_BUSY_TIMEOUT_MS", "15000") or 15000)))
        conn.execute(f"PRAGMA busy_timeout={busy_ms}")
        temp_store = (os.getenv("SCLAW_SQLITE_TEMP_STORE") or "FILE").strip().upper()
        if temp_store not in {"DEFAULT", "FILE", "MEMORY"}:
            temp_store = "FILE"
        conn.execute(f"PRAGMA temp_store={temp_store}")
        cache_kib = max(1024, min(64000, int(os.getenv("SCLAW_SQLITE_CACHE_SIZE_KIB", "8000") or 8000)))
        conn.execute(f"PRAGMA cache_size=-{cache_kib}")
        # WAL 只需每個進程確認一次；每次查詢都設定會在背景寫入時放大鎖等待。
        if not _WAL_CONFIGURED:
            with _WAL_LOCK:
                if not _WAL_CONFIGURED:
                    try:
                        conn.execute("PRAGMA journal_mode=WAL")
                    finally:
                        _WAL_CONFIGURED = True
    except sqlite3.Error:
        pass


@contextmanager
def get_conn():
    ensure_dirs()
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=60.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    _configure_sqlite_connection(conn)
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        try:
            try:
                conn.execute("PRAGMA busy_timeout=1000")
            except sqlite3.Error:
                pass
            conn.executescript(
                """
            CREATE TABLE IF NOT EXISTS source_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                source_category TEXT NOT NULL,
                source_url TEXT NOT NULL,
                item_url TEXT NOT NULL UNIQUE,
                title_original TEXT NOT NULL,
                body_original TEXT,
                language TEXT NOT NULL DEFAULT 'ja',
                published_at TEXT,
                crawled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                access_status TEXT NOT NULL DEFAULT 'public',
                access_note TEXT NOT NULL DEFAULT '',
                last_checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS content_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL,
                title_zh_hant TEXT NOT NULL,
                title_zh_hans TEXT NOT NULL,
                body_zh_hant TEXT NOT NULL,
                body_zh_hans TEXT NOT NULL,
                region_code TEXT NOT NULL,
                keyword_type TEXT NOT NULL,
                intent_target TEXT NOT NULL DEFAULT '房地產',
                topic_category TEXT NOT NULL DEFAULT '市場資訊',
                keyword_tags TEXT NOT NULL DEFAULT '',
                seo_slug TEXT NOT NULL,
                seo_title TEXT NOT NULL,
                seo_description TEXT NOT NULL,
                schema_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(source_item_id) REFERENCES source_items(id)
            );

            CREATE INDEX IF NOT EXISTS idx_content_region ON content_items(region_code);
            CREATE INDEX IF NOT EXISTS idx_content_keyword_type ON content_items(keyword_type);
            CREATE INDEX IF NOT EXISTS idx_content_seo_slug ON content_items(seo_slug);
            CREATE INDEX IF NOT EXISTS idx_source_items_last_checked
                ON source_items(last_checked_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_source_items_source_name
                ON source_items(source_name);
            CREATE INDEX IF NOT EXISTS idx_content_source_item
                ON content_items(source_item_id);

            CREATE TABLE IF NOT EXISTS case_investment_metrics (
                source_item_id INTEGER PRIMARY KEY,
                metrics_json TEXT NOT NULL,
                data_quality TEXT NOT NULL DEFAULT '',
                source_label TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source_last_checked_at TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(source_item_id) REFERENCES source_items(id)
            );
            CREATE INDEX IF NOT EXISTS idx_case_investment_quality
                ON case_investment_metrics(data_quality, computed_at DESC);

            CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
                title_zh_hant,
                title_zh_hans,
                body_zh_hant,
                body_zh_hans,
                content='content_items',
                content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS content_ai AFTER INSERT ON content_items BEGIN
                INSERT INTO content_fts(rowid, title_zh_hant, title_zh_hans, body_zh_hant, body_zh_hans)
                VALUES (new.id, new.title_zh_hant, new.title_zh_hans, new.body_zh_hant, new.body_zh_hans);
            END;

            CREATE TRIGGER IF NOT EXISTS content_ad AFTER DELETE ON content_items BEGIN
                INSERT INTO content_fts(content_fts, rowid, title_zh_hant, title_zh_hans, body_zh_hant, body_zh_hans)
                VALUES('delete', old.id, old.title_zh_hant, old.title_zh_hans, old.body_zh_hant, old.body_zh_hans);
            END;

            CREATE TRIGGER IF NOT EXISTS content_au AFTER UPDATE ON content_items BEGIN
                INSERT INTO content_fts(content_fts, rowid, title_zh_hant, title_zh_hans, body_zh_hant, body_zh_hans)
                VALUES('delete', old.id, old.title_zh_hant, old.title_zh_hans, old.body_zh_hant, old.body_zh_hans);
                INSERT INTO content_fts(rowid, title_zh_hant, title_zh_hans, body_zh_hant, body_zh_hans)
                VALUES (new.id, new.title_zh_hant, new.title_zh_hans, new.body_zh_hant, new.body_zh_hans);
            END;

            CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
                title_original,
                body_original,
                item_url,
                source_name,
                content='source_items',
                content_rowid='id'
            );

            CREATE TRIGGER IF NOT EXISTS source_ai AFTER INSERT ON source_items BEGIN
                INSERT INTO source_fts(rowid, title_original, body_original, item_url, source_name)
                VALUES (new.id, new.title_original, new.body_original, new.item_url, new.source_name);
            END;

            CREATE TRIGGER IF NOT EXISTS source_ad AFTER DELETE ON source_items BEGIN
                INSERT INTO source_fts(source_fts, rowid, title_original, body_original, item_url, source_name)
                VALUES('delete', old.id, old.title_original, old.body_original, old.item_url, old.source_name);
            END;

            CREATE TRIGGER IF NOT EXISTS source_au AFTER UPDATE ON source_items BEGIN
                INSERT INTO source_fts(source_fts, rowid, title_original, body_original, item_url, source_name)
                VALUES('delete', old.id, old.title_original, old.body_original, old.item_url, old.source_name);
                INSERT INTO source_fts(rowid, title_original, body_original, item_url, source_name)
                VALUES (new.id, new.title_original, new.body_original, new.item_url, new.source_name);
            END;

            CREATE TABLE IF NOT EXISTS jp_listing_region_index (
                region_key TEXT NOT NULL,
                source_item_id INTEGER NOT NULL,
                sort_time TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(region_key, source_item_id)
            );
            CREATE INDEX IF NOT EXISTS idx_jp_listing_region_source
                ON jp_listing_region_index(source_item_id);
            CREATE INDEX IF NOT EXISTS idx_jp_listing_region_sort
                ON jp_listing_region_index(region_key, sort_time DESC, source_item_id DESC);

            CREATE TABLE IF NOT EXISTS keyword_search_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                channel TEXT NOT NULL,
                search_count INTEGER NOT NULL DEFAULT 0,
                last_filters_json TEXT NOT NULL DEFAULT '{}',
                last_searched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(keyword, channel)
            );

            CREATE INDEX IF NOT EXISTS idx_keyword_stats_keyword ON keyword_search_stats(keyword);
            CREATE INDEX IF NOT EXISTS idx_keyword_stats_count ON keyword_search_stats(search_count DESC);

            CREATE TABLE IF NOT EXISTS keyword_search_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                channel TEXT NOT NULL,
                filters_json TEXT NOT NULL DEFAULT '{}',
                searched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_keyword_logs_keyword ON keyword_search_logs(keyword);
            CREATE INDEX IF NOT EXISTS idx_keyword_logs_time ON keyword_search_logs(searched_at DESC);

            CREATE TABLE IF NOT EXISTS seo_draft_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL UNIQUE,
                seo_slug TEXT NOT NULL UNIQUE,
                seo_title TEXT NOT NULL,
                seo_description TEXT NOT NULL,
                body_zh_hant TEXT NOT NULL,
                body_zh_hans TEXT NOT NULL,
                faq_schema_json TEXT NOT NULL,
                source_channels TEXT NOT NULL DEFAULT '',
                keyword_score INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft',
                generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_seo_draft_status ON seo_draft_items(status);
            CREATE INDEX IF NOT EXISTS idx_seo_draft_score ON seo_draft_items(keyword_score DESC);

            CREATE TABLE IF NOT EXISTS sales_mcp_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_message TEXT NOT NULL,
                assistant_reply TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'discover',
                intent_score INTEGER NOT NULL DEFAULT 0,
                outcome TEXT NOT NULL DEFAULT 'active',
                recommendation_json TEXT NOT NULL DEFAULT '{}',
                knowledge_meta_json TEXT NOT NULL DEFAULT '{}',
                conversation_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_sales_mcp_events_session ON sales_mcp_events(session_id);
            CREATE INDEX IF NOT EXISTS idx_sales_mcp_events_stage ON sales_mcp_events(stage);
            CREATE INDEX IF NOT EXISTS idx_sales_mcp_events_score ON sales_mcp_events(intent_score DESC);
            CREATE INDEX IF NOT EXISTS idx_sales_mcp_events_time ON sales_mcp_events(created_at DESC);

            CREATE TABLE IF NOT EXISTS sales_mcp_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                intent_score INTEGER NOT NULL DEFAULT 0,
                notify_reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_sales_mcp_notify_session ON sales_mcp_notifications(session_id);
            CREATE INDEX IF NOT EXISTS idx_sales_mcp_notify_status ON sales_mcp_notifications(status);

            CREATE TABLE IF NOT EXISTS social_radar_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL DEFAULT '',
                account_handle TEXT NOT NULL DEFAULT '',
                account_url TEXT NOT NULL DEFAULT '',
                audience_note TEXT NOT NULL DEFAULT '',
                topic_note TEXT NOT NULL DEFAULT '',
                region_note TEXT NOT NULL DEFAULT '',
                channel_region TEXT NOT NULL DEFAULT '',
                follower_count INTEGER NOT NULL DEFAULT 0,
                like_count INTEGER NOT NULL DEFAULT 0,
                comment_count INTEGER NOT NULL DEFAULT 0,
                account_score INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 50,
                raw_line TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(platform, account_handle)
            );

            CREATE TABLE IF NOT EXISTS social_radar_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                source_account TEXT NOT NULL DEFAULT '',
                source_account_url TEXT NOT NULL DEFAULT '',
                prospect_handle TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                profile_url TEXT NOT NULL DEFAULT '',
                post_url TEXT NOT NULL DEFAULT '',
                interaction_type TEXT NOT NULL DEFAULT '',
                interaction_text TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL DEFAULT 0,
                stage TEXT NOT NULL DEFAULT 'observe',
                intent_label TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                suggested_reply TEXT NOT NULL DEFAULT '',
                route_message TEXT NOT NULL DEFAULT '',
                support_url TEXT NOT NULL DEFAULT '',
                channel_region TEXT NOT NULL DEFAULT '',
                preferred_area TEXT NOT NULL DEFAULT '',
                budget_hint TEXT NOT NULL DEFAULT '',
                investment_intent TEXT NOT NULL DEFAULT '',
                persona_tags_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'new',
                raw_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_social_radar_score ON social_radar_leads(score DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_social_radar_platform ON social_radar_leads(platform, source_account);
            CREATE INDEX IF NOT EXISTS idx_social_radar_status ON social_radar_leads(status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS support_bot_eval_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_message TEXT NOT NULL DEFAULT '',
                human_intent INTEGER NOT NULL DEFAULT 0,
                lead_capture_ready INTEGER NOT NULL DEFAULT 0,
                selected_case_count INTEGER NOT NULL DEFAULT 0,
                managed_case_count INTEGER NOT NULL DEFAULT 0,
                knowledge_row_count INTEGER NOT NULL DEFAULT 0,
                knowledge_score INTEGER NOT NULL DEFAULT 0,
                case_score INTEGER NOT NULL DEFAULT 0,
                handoff_score INTEGER NOT NULL DEFAULT 0,
                overall_score INTEGER NOT NULL DEFAULT 0,
                optimization_level TEXT NOT NULL DEFAULT 'L1',
                bot_status TEXT NOT NULL DEFAULT 'needs_training',
                recommendations_json TEXT NOT NULL DEFAULT '[]',
                eval_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_support_bot_eval_session ON support_bot_eval_events(session_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_support_bot_eval_score ON support_bot_eval_events(overall_score DESC);
            CREATE INDEX IF NOT EXISTS idx_support_bot_eval_time ON support_bot_eval_events(created_at DESC);

            CREATE TABLE IF NOT EXISTS data_completion_bot_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_label TEXT NOT NULL DEFAULT '',
                target_source_item_id INTEGER NOT NULL DEFAULT 0,
                case_media_fixed INTEGER NOT NULL DEFAULT 0,
                case_text_fixed INTEGER NOT NULL DEFAULT 0,
                missing_media_count INTEGER NOT NULL DEFAULT 0,
                syncable_media_count INTEGER NOT NULL DEFAULT 0,
                weak_text_count INTEGER NOT NULL DEFAULT 0,
                zero_cell_count INTEGER NOT NULL DEFAULT 0,
                remaining_three_zero_count INTEGER NOT NULL DEFAULT 0,
                remaining_three_total INTEGER NOT NULL DEFAULT 0,
                overall_score INTEGER NOT NULL DEFAULT 0,
                optimization_level TEXT NOT NULL DEFAULT 'L1',
                bot_status TEXT NOT NULL DEFAULT 'needs_scan',
                recommendations_json TEXT NOT NULL DEFAULT '[]',
                eval_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_data_completion_bot_time ON data_completion_bot_events(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_data_completion_bot_status ON data_completion_bot_events(bot_status);
            CREATE INDEX IF NOT EXISTS idx_data_completion_bot_score ON data_completion_bot_events(overall_score DESC);

            CREATE TABLE IF NOT EXISTS offline_support_scenarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scene_id TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                keywords_json TEXT NOT NULL DEFAULT '[]',
                conclusion TEXT NOT NULL,
                bullets_json TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 100,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_offline_support_enabled ON offline_support_scenarios(enabled);
            CREATE INDEX IF NOT EXISTS idx_offline_support_priority ON offline_support_scenarios(priority DESC, updated_at DESC);

            CREATE TABLE IF NOT EXISTS support_qa_training (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qa_id TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL DEFAULT '',
                keywords_json TEXT NOT NULL DEFAULT '[]',
                answer_body TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 100,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_support_qa_training_enabled ON support_qa_training(enabled);
            CREATE INDEX IF NOT EXISTS idx_support_qa_training_priority ON support_qa_training(priority DESC, updated_at DESC);

            CREATE TABLE IF NOT EXISTS app_kv (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS human_handoff_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                email TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                action_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                matched_scene_id TEXT NOT NULL DEFAULT '',
                matched_scene_label TEXT NOT NULL DEFAULT '',
                context_message TEXT NOT NULL DEFAULT '',
                opinion TEXT NOT NULL DEFAULT '',
                questionnaire_json TEXT NOT NULL DEFAULT '',
                conversation_json TEXT NOT NULL DEFAULT '',
                scenario_weights_json TEXT NOT NULL DEFAULT '',
                ai_handoff_summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_human_handoff_created ON human_handoff_requests(created_at DESC);

            CREATE TABLE IF NOT EXISTS telegram_outbound_bridge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_chat_id TEXT NOT NULL,
                telegram_message_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(telegram_chat_id, telegram_message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tg_bridge_session ON telegram_outbound_bridge(session_id);

            CREATE TABLE IF NOT EXISTS telegram_staff_inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                body TEXT NOT NULL,
                telegram_from_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_tg_inbox_session_id ON telegram_staff_inbox(session_id, id);

            CREATE TABLE IF NOT EXISTS support_session_case_interest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                case_key TEXT NOT NULL,
                source_item_id INTEGER NOT NULL DEFAULT 0,
                content_id INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                item_url TEXT NOT NULL DEFAULT '',
                article_url TEXT NOT NULL DEFAULT '',
                transaction_label_zh TEXT NOT NULL DEFAULT '',
                jp_region_display_zh TEXT NOT NULL DEFAULT '',
                transit_line_zh TEXT NOT NULL DEFAULT '',
                address_hint_zh TEXT NOT NULL DEFAULT '',
                price_text_hant TEXT NOT NULL DEFAULT '',
                layout_text_hant TEXT NOT NULL DEFAULT '',
                area_text_hant TEXT NOT NULL DEFAULT '',
                building_type_zh TEXT NOT NULL DEFAULT '',
                thumb_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id, case_key)
            );
            CREATE INDEX IF NOT EXISTS idx_support_case_interest_session
              ON support_session_case_interest(session_id, updated_at DESC, id DESC);

            CREATE TABLE IF NOT EXISTS source_item_recrawl_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_item_id INTEGER NOT NULL,
                item_url TEXT NOT NULL DEFAULT '',
                operator TEXT NOT NULL DEFAULT '',
                trigger_kind TEXT NOT NULL DEFAULT '',
                changed INTEGER NOT NULL DEFAULT 0,
                changed_fields_json TEXT NOT NULL DEFAULT '[]',
                title_before TEXT NOT NULL DEFAULT '',
                title_after TEXT NOT NULL DEFAULT '',
                body_len_before INTEGER NOT NULL DEFAULT 0,
                body_len_after INTEGER NOT NULL DEFAULT 0,
                image_count_before INTEGER NOT NULL DEFAULT 0,
                image_count_after INTEGER NOT NULL DEFAULT 0,
                body_preview_before TEXT NOT NULL DEFAULT '',
                body_preview_after TEXT NOT NULL DEFAULT '',
                diff_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_recrawl_log_source_item
              ON source_item_recrawl_log(source_item_id, id DESC);
                """
            )
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                return
            raise
        _ensure_column(conn, "human_handoff_requests", "conversation_json", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "human_handoff_requests", "scenario_weights_json", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "human_handoff_requests", "ai_handoff_summary", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "human_handoff_requests", "action_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "human_handoff_requests", "session_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "human_handoff_requests", "matched_scene_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "human_handoff_requests", "matched_scene_label", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "human_handoff_requests", "context_message", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "human_handoff_requests", "opinion", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "human_handoff_requests", "questionnaire_json", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "jp_listing_region_index", "sort_time", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "social_radar_accounts", "channel_region", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "social_radar_accounts", "follower_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "social_radar_accounts", "like_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "social_radar_accounts", "comment_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "social_radar_accounts", "account_score", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "social_radar_leads", "channel_region", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "social_radar_leads", "preferred_area", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "social_radar_leads", "budget_hint", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "social_radar_leads", "investment_intent", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "social_radar_leads", "persona_tags_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "support_session_case_interest", "address_hint_zh", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "support_session_case_interest", "thumb_url", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "source_items", "access_status", "TEXT NOT NULL DEFAULT 'public'")
        _ensure_column(conn, "source_items", "access_note", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "source_items", "last_checked_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
        _ensure_column(conn, "source_items", "image_urls", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "source_items", "content_kind", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "content_items", "intent_target", "TEXT NOT NULL DEFAULT '房地產'")
        _ensure_column(conn, "content_items", "topic_category", "TEXT NOT NULL DEFAULT '市場資訊'")
        _ensure_column(conn, "content_items", "keyword_tags", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "content_items", "updated_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
        _ensure_column(conn, "content_items", "case_transaction_override", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "content_items", "case_jp_region_override", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "content_items", "case_transit_override", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "content_items", "jp_station_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "content_items", "walk_min", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "content_items", "featured_weight", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "content_items", "listing_media_json", "TEXT NOT NULL DEFAULT '[]'")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS case_investment_metrics (
                source_item_id INTEGER PRIMARY KEY,
                metrics_json TEXT NOT NULL,
                data_quality TEXT NOT NULL DEFAULT '',
                source_label TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                computed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source_last_checked_at TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(source_item_id) REFERENCES source_items(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_case_investment_quality "
            "ON case_investment_metrics(data_quality, computed_at DESC)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_seo_slug ON content_items(seo_slug)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_source_item ON content_items(source_item_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_intent_target ON content_items(intent_target)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_topic_category ON content_items(topic_category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_region_recent ON content_items(region_code, id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_topic_recent ON content_items(topic_category, id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_intent_recent ON content_items(intent_target, id DESC)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_items_content_kind_last_checked "
            "ON source_items(content_kind, last_checked_at DESC, id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_items_content_kind_published "
            "ON source_items(content_kind, published_at DESC, id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_items_content_kind_crawled "
            "ON source_items(content_kind, crawled_at DESC, id DESC)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_updated ON content_items(updated_at DESC, id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_case_region ON content_items(case_jp_region_override)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_content_featured_updated "
            "ON content_items(featured_weight DESC, updated_at DESC, id DESC)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_jp_station ON content_items(jp_station_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_jp_station_walk ON content_items(jp_station_id, walk_min)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_case_region_recent ON content_items(case_jp_region_override, id DESC)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_content_case_transaction_recent "
            "ON content_items(case_transaction_override, id DESC)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_jp_station_recent ON content_items(jp_station_id, id DESC)")
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jp_listing_region_sort_time "
                "ON jp_listing_region_index(region_key, sort_time DESC, source_item_id DESC)"
            )
        except sqlite3.OperationalError:
            pass
        from src.jp_transit_model import ensure_jp_transit_schema_and_seed

        try:
            ensure_jp_transit_schema_and_seed(conn)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise

        try:
            # External-content FTS tables can return rows without the index being populated.
            # Check the shadow index table instead.
            has_source_fts_index = conn.execute("SELECT 1 FROM source_fts_idx LIMIT 1").fetchone()
        except sqlite3.OperationalError:
            has_source_fts_index = None
        if not has_source_fts_index:
            try:
                has_source_rows = conn.execute("SELECT 1 FROM source_items LIMIT 1").fetchone()
            except sqlite3.OperationalError:
                has_source_rows = None
            if has_source_rows:
                try:
                    conn.execute("INSERT INTO source_fts(source_fts) VALUES('rebuild')")
                except sqlite3.OperationalError:
                    pass
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names = {row[1] for row in existing}
    if column in names:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
