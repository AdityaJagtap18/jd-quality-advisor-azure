"""Create (or update) an Azure AI Content Safety text blocklist and upload terms.

This is the "guardrail authoring" step for the JDQI demo. Azure AI Content
Safety ships with the four managed harm categories (Hate / SelfHarm / Sexual /
Violence) out of the box, but the discriminatory-hiring language the JDQI
pipeline needs to catch is domain-specific — so we express it as a custom
*blocklist* of exact terms/phrases. `agents/guardrails.py` references this
blocklist by name (AZURE_CONTENT_SAFETY_BLOCKLIST) on every analyze_text call.

Usage:
    source .venv/bin/activate
    python scripts/upload_blocklist.py                       # defaults below
    python scripts/upload_blocklist.py --file data/blocklist_terms.csv \
                                       --blocklist jdqi-blocklist

Requires (in .env or the shell environment):
    AZURE_CONTENT_SAFETY_ENDPOINT
    AZURE_CONTENT_SAFETY_KEY

Note: after editing a blocklist it can take up to ~5 minutes for changes to
take effect in analyze_text.

CSV format (header required):  term,description
"""

import argparse
import csv
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from azure.ai.contentsafety import BlocklistClient
from azure.ai.contentsafety.models import (
    AddOrUpdateTextBlocklistItemsOptions,
    TextBlocklist,
    TextBlocklistItem,
)
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError

_DEFAULT_FILE = "data/blocklist_terms.csv"
_DEFAULT_BLOCKLIST = "jdqi-blocklist"
_DEFAULT_DESCRIPTION = "JDQI discriminatory / exclusionary hiring language"


def _load_terms(path: Path) -> list[TextBlocklistItem]:
    """Read a term,description CSV into TextBlocklistItem objects."""
    items: list[TextBlocklistItem] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "term" not in reader.fieldnames:
            sys.exit(f"CSV {path} must have a 'term' column (header row required).")
        for row in reader:
            term = (row.get("term") or "").strip()
            if not term:
                continue
            items.append(
                TextBlocklistItem(text=term, description=(row.get("description") or "").strip())
            )
    if not items:
        sys.exit(f"No terms found in {path}.")
    return items


def main() -> None:
    load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Upload terms to an Azure Content Safety blocklist.")
    parser.add_argument("--file", default=_DEFAULT_FILE, help=f"CSV of terms (default: {_DEFAULT_FILE})")
    parser.add_argument(
        "--blocklist",
        default=os.getenv("AZURE_CONTENT_SAFETY_BLOCKLIST", _DEFAULT_BLOCKLIST),
        help="Blocklist name (default: env AZURE_CONTENT_SAFETY_BLOCKLIST or 'jdqi-blocklist')",
    )
    parser.add_argument("--description", default=_DEFAULT_DESCRIPTION)
    args = parser.parse_args()

    endpoint = os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT", "").strip()
    key = os.getenv("AZURE_CONTENT_SAFETY_KEY", "").strip()
    if not endpoint or not key:
        sys.exit("Set AZURE_CONTENT_SAFETY_ENDPOINT and AZURE_CONTENT_SAFETY_KEY (in .env).")

    csv_path = Path(args.file)
    if not csv_path.exists():
        sys.exit(f"File not found: {csv_path}")

    items = _load_terms(csv_path)
    client = BlocklistClient(endpoint, AzureKeyCredential(key))

    try:
        client.create_or_update_text_blocklist(
            blocklist_name=args.blocklist,
            options=TextBlocklist(blocklist_name=args.blocklist, description=args.description),
        )
        print(f"✅ Blocklist ready: {args.blocklist}")

        result = client.add_or_update_blocklist_items(
            blocklist_name=args.blocklist,
            options=AddOrUpdateTextBlocklistItemsOptions(blocklist_items=items),
        )
        print(f"✅ Uploaded {len(result.blocklist_items)} term(s):")
        for item in result.blocklist_items:
            print(f"   • {item.text}")
    except HttpResponseError as exc:
        msg = getattr(getattr(exc, "error", None), "message", None) or str(exc)
        sys.exit(f"❌ Azure Content Safety error: {msg}")

    print("\nNote: blocklist edits can take up to ~5 minutes to take effect in analyze_text.")


if __name__ == "__main__":
    main()
