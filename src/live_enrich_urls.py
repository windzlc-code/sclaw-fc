"""即時補抓／批次補圖：允許的物件詳情 URL 判定（與 fetch_property_detail 解析能力對齊）。"""

from __future__ import annotations

import re


def live_enrich_eligible_url(item_url: str) -> bool:
    u = (item_url or "").strip()
    if not u.startswith(("http://", "https://")):
        return False
    ul = u.lower()
    if "suumo.jp" in ul:
        return bool(
            re.search(r"/(?:nc_|jnc_)[0-9a-z_]+", ul)
            or "/chintai/jnc_" in ul
            or "/chintai/nc_" in ul
            or re.search(r"/(?:ikkodate|chukoikkodate)/[^?#]*/(?:bc_|nc_|jnc_)[0-9a-z_]+", ul)
            or ("/jj/bukken/shosai/" in ul and re.search(r"(?:[?&])nc=[0-9a-z_]{6,}", ul))
        )
    if "realestate.yahoo.co.jp" in ul:
        return bool(
            re.search(r"/used/mansion/detail(?:_corp)?/[a-z0-9]{5,}(?:/|$|\?)", ul)
            or re.search(r"/land/detail(?:_corp)?/[a-z0-9]{5,}(?:/|$|\?)", ul)
            or re.search(r"/(?:used|new)/house/detail(?:_corp)?/[a-z0-9]{5,}(?:/|$|\?)", ul)
        )
    if "athome.co.jp" in ul:
        return bool(
            re.search(r"/mansion/(?:shinchiku|chuko)/[0-9a-z_-]+", ul)
            or re.search(r"/kodate/(?:shinchiku|chuko)/[0-9a-z_-]+", ul)
            or re.search(r"/kodate/[0-9a-z_-]+/?(?:$|\?)", ul)
            or re.search(r"/tochi/[0-9a-z_-]+", ul)
            or "/chintai/" in ul
        )
    if "realestate.rakuten.co.jp" in ul:
        return bool(
            re.search(r"/(?:usedmansion|newdetached|useddetached|land)/id-[0-9a-z_-]+/?(?:$|\?)", ul)
            or "/bukken/" in ul
            or "/mansion/bk_" in ul
        )
    if "yes1.co.jp" in ul:
        return bool(re.search(r"/contents/detail/[0-9a-z_-]+/?(?:$|\?)", ul) or "/bukken/" in ul or "/estate/" in ul)
    if "oheya-su.jp" in ul:
        return bool(re.search(r"/(?:bukken|detail|chintai|mansion|kodate)/[^?#]*[0-9]{4,}", ul))
    if "homes.co.jp" in ul:
        if "/chintai/room/" in ul:
            return True
        if "/mansion/b-" in ul or "/mansion/ms_" in ul:
            return True
        if "/kodate/" in ul or "/ikkodate/" in ul or "/kk/" in ul:
            return True
        if "/land/" in ul and "detail" in ul:
            return True
        return False
    return False
