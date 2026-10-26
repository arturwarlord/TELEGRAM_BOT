from __future__ import annotations

import os
import sys
from pathlib import Path

# When invoked as "python scripts/check_telegram.py", Python adds the scripts/
# directory (not the repository root) to sys.path. Add the project root so
# the sibling deal_hunter package can be imported in GitHub Actions and locally.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deal_hunter.telegram_check import verify_telegram_setup


def main() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    print("===== TELEGRAM SETUP CHECK (READ-ONLY) =====")
    print("Safety: this check calls getMe/getChat/getChatMember only; it does not publish a message.")

    try:
        result = verify_telegram_setup(token, chat_id)
    except Exception as exc:
        print(f"[CHECK FAILED] {type(exc).__name__}: {exc}")
        return 1

    print(f"[OK] Token accepted for bot @{result['bot_username']} (ID {result['bot_id']}).")
    print(f"[OK] Channel resolved: {result['chat_title']} ({result['chat_id']}).")
    print(f"[OK] Chat type: {result['chat_type']}; bot status: {result['bot_status']}.")
    print("[OK] Bot has permission to post messages.")
    print("Telegram setup is ready. No post was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
