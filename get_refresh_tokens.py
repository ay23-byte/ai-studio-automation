# Run in VS Code terminal:
#   python get_refresh_tokens.py
#
# Keep client_secret.json in the same folder.
# Sign in using the Google account that owns your YouTube channel.
#
# Install dependencies if needed:
#   python -m pip install --upgrade google-auth-oauthlib google-auth-httplib2 google-api-python-client

from pathlib import Path
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

CLIENT_FILE = Path(__file__).resolve().parent / "client_secret.json"


def main():
    if not CLIENT_FILE.is_file():
        raise FileNotFoundError(
            f"client_secret.json is missing. Expected it here: {CLIENT_FILE}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_FILE),
        SCOPES,
    )

    creds = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent",
        success_message="YouTube authorization successful. You may close this browser tab.",
    )

    if not creds.refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. Check OAuth consent settings, "
            "revoke this app's access from your Google Account security settings, "
            "then run the script again and approve consent."
        )

    print("\n" + "=" * 64)
    print("YT_REFRESH_TOKEN =", creds.refresh_token)
    print("=" * 64)
    print("\nAdd this value to GitHub Actions Secrets:")
    print("  Name: YT_REFRESH_TOKEN")
    print("\nKeep this token private. Do not paste it into chat or commit it.")
    print("Your YT_CLIENT_ID and YT_CLIENT_SECRET secrets must also be configured.")


if __name__ == "__main__":
    main()
