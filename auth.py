"""
One-time OAuth flow: takes a Google Cloud OAuth client_secrets.json,
runs the browser-based InstalledAppFlow, and saves token.json with the
refresh token. Re-run this if scopes change or the refresh token gets revoked.

Usage:
    python auth.py [path/to/client_secret.json]

If no path given, looks for credentials/client_secret*.json in the project dir.
"""

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SCRIPT_DIR = Path(__file__).resolve().parent


def find_client_secret() -> Path:
    if len(sys.argv) > 1:
        p = Path(sys.argv[1]).expanduser().resolve()
        if not p.exists():
            sys.exit(f"file not found: {p}")
        return p

    candidates = list((SCRIPT_DIR / "credentials").glob("client_secret*.json"))
    if not candidates:
        sys.exit(
            "no client_secret*.json found in credentials/.\n"
            "place your downloaded OAuth client JSON there, "
            "or pass the path as an argument."
        )
    return candidates[0]


def main():
    client_secret_path = find_client_secret()
    print(f"using OAuth client: {client_secret_path}")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    token_path = SCRIPT_DIR / "token.json"
    token_path.write_text(creds.to_json())
    print(f"saved token to {token_path}")

    # also print the JSON content for copying into GOOGLE_OAUTH_TOKEN secret
    print("\n--- copy this for GitHub Actions secret GOOGLE_OAUTH_TOKEN ---")
    print(creds.to_json())
    print("--- end ---")


if __name__ == "__main__":
    main()
