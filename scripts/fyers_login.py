"""Interactive FYERS OAuth login for MyTradeView (same flow as the fyers_apiv3 SessionModel notebook).

Steps performed:
  1. Builds the FYERS authorization URL from FYERS_CLIENT_ID / FYERS_SECRET_KEY / FYERS_REDIRECT_URI (.env).
  2. Opens it in your browser (or prints it) so you log in + complete 2FA at FYERS.
  3. You paste the full redirected URL (or just the auth_code) at a MASKED prompt.
  4. Exchanges the auth code for the daily access token (SessionModel.generate_token).
  5. Saves it to FYERS_TOKEN_CACHE_PATH (default ./secrets/fyers_token.json).
  6. Verifies it with a real get_profile() call.

The auth code, access token and secret are never printed or logged.

Usage:
    .venv\\Scripts\\python scripts/fyers_login.py            # interactive login
    .venv\\Scripts\\python scripts/fyers_login.py --status   # verify the stored token only
    .venv\\Scripts\\python scripts/fyers_login.py --logout   # delete the stored token
"""

import argparse
import asyncio
import getpass
import sys
import webbrowser
from pathlib import Path

import dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
dotenv.load_dotenv(ROOT / ".env")

from src.broker.fyers_auth import FyersAuthError, FyersAuthManager  # noqa: E402


def print_status(st: dict) -> None:
    print(f"  authenticated : {st['authenticated']}")
    print(f"  reason        : {st['reason']}")
    print(f"  token stored  : {st['token_present']}")
    print(f"  verified at   : {st['validated_at']}")


async def main() -> int:
    ap = argparse.ArgumentParser(description="FYERS OAuth login for MyTradeView (PAPER mode).")
    ap.add_argument("--status", action="store_true", help="Validate the stored token via get_profile().")
    ap.add_argument("--logout", action="store_true", help="Delete the stored token.")
    ap.add_argument("--no-browser", action="store_true", help="Print the login URL instead of opening a browser.")
    args = ap.parse_args()

    auth = FyersAuthManager()
    print(f"Token cache path: {auth.token_cache_path}")

    if args.logout:
        auth.clear_token()
        print("Stored FYERS token deleted.")
        return 0

    if args.status:
        st = await auth.validate()
        print_status(st)
        return 0 if st["authenticated"] else 1

    if not auth.is_configured:
        print("ERROR: FYERS_CLIENT_ID, FYERS_SECRET_KEY and FYERS_REDIRECT_URI must be set in .env")
        return 2

    existing = await auth.validate()
    if existing["authenticated"]:
        print("A valid FYERS session for today already exists (verified via get_profile()). Nothing to do.")
        print("Use --logout first if you want to force a new login.")
        return 0

    url = auth.get_login_url()  # contains client_id + one-time state; no secret
    print("\nStep 1: Log in to FYERS (credentials + 2FA happen on the FYERS site).")
    if args.no_browser or not webbrowser.open(url):
        print("Open this URL in your browser:\n")
        print(url)
    else:
        print("Opened the FYERS login page in your browser.")
    print(
        f"\nStep 2: Complete the FYERS login and 2FA screens in the browser.\n"
        f"        Only after success, FYERS redirects to {auth.redirect_uri}?auth_code=...\n"
        "        The page may show 'cannot be reached' - that is expected.\n"
        "        Copy the FULL redirected callback URL from the browser address bar.\n"
        "        Do not paste the original generate-authcode URL."
    )
    pasted = getpass.getpass("\nPaste redirected URL or auth_code (input hidden): ").strip()
    if not pasted:
        print("Nothing pasted; aborted.")
        return 1

    try:
        st = await auth.exchange_auth_code(pasted)
    except FyersAuthError as e:
        print(f"\nLOGIN FAILED: {e}")
        return 1
    finally:
        pasted = ""  # drop reference

    print("\nResult:")
    print_status(st)
    if st["authenticated"]:
        print("\nSUCCESS: token stored and verified with FYERS get_profile().")
        print("Restart or keep FastAPI running; it validates the stored token automatically.")
        return 0
    print("\nToken exchange succeeded but FYERS profile verification failed; the session is NOT authenticated.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
