"""Local, user-authorized YouTube uploads for LocalAI Studio."""
import asyncio
import base64
import ctypes
import hashlib
import json
import logging
import os
import secrets
import subprocess
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

import jobs
import settings
from settings import CONFIG, DATA, FFPROBE

log = logging.getLogger("youtube")
CLIENT_PATH = CONFIG / "youtube_oauth_client.json"
TOKEN_PATH = DATA / "youtube_refresh_token.dpapi"
ACCOUNT_PATH = DATA / "youtube_account.json"
PUBLISHED_PATH = DATA / "youtube_published.json"
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
OAUTH_TIMEOUT_SECONDS = 900


def _target() -> str:
    """Channel uploads are locked to (Settings -> youtube_channel). Empty = the one connected channel."""
    return str(settings.get("youtube_channel") or "").strip().lstrip("@")


def _matches(channel: dict) -> bool:
    t = _target().casefold()
    if not t:
        return bool(channel.get("id"))
    return (channel.get("title", "").casefold() == t
            or channel.get("custom_url", "").strip("/@").casefold() == t)
_lock = threading.RLock()
_flow = None
_connection_error = None


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("YouTube credential protection is available on Windows only.")
    crypt32 = ctypes.WinDLL("Crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
                                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR),
                                           ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(_Blob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    raw = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
    output = _Blob()
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(source), "LocalAI YouTube credential", None, None, None, 0, ctypes.byref(output))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def _load_client() -> dict:
    if not CLIENT_PATH.exists():
        raise RuntimeError("Add your Google Desktop OAuth client file in LocalAI Settings first.")
    saved = json.loads(CLIENT_PATH.read_text(encoding="utf-8"))
    client = {k: saved[k] for k in ("client_id", "auth_uri", "token_uri") if saved.get(k)}
    client["client_secret"] = _dpapi(base64.b64decode(saved["client_secret_dpapi"]), False).decode("utf-8")
    if not client.get("client_id") or not client.get("client_secret"):
        raise RuntimeError("The saved Google OAuth client file is incomplete.")
    return client


def save_client(body: dict) -> dict:
    global _connection_error
    raw = body.get("installed") if isinstance(body, dict) else None
    if not isinstance(raw, dict):
        raise ValueError("Choose the JSON file for an OAuth client of type Desktop app (installed).")
    client_id, secret = raw.get("client_id"), raw.get("client_secret")
    if not isinstance(client_id, str) or not client_id.endswith(".apps.googleusercontent.com") or not isinstance(secret, str) or not secret:
        raise ValueError("That Google Desktop OAuth file is missing its client ID or client secret.")
    saved = {"client_id": client_id, "client_secret_dpapi": base64.b64encode(_dpapi(secret.encode("utf-8"), True)).decode("ascii"),
             "auth_uri": raw.get("auth_uri") or "https://accounts.google.com/o/oauth2/v2/auth",
             "token_uri": raw.get("token_uri") or "https://oauth2.googleapis.com/token"}
    CONFIG.mkdir(parents=True, exist_ok=True)
    old_client_id = None
    if CLIENT_PATH.exists():
        try:
            old_client_id = json.loads(CLIENT_PATH.read_text(encoding="utf-8")).get("client_id")
        except Exception:
            pass
    if old_client_id and old_client_id != client_id:
        TOKEN_PATH.unlink(missing_ok=True)
        ACCOUNT_PATH.unlink(missing_ok=True)
        _connection_error = None
    CLIENT_PATH.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return status()


def _read_account() -> dict:
    try:
        return json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def status() -> dict:
    account = _read_account()
    channel = account.get("channel") or {}
    channels = account.get("channels", [])
    return {"configured": CLIENT_PATH.exists(), "connected": TOKEN_PATH.exists(), "channel": account.get("channel"),
            "channels": channels, "target_channel": _target() or channel.get("title") or "",
            "target_ready": _matches(channel) and len(channels) == 1,
            "connecting": bool(_flow and _flow.get("active")), "connection_error": _connection_error}


def _save_account(channels: list[dict], selected_id: str | None = None):
    channels = [{"id": c.get("id"), "title": c.get("snippet", {}).get("title", ""),
                 "custom_url": c.get("snippet", {}).get("customUrl", "")} for c in channels]
    selected = next((c for c in channels if c["id"] == selected_id), None)
    if selected is None:
        selected = next((c for c in channels if _matches(c)), None)
    data = {"channels": channels, "channel": selected}
    ACCOUNT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


class _OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _flow
        query = parse_qs(urlparse(self.path).query)
        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        error = query.get("error", [""])[0]
        with _lock:
            flow = _flow
        if not flow or state != flow["state"]:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"This LocalAI authorization request is invalid. Return to LocalAI and try again.")
            return
        self.send_response(200 if code else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><meta charset='utf-8'><title>LocalAI</title><body><h2>Authorization received</h2><p>Return to LocalAI Studio.</p></body></html>" if code else b"Authorization was cancelled. Return to LocalAI Studio.")
        def complete():
            if not flow["future"].done():
                flow["future"].set_result({"code": code, "error": error})
        flow["loop"].call_soon_threadsafe(complete)

    def log_message(self, *_args):
        return


async def start_connect() -> dict:
    global _flow, _connection_error
    client = _load_client()
    with _lock:
        if _flow and _flow.get("active"):
            raise RuntimeError("A Google authorization is already in progress. Finish it in the browser first.")
        server = HTTPServer(("127.0.0.1", 0), _OAuthHandler)
        server.timeout = OAUTH_TIMEOUT_SECONDS
        redirect_uri = f"http://127.0.0.1:{server.server_port}"
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        _flow = {"active": True, "state": state, "verifier": verifier, "future": future, "loop": loop, "server": server}
        _connection_error = None
        thread = threading.Thread(target=server.handle_request, name="youtube-oauth-loopback", daemon=True)
        thread.start()
    params = {"client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
              "scope": f"{YOUTUBE_UPLOAD_SCOPE} {YOUTUBE_READ_SCOPE}", "access_type": "offline", "prompt": "consent",
              "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    asyncio.create_task(_finish_connect(_flow, client, redirect_uri))
    return {"auth_url": client.get("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth") + "?" + urlencode(params)}


def disconnect() -> dict:
    global _connection_error
    TOKEN_PATH.unlink(missing_ok=True)
    ACCOUNT_PATH.unlink(missing_ok=True)
    _connection_error = None
    return status()


async def _finish_connect(flow: dict, client: dict, redirect_uri: str):
    global _flow, _connection_error
    try:
        response = await asyncio.wait_for(flow["future"], timeout=OAUTH_TIMEOUT_SECONDS)
        if response.get("error"):
            raise RuntimeError("Google authorization was cancelled or denied.")
        async with httpx.AsyncClient(timeout=30) as http:
            token_res = await http.post(client.get("token_uri", "https://oauth2.googleapis.com/token"), data={
                "code": response["code"], "client_id": client["client_id"], "client_secret": client["client_secret"],
                "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": flow["verifier"]})
            token_res.raise_for_status()
            token = token_res.json()
            refresh = token.get("refresh_token")
            if not refresh:
                raise RuntimeError("Google did not return a refresh token. Disconnect LocalAI from your Google account and connect again.")
            access = token["access_token"]
            channel_res = await http.get("https://www.googleapis.com/youtube/v3/channels", params={
                "part": "snippet", "mine": "true", "maxResults": 50}, headers={"Authorization": f"Bearer {access}"})
            channel_res.raise_for_status()
            channels = channel_res.json().get("items", [])
        protected = _dpapi(refresh.encode("utf-8"), True)
        TOKEN_PATH.write_bytes(protected)
        _save_account(channels)
        flow["result"] = "connected"
        _connection_error = None
    except asyncio.TimeoutError:
        flow["result"] = "Authorization timed out. Try connecting again."
        _connection_error = flow["result"]
    except Exception as e:
        flow["result"] = str(e)[:300]
        _connection_error = flow["result"]
        log.warning("YouTube account connection did not complete: %s", type(e).__name__)
    finally:
        flow["active"] = False
        try:
            flow["server"].server_close()
        except Exception:
            pass
        with _lock:
            if _flow is flow:
                _flow = None


async def _access_token() -> str:
    client = _load_client()
    if not TOKEN_PATH.exists():
        raise RuntimeError("Connect your YouTube account in LocalAI Settings first.")
    refresh = _dpapi(TOKEN_PATH.read_bytes(), False).decode("utf-8")
    async with httpx.AsyncClient(timeout=30) as http:
        res = await http.post(client.get("token_uri", "https://oauth2.googleapis.com/token"), data={
            "client_id": client["client_id"], "client_secret": client["client_secret"], "refresh_token": refresh,
            "grant_type": "refresh_token"})
        if res.is_error:
            raise RuntimeError("YouTube authorization expired. Reconnect the account in LocalAI Settings.")
        return res.json()["access_token"]


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(2 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _published() -> list[dict]:
    try:
        return json.loads(PUBLISHED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def published_uploads() -> list[dict]:
    """Return safe, recent upload details for the UI; copyright results stay in Studio."""
    items = []
    for record in reversed(_published()[-20:]):
        video_id = str(record.get("video_id") or "").strip()
        if not video_id:
            continue
        items.append({
            "video_id": video_id,
            "title": str(record.get("title") or ""),
            "url": f"https://youtu.be/{video_id}",
            "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
            "privacy_status": record.get("privacy_status", "private"),
            "uploaded_at": record.get("uploaded_at"),
        })
    return items


async def run_upload(job):
    p = job.params
    source = jobs.resolve_input(p.get("file"))
    if not source or source.suffix.lower() not in (".mp4", ".mov", ".webm"):
        raise ValueError("Choose an exported video file from LocalAI Studio.")
    try:
        probe = subprocess.run([str(FFPROBE), "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height:format=duration", "-of", "json", str(source)],
                               capture_output=True, text=True, timeout=30, check=True)
        info = json.loads(probe.stdout)
        stream = info["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        duration = float(info.get("format", {}).get("duration") or 0)
    except Exception as e:
        raise ValueError("Could not verify this video file. Export it again from the Clips page.") from e
    if height < width:
        raise ValueError(f"YouTube Shorts need a square or vertical video. This export is {width}×{height}; export it as 9:16 first.")
    if duration <= 0 or duration > 180:
        raise ValueError("A YouTube Short must be no longer than 3 minutes.")
    account = _read_account()
    channel = account.get("channel") or {}
    if not _matches(channel):
        raise RuntimeError(f"The selected YouTube channel is not {_target() or 'connected'}. Choose the correct channel in Settings.")
    title = str(p.get("title") or "").strip()
    if not title or len(title) > 100:
        raise ValueError("Enter a YouTube title of 1–100 characters.")
    description = str(p.get("description") or "").strip()
    tags = [str(t).strip().lstrip("#") for t in p.get("tags", []) if str(t).strip()]
    tags = list(dict.fromkeys(t[:100] for t in tags))[:30]
    limited_tags, used_chars = [], 0
    for tag in tags:
        if used_chars + len(tag) > 500:
            break
        limited_tags.append(tag)
        used_chars += len(tag)
    tags = limited_tags
    hashtags = ["#" + str(h).strip().lstrip("#") for h in p.get("hashtags", []) if str(h).strip()]
    hashtags = list(dict.fromkeys(h for h in hashtags if all(ch.isalnum() or ch == "_" for ch in h[1:])))[:5]
    if hashtags:
        description = (description + "\n\n" if description else "") + " ".join(hashtags)
    if len(description) > 5000:
        raise ValueError("YouTube description is too long (maximum 5,000 characters).")
    privacy = p.get("privacy_status", "private")
    if privacy not in ("private", "unlisted", "public"):
        raise ValueError("Choose a valid video visibility setting.")
    file_hash = _file_hash(source)
    source_clip_id = str(p.get("source_clip_id") or "")
    prior = next((x for x in _published() if x.get("channel_id") == channel.get("id")
                  and (x.get("sha256") == file_hash or (source_clip_id and x.get("source_clip_id") == source_clip_id))), None)
    if prior:
        raise RuntimeError(f"This exact video was already uploaded to this channel: https://youtu.be/{prior['video_id']}")
    access = await _access_token()
    async with httpx.AsyncClient(timeout=30) as http:
        channels_res = await http.get("https://www.googleapis.com/youtube/v3/channels", params={
            "part": "snippet", "mine": "true", "maxResults": 50}, headers={"Authorization": f"Bearer {access}"})
        if channels_res.is_error:
            raise RuntimeError("Could not verify the active YouTube channel before upload.")
        live_channels = channels_res.json().get("items", [])
    if len(live_channels) != 1 or live_channels[0].get("id") != channel.get("id"):
        raise RuntimeError(f"Google does not confirm a single active {_target() or channel.get('title') or 'YouTube'} channel. Reconnect the intended channel before uploading.")
    size = source.stat().st_size
    mime = "video/mp4" if source.suffix.lower() == ".mp4" else "video/quicktime" if source.suffix.lower() == ".mov" else "video/webm"
    metadata = {"snippet": {"title": title, "description": description, "tags": tags},
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(p.get("made_for_kids", False))}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30, read=120, write=120, pool=30)) as http:
        init = await http.post("https://www.googleapis.com/upload/youtube/v3/videos", params={
            "uploadType": "resumable", "part": "snippet,status"},
            headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json; charset=UTF-8",
                     "X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)}, json=metadata)
        if init.is_error:
            detail = init.json().get("error", {}).get("message", "YouTube rejected the upload request.")
            raise RuntimeError(detail[:300])
        session_url = init.headers.get("Location")
        if not session_url:
            raise RuntimeError("YouTube did not return an upload session URL.")
        job.message = "Uploading video to YouTube…"
        job.progress = 0.03

        async def chunks():
            sent = 0
            with source.open("rb") as f:
                while chunk := await asyncio.to_thread(f.read, 8 * 1024 * 1024):
                    sent += len(chunk)
                    job.progress = min(0.97, 0.05 + 0.9 * sent / max(size, 1))
                    job.message = f"Uploading to YouTube… {round(100 * sent / max(size, 1))}%"
                    yield chunk

        uploaded = await http.put(session_url, headers={"Authorization": f"Bearer {access}", "Content-Type": mime,
                                                         "Content-Length": str(size)}, content=chunks())
        if uploaded.is_error:
            detail = uploaded.json().get("error", {}).get("message", "YouTube upload failed.")
            raise RuntimeError(detail[:300])
        video_id = uploaded.json().get("id")
    if not video_id:
        raise RuntimeError("YouTube finished the request but returned no video ID.")
    record = {"channel_id": channel["id"], "sha256": file_hash, "video_id": video_id, "title": title,
              "source_clip_id": source_clip_id, "privacy_status": privacy, "uploaded_at": time.time(), "source": jobs.rel(source)}
    records = _published()
    records.append(record)
    PUBLISHED_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    job.result = {"video_id": video_id, "url": f"https://youtu.be/{video_id}", "title": title,
                  "privacy_status": privacy, "channel": channel.get("title"), "width": width, "height": height,
                  "duration": round(duration, 2), "is_short_eligible": True}
    job.message = "Uploaded to YouTube"
