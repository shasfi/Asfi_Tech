#!/usr/bin/env python3
"""
scripts/get_refresh_token.py

RUN THIS ONCE, ON YOUR OWN COMPUTER (not in GitHub Actions) — it opens a
browser window for you to log into the "AI With Asfi" YouTube channel and
grant upload permission. It then prints a REFRESH TOKEN that GitHub Actions
will reuse forever after (until you revoke it), so this is a one-time step.

SETUP BEFORE RUNNING:
1. Go to https://console.cloud.google.com/ -> the SAME project where you
   already enabled "YouTube Data API v3".
2. Go to "APIs & Services" -> "Credentials" -> "Create Credentials" ->
   "OAuth client ID".
   - If asked, configure the "OAuth consent screen" first: User type =
     "External", app name = anything (e.g. "Asfi Tech YT Uploader"), your
     email as support/developer contact. Under "Test users", add YOUR OWN
     Google account email (the one that owns/manages the YouTube channel).
   - Application type = "Desktop app".
   - Download the JSON — save it as `client_secret.json` in this same
     `scripts/` folder (DO NOT commit this file to GitHub — it's ignored
     via .gitignore).
3. On your own computer (needs Python installed):
     pip install google-auth-oauthlib google-api-python-client
     python scripts/get_refresh_token.py
4. A browser window opens -> log in with the Google account that owns the
   YouTube channel -> approve access. You may see an "unverified app"
   warning since this is your own personal OAuth client — click
   "Advanced" -> "Go to Asfi Tech YT Uploader (unsafe)" to proceed; this
   is expected for personal-use apps that haven't gone through Google's
   full verification review, and is safe since it's your own app.
5. The script prints a REFRESH TOKEN. Copy it and add it as a GitHub
   Actions secret named YT_REFRESH_TOKEN (along with YT_CLIENT_ID and
   YT_CLIENT_SECRET from the same client_secret.json file).
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS_FILE = "client_secret.json"


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n" + "=" * 70)
    print("SUCCESS — save these three values as GitHub Actions secrets:")
    print("=" * 70)
    print(f"YT_CLIENT_ID     = {creds.client_id}")
    print(f"YT_CLIENT_SECRET = {creds.client_secret}")
    print(f"YT_REFRESH_TOKEN = {creds.refresh_token}")
    print("=" * 70)
    print("This refresh token does not expire unless you revoke access at")
    print("https://myaccount.google.com/permissions — keep it secret, it")
    print("grants upload access to your channel.")


if __name__ == "__main__":
    main()
