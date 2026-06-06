import argparse
import re

from src.db import get_conn, init_db


LABEL_RE = re.compile(r"\[(?:財產調查圖片網址|物件參考圖像\s*URL)\]?\s*", re.IGNORECASE)
BROKEN_SUUMO_RE = re.compile(r"https?://img\d*\.suumo\.com/jj/resize[a-z]*[^\s\]\)'\"]*", re.IGNORECASE)
SRC_TOKEN_RE = re.compile(r"src\s*=\s*[^\s\]\)'\"]+", re.IGNORECASE)
GAZO_TOKEN_RE = re.compile(r"gazo%2F[^\s\]\)'\"]*", re.IGNORECASE)
MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
BROKEN_PERCENT_RE = re.compile(r"%(?:$|[^0-9A-Fa-f]|[0-9A-Fa-f]$)")


def _clean_body_text(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    out_lines: list[str] = []
    for line in raw.splitlines():
        x = LABEL_RE.sub(" ", line)
        x = BROKEN_SUUMO_RE.sub(" ", x)
        x = SRC_TOKEN_RE.sub(" ", x)
        x = GAZO_TOKEN_RE.sub(" ", x)
        x = MULTI_SPACE_RE.sub(" ", x).strip()
        if x:
            out_lines.append(x)
    return "\n".join(out_lines).strip()


def _is_url_broken(url: str) -> bool:
    u = str(url or "").strip()
    if not u:
        return True
    if not u.startswith("http"):
        return True
    if BROKEN_PERCENT_RE.search(u):
        return True
    if "suumo.com/jj/resize" in u.lower() and "src=" not in u.lower():
        return True
    return False


def _clean_image_urls(text: str) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    out: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        u = line.strip()
        if _is_url_broken(u):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return "\n".join(out)


def run(*, apply: bool) -> None:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, body_original, image_urls
            FROM source_items
            WHERE
              COALESCE(body_original,'') LIKE '%resize%'
              OR COALESCE(body_original,'') LIKE '%src=%'
              OR COALESCE(body_original,'') LIKE '%[財產調查圖片網址]%'
              OR COALESCE(body_original,'') LIKE '%[物件參考圖像 URL]%'
              OR COALESCE(image_urls,'') LIKE '%resize%'
              OR COALESCE(image_urls,'') LIKE '%img%%'
            """
        ).fetchall()

        changed = 0
        for r in rows:
            rid = int(r["id"])
            old_body = str(r["body_original"] or "")
            old_images = str(r["image_urls"] or "")
            new_body = _clean_body_text(old_body)
            new_images = _clean_image_urls(old_images)
            if new_body == old_body and new_images == old_images:
                continue
            changed += 1
            if apply:
                conn.execute(
                    """
                    UPDATE source_items
                    SET body_original = ?, image_urls = ?, last_checked_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (new_body, new_images, rid),
                )

        if apply:
            conn.commit()

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] scanned={len(rows)} changed={changed}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean broken SUUMO media fragments from source_items.")
    parser.add_argument("--apply", action="store_true", help="Apply updates to database (default: dry-run only).")
    args = parser.parse_args()
    run(apply=bool(args.apply))


if __name__ == "__main__":
    main()
