"""Filter low-value URLs and strip repetitive SEO suffixes for link lists / RAG UI."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

# Paths that rarely carry crawlable public article value for our use case.
_AUTH_PATH_RE = re.compile(
    r"(/login|/logout|/signin|/sign-up|/signup|/register|/regist\b|/mypage|/my/page|/my/account|"
    r"/password|/passwd|/reset|/oauth|/openid|/auth\b|/member/login|/smt/psl/cond|/cart|/checkout|"
    r"/error|/maintenance)",
    re.IGNORECASE,
)

_NOISE_HOST_SUBSTR = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "facebook.com/tr",
    "pixel.",
    "analytics.",
)


def _path_is_portal_login_segment(path: str) -> bool:
    """True when path is clearly a site login/register entry (path segment `/login`, not e.g. `loginkiyaku`)."""
    plc = (path or "/").lower().rstrip("/")
    if not plc or plc == "/":
        return False
    for head in ("/login", "/signin", "/signup", "/register", "/regist"):
        if plc == head or plc.startswith(f"{head}/"):
            return True
    return False


def _athome_help_non_property_path(path: str) -> bool:
    """
    AtHome /help/ 底下多為站務／登入／使用條款，與物件行情無關；物件向說明常見於
    /help/chintai、/help/mansion 等子路徑，予以保留。
    """
    pl = (path or "").lower()
    if "/help/" not in pl:
        return False
    if any(x in pl for x in ("/help/chintai", "/help/mansion", "/help/kodate", "/help/tochi", "/help/souba")):
        return False
    if "loginkiyaku" in pl or "/help/kiyaku" in pl:
        return True
    if any(x in pl for x in ("tokusoku", "privacy", "cookie", "linkpolicy", "sitepolicy", "copyright")):
        return True
    return False


def title_looks_like_crawl_placeholder(title: str) -> bool:
    """True when title is our generic restricted/failed-crawl label (any zh variant)."""
    t = re.sub(r"\s+", "", (title or "").strip())
    if not t:
        return True
    if t in ("（未命名）", "(未命名)"):
        return True
    # 簡繁與 OCR／編碼誤字（如「抽吸」）
    if re.search(r"需[授]?[权權].{0,4}[无無][法法].{0,4}[抓抽]", t):
        return True
    if "無法抓取" in t or "无法抓取" in t or "无法抽吸" in t:
        return True
    # 門戶以 noscript／反機器人頁回應時常見的標題（伺服器端讀不到正文）
    tl = (title or "").strip().lower()
    if "noscript" in tl:
        return True
    if re.search(r"javascript.{0,8}(被禁|禁用|停用|無法|无法)", t, re.IGNORECASE):
        return True
    if re.search(r"(被禁|禁用|停用).{0,8}javascript", t, re.IGNORECASE):
        return True
    if re.search(r"請.*啟用.*javascript|请.*启用.*javascript|javascript.*啟用|javascript.*启用", t, re.IGNORECASE):
        return True
    if "cookie" in tl and ("有効" in t or "有效" in t or "启用" in t or "啟用" in t):
        return True
    return False


def url_is_portal_broad_hub(url: str) -> bool:
    """
    True for major portal *directory* URLs (root / chintai top / area listing),
    which are rarely useful as「站內匹配」連結（多為爬蟲占位標題）。
    FAQ・knowhow・物件詳情路徑除外。
    """
    s = (url or "").strip()
    if not s.startswith(("http://", "https://")):
        return True
    if url_is_low_value_for_link_list(s):
        return True
    low = s.lower()
    if any(x in low for x in ("/knowhow/", "/kasu/", "#common-", "/kas/", "/know/")):
        return False
    # 明確物件詳情
    if "/chintai/room/" in low:
        return False
    try:
        p = urlparse(s.split("#")[0])
    except Exception:
        return True
    h = (p.netloc or "").lower()
    if h.startswith("www."):
        h = h[4:]
    path = (p.path or "/").rstrip("/") or "/"
    pl = path.lower()
    segs = [x for x in path.split("/") if x]

    if "homes.co.jp" in h:
        if pl in ("/", "/chintai", "/ms"):
            return True
        if pl.startswith("/chintai") and "/room/" not in pl:
            return True
        return False

    if "athome.co.jp" in h:
        if pl in ("/", "/chintai"):
            return True
        if pl.startswith("/chintai"):
            if any(x in pl for x in ("/detail", "bukken", "/room")):
                return False
            return True
        return False

    if "suumo.jp" in h:
        if pl in ("/", "/chintai"):
            return True
        if pl.startswith("/chintai/"):
            if any(seg.startswith(("j_", "bc_", "nc_")) for seg in segs):
                return False
            return True
        if pl.startswith("/ms/") and not any(x in low for x in ("/j_", "/bc_", "/nc_", "/msn", "shinchiku")):
            # 區域列表頁；含 nc_ 等視為可能詳情
            if "/nc_" in low or "/j_" in low:
                return False
            return True

    return False


def url_is_low_value_for_link_list(url: str) -> bool:
    """True if URL is likely login wall, tracker, or non-content page."""
    s = (url or "").strip()
    if not s.startswith(("http://", "https://")):
        return False
    try:
        p = urlparse(s.split("#")[0])
    except Exception:
        return True
    h = (p.netloc or "").lower()
    if h.startswith("www."):
        h = h[4:]
    for frag in _NOISE_HOST_SUBSTR:
        if frag in h:
            return True
    path = p.path or "/"
    q = (p.query or "").lower()
    if _AUTH_PATH_RE.search(f"{path}?{q}"):
        return True
    if "athome.co.jp" in h:
        if _path_is_portal_login_segment(path):
            return True
        # 少數代理／異常 URL 若 path 未帶 /login，仍可能從字串辨識
        if re.search(r"athome\.co\.jp/login(?:/|\?|$)", s.lower()):
            return True
        if _athome_help_non_property_path(path):
            return True
    if "homes.co.jp" in h and _path_is_portal_login_segment(path):
        return True
    if "suumo.jp" in h and _path_is_portal_login_segment(path):
        return True
    return False


# Titles often share the same trailing brand / guide phrase from SEO templates.
_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"[｜|\-—–\s]*(?:台灣買日本房地產指南|台灣人買日本房完整流程|日本買房完整流程指南|沐新株式會社|沐新)\s*$"
    ),
    re.compile(r"[｜|\-—–]\s*[^\s｜|]{1,20}\s*官網\s*$"),
)


def sanitize_dialog_link_label(label: str, *, url: str = "") -> str:
    """Remove repetitive site-wide suffixes; shorten generic labels."""
    t = re.sub(r"\s+", " ", (label or "").strip())
    if not t:
        t = _label_from_url(url)
    changed = True
    while changed and t:
        changed = False
        for pat in _SUFFIX_PATTERNS:
            nt = pat.sub("", t).strip()
            if nt != t:
                t = nt
                changed = True
        # Parenthetical duplicate at end: "…（繁：…）" keep as-is; strip empty parens
        t = re.sub(r"[（(]\s*[）)]", "", t).strip()
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < 2:
        t = _label_from_url(url)
    return t[:120]


def _label_from_url(url: str) -> str:
    try:
        p = urlparse((url or "").strip().split("#")[0])
        seg = [x for x in (p.path or "/").split("/") if x]
        if not seg:
            return (p.netloc or "連結")[:80]
        tail = unquote(seg[-1].replace("-", " ").replace("_", " "))
        tail = re.sub(r"\s+", " ", tail).strip()
        return (tail or p.netloc or "連結")[:120]
    except Exception:
        return "連結"


def listing_title_or_fallback(title: str, url: str) -> str:
    """
    Use crawl title when usable; otherwise a client-facing label from URL
    (avoids showing portal bot-wall titles like「JavaScript 被禁用」).
    """
    raw = (title or "").strip()
    if raw and not title_looks_like_crawl_placeholder(raw):
        return raw[:240]
    u = (url or "").strip()
    low = u.lower()
    if "homes.co.jp" in low and "/chintai/room/" in low:
        return "LIFULL HOME'S 租賃物件（官方頁面；請以瀏覽器開啟）"
    if "homes.co.jp" in low and any(x in low for x in ("/mansion/", "/kodate/", "/tochi/", "/ms/")):
        return "LIFULL HOME'S 物件（官方頁面；請以瀏覽器開啟）"
    if "suumo.jp" in low:
        return "SUUMO 摘要／物件（官方頁面；請以瀏覽器開啟）"
    if "athome.co.jp" in low:
        return "AtHome 摘要／物件（官方頁面；請以瀏覽器開啟）"
    if "realestate.yahoo.co.jp" in low:
        return "Yahoo!不動産 摘要／物件（官方頁面；請以瀏覽器開啟）"
    if "realestate.rakuten.co.jp" in low:
        return "楽天不動産 摘要／物件（官方頁面；請以瀏覽器開啟）"
    if "yes1.co.jp" in low or "yes-station.jp" in low:
        return "イエステーション YesStation 摘要／物件（官方頁面；請以瀏覽器開啟）"
    if "oheya-su.jp" in low or "oheyasuu.com" in low:
        return "OHEYASU（お部屋探す）摘要／物件（官方頁面；請以瀏覽器開啟）"
    lab = sanitize_dialog_link_label("", url=u)
    if lab and not title_looks_like_crawl_placeholder(lab):
        return lab[:240]
    return "站外來源摘要連結"


def dedupe_links_by_url(links: list[dict[str, str]], *, max_items: int = 12) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in links:
        if not isinstance(row, dict):
            continue
        u = str(row.get("url") or "").strip()
        if not u or url_is_low_value_for_link_list(u):
            continue
        key = u.lower().split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        lab = sanitize_dialog_link_label(str(row.get("label") or ""), url=u)
        if not lab:
            lab = _label_from_url(u)
        out.append({"label": lab[:120], "url": u[:2000]})
        if len(out) >= max_items:
            break
    return out
