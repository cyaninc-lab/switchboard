"""Bug reports straight to GitHub issues: a GitHub App's device sign-in (no secret, no server), then the issue is filed
as the person. The app only has Issues read/write on the Switchboard repo, so the token can do nothing else."""
import os
import aiohttp

CLIENT_ID = "Iv23ctIss8XRUpl0dpco"  # the "Switchboard reports" GitHub App (public: device flow needs no secret)
REPO = "cyaninc-lab/switchboard"
WEB = os.environ.get("SWITCHBOARD_GITHUB_WEB", "https://github.com")  # overridable for tests
API = os.environ.get("SWITCHBOARD_GITHUB_API", "https://api.github.com")
TOKEN, LOGIN = "github_token", "github_login"  # daemon settings; ponytail: in the SQLite file like other tokens, keychain later
MAX_BODY = 60000  # GitHub caps an issue body at 65536 characters


async def _post(url, data=None, json=None, token=None):
    h = {"Accept": "application/json", "User-Agent": "Switchboard"}
    if token:
        h |= {"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.post(url, data=data, json=json, headers=h) as r:
            return r.status, await r.json(content_type=None)


async def start():
    """A code for the person to enter at github.com/login/device."""
    _, d = await _post(f"{WEB}/login/device/code", data={"client_id": CLIENT_ID})
    if "device_code" not in d:
        raise RuntimeError(d.get("error_description") or d.get("error") or "github_unavailable")
    return {k: d[k] for k in ("device_code", "user_code", "verification_uri", "expires_in", "interval")}


async def poll(store, device_code):
    """pending / slow_down until they approve; then the token is kept and the GitHub login returned."""
    _, d = await _post(f"{WEB}/login/oauth/access_token",
                       data={"client_id": CLIENT_ID, "device_code": device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    if "access_token" not in d:
        err = d.get("error", "unknown")
        return {"status": {"authorization_pending": "pending", "slow_down": "slow_down", "expired_token": "expired", "access_denied": "denied"}.get(err, err)}
    token = d["access_token"]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.get(f"{API}/user", headers={"Authorization": f"Bearer {token}", "User-Agent": "Switchboard"}) as r:
            login = (await r.json(content_type=None)).get("login", "")
    store.set_setting(TOKEN, token)
    store.set_setting(LOGIN, login)
    return {"status": "done", "login": login}


def status(store):
    return {"signed_in": bool(store.setting(TOKEN)), "login": store.setting(LOGIN) or None}


def sign_out(store):
    store.set_setting(TOKEN, "")
    store.set_setting(LOGIN, "")


async def file_issue(store, title, body):
    token = store.setting(TOKEN)
    if not token:
        raise PermissionError("not_signed_in")
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n\n…(cut to fit GitHub's limit)"
    code, d = await _post(f"{API}/repos/{REPO}/issues", json={"title": title[:200] or "Bug report", "body": body, "labels": ["bug"]}, token=token)
    if code == 401:  # revoked on GitHub: sign in again
        sign_out(store)
        raise PermissionError("not_signed_in")
    if code >= 300:
        raise RuntimeError(d.get("message") or f"github_{code}")
    return {"url": d["html_url"], "number": d["number"]}
