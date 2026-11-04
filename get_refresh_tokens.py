"""
Generate a Google OAuth refresh token for YouTube Data API uploads.

SETUP
1. Enable YouTube Data API v3 in Google Cloud Console.
2. Create an OAuth Client ID with application type "Desktop app".
3. Download its JSON file and save beside this script as client_secret.json.
4. Install dependencies:
   python -m pip install --upgrade google-auth-oauthlib google-auth-httplib2 google-api-python-client
5. Run:
   python get_refresh_tokens.py

Sign in with the YouTube account/channel that should receive uploads.
Copy the printed refresh token to GitHub Actions secret YT_REFRESH_TOKEN.

SECURITY
Never share the refresh token or client_secret.json. Both are ignored by
.gitignore. Do not commit credentials or paste them into chat.
"""

from pathlib import Path
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET_FILE = Path(__file__).resolve().parent / "client_secret.json"


def main():
    if not CLIENT_SECRET_FILE.is_file():
        print("ERROR: client_secret.json was not found.")
        print(f"Expected location: {CLIENT_SECRET_FILE}")
        print("Download OAuth credentials for a Desktop app and save them")
        print("beside this script with the exact name client_secret.json.")
        sys.exit(1)

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CLIENT_SECRET_FILE),
            scopes=SCOPES,
        )
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=True,
            success_message="YouTube authorization successful. You may close this tab.",
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
        )

        if not credentials.refresh_token:
            print("\nGoogle did not return a refresh token.")
            print("Revoke this app's access in your Google Account security settings,")
            print("then run this script again and approve the consent screen.")
            sys.exit(2)

        print("\n" + "=" * 64)
        print("YOUTUBE OAUTH REFRESH TOKEN")
        print("=" * 64)
        print(credentials.refresh_token)
        print("=" * 64)
        print("\nAdd the value above to GitHub Actions secret: YT_REFRESH_TOKEN")
        print("Keep it private. Do not paste it into chat or commit it to Git.")
        print("Your existing YT_CLIENT_ID and YT_CLIENT_SECRET secrets are also required.")

    except Exception as exc:
        print(f"\nOAuth failed: {type(exc).__name__}: {exc}")
        print("Check that YouTube Data API v3 is enabled, the JSON is a Desktop")
        print("OAuth client credential, and your Google account is allowed by")
        print("the OAuth consent-screen configuration.")
        sys.exit(1)


if __name__ == "__main__":
    main()
