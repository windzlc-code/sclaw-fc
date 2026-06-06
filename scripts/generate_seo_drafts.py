import argparse
import os
import sys

from src.db import init_db
from src.seo_draft_service import generate_seo_drafts


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SEO drafts from hot keywords.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-count", type=int, default=1)
    parser.add_argument(
        "--gemini",
        action="store_true",
        help="Use AI (DeepSeek/Gemini per LLM_ACTIVE_PROVIDER; requires matching API keys in .env or admin app_kv).",
    )
    parser.add_argument("--persona-region", type=str, default=os.getenv("SEO_GEMINI_REGION", "tw"))
    parser.add_argument(
        "--persona-category",
        type=str,
        default=os.getenv("SEO_GEMINI_CATEGORY", "finance_workplace"),
    )
    parser.add_argument("--gemini-model", type=str, default=os.getenv("GEMINI_MODEL", ""))
    parser.add_argument(
        "--llm-provider",
        type=str,
        default=os.getenv("LLM_CLI_PROVIDER", ""),
        help="Override provider for this run: deepseek | gemini (empty = server default).",
    )
    args = parser.parse_args()

    init_db()
    result = generate_seo_drafts(
        limit=args.limit,
        min_count=args.min_count,
        use_gemini=args.gemini,
        persona_region=args.persona_region,
        persona_category=args.persona_category,
        gemini_model=args.gemini_model,
        llm_provider=args.llm_provider,
    )
    if result.get("ok") is False:
        print(result.get("message") or "SEO draft generation aborted.", file=sys.stderr)
        raise SystemExit(1)
    print(
        "SEO draft generation completed. "
        f"generated={result['generated']} count={result['count']} "
        f"created={result.get('created', 0)} updated={result.get('updated', 0)} "
        f"message={result['message']}"
    )


if __name__ == "__main__":
    main()
