"""Connect your Strava account once: approve access in the browser, and the refresh
token is saved to backend/.env (it's never printed).

Before running, set your Strava API app's "Authorization Callback Domain" to
`localhost` (https://www.strava.com/settings/api). Then, from the lifts/ directory:

    python -m lift_vertical.strava_connect

Copy the new STRAVA_REFRESH_TOKEN from backend/.env into Render's Environment settings
for the live site.
"""

import argparse
import re
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
from dotenv import dotenv_values

from .strava import TOKEN_URL

PORT = 8723
REDIRECT_URI = f"http://localhost:{PORT}/exchange"
SCOPE = "read,activity:read_all"
DEFAULT_ENV = Path(__file__).resolve().parents[2] / "backend" / ".env"


def save_refresh_token(env_file: Path, token: str) -> None:
    """Replace (or add) the STRAVA_REFRESH_TOKEN line, leaving the rest of the file alone."""
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    line = f"STRAVA_REFRESH_TOKEN={token}"
    if re.search(r"^STRAVA_REFRESH_TOKEN=.*$", text, flags=re.M):
        text = re.sub(r"^STRAVA_REFRESH_TOKEN=.*$", lambda _: line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    env_file.write_text(text, encoding="utf-8")


def wait_for_code() -> dict[str, str]:
    """Run a one-request local server that receives Strava's redirect."""
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in query.items()})
            ok = "code" in result
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            message = "Strava connected. You can close this tab." if ok else "Strava access wasn't granted."
            self.wfile.write(f"<p style='font-family:sans-serif'>{message}</p>".encode())
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args) -> None:  # keep the code out of the console
            pass

    HTTPServer(("localhost", PORT), Handler).serve_forever()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV, help="The .env file to read and update")
    args = parser.parse_args()

    env = dotenv_values(args.env)
    client_id, client_secret = env.get("STRAVA_CLIENT_ID"), env.get("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit(f"Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in {args.env} first.")

    url = "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": REDIRECT_URI, "response_type": "code",
        "approval_prompt": "force", "scope": SCOPE,
    })
    print("Opening Strava in your browser to approve access. If it doesn't open, visit:\n" + url)
    webbrowser.open(url)
    result = wait_for_code()
    if "code" not in result:
        raise SystemExit(f"Not connected: {result.get('error', 'no code returned')}")
    if "activity:read" not in result.get("scope", ""):
        raise SystemExit("Approved without activity access. Run again and leave 'View data about your activities' ticked.")

    response = httpx.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret, "code": result["code"], "grant_type": "authorization_code",
    }, timeout=20)
    if not response.is_success:
        raise SystemExit(f"Token exchange failed ({response.status_code}).")
    body = response.json()
    save_refresh_token(args.env, body["refresh_token"])
    name = (body.get("athlete") or {}).get("firstname", "")
    print(f"Connected{' as ' + name if name else ''} with scopes: {result['scope']}. Saved the refresh token to {args.env}.")


if __name__ == "__main__":
    main()
