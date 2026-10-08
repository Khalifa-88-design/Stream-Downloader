import re
import threading
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request, send_from_directory
import yt_dlp

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
jobs = {}


class AppLogger:
    def debug(self, msg):
        # Keep normal yt-dlp debug noise out of the browser.
        pass

    def warning(self, msg):
        print("yt-dlp warning:", strip_ansi(str(msg)))

    def error(self, msg):
        print("yt-dlp error:", strip_ansi(str(msg)))


def strip_ansi(text):
    return re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)


def friendly_error(exc):
    raw = strip_ansi(str(exc)).strip()
    lower = raw.lower()

    if "unsupported url" in lower:
        return {
            "code": "unsupported",
            "message": (
                "This webpage is not supported by yt-dlp. "
                "This does not mean the site is offline; it means yt-dlp "
                "does not currently have an extractor for this page."
            ),
        }

    if "timed out" in lower or "read timed out" in lower or "connectionpool" in lower:
        return {
            "code": "timeout",
            "message": (
                "The media server timed out while sending data. "
                "The app now retries slow connections automatically. "
                "If it continues, try again or use a different quality."
            ),
        }

    if "requested format is not available" in lower:
        return {
            "code": "format",
            "message": (
                "That exact format is unavailable for this video. "
                "Choose 'Best available' or another detected quality."
            ),
        }

    if "ffmpeg" in lower and ("not found" in lower or "not installed" in lower):
        return {
            "code": "ffmpeg",
            "message": (
                "FFmpeg is required to merge separate video and audio streams. "
                "Install FFmpeg and restart the application."
            ),
        }

    return {"code": "error", "message": raw or "The operation failed."}


def common_options():
    return {
        "quiet": True,
        "no_warnings": False,
        "logger": AppLogger(),
        "noplaylist": True,

        # YouTube currently needs a supported JS runtime for full extraction.
        # If Deno is installed and on PATH, yt-dlp will use it.
        "js_runtimes": {"deno": {}},

        # Network resilience for slow/CDN connections.
        "socket_timeout": 90,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "retry_sleep_functions": {
            "http": lambda n: min(2 * n, 10),
            "fragment": lambda n: min(2 * n, 10),
        },

        # One fragment at a time is slower but more reliable on unstable links.
        "concurrent_fragment_downloads": 1,

        "paths": {"home": str(DOWNLOAD_DIR)},
        "outtmpl": {"default": "%(title)s [%(id)s].%(ext)s"},
    }


def is_public_http_url(url):
    """Allow normal public HTTP(S) URLs and avoid localhost/private-network SSRF."""
    try:
        import ipaddress, socket
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        host = p.hostname.lower()
        if host in {"localhost", "localhost.localdomain"}:
            return False
        try:
            infos = socket.getaddrinfo(host, None)
            for item in infos:
                addr = item[4][0]
                ip = ipaddress.ip_address(addr)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return False
        except socket.gaierror:
            return False
        return True
    except Exception:
        return False


def discover_media_urls(page_url):
    """Find openly exposed media URLs in a normal HTML page.

    This is a compatibility fallback only. It does not execute site JavaScript,
    defeat DRM, bypass login/paywalls, or reverse engineer protected players.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(page_url, headers=headers, timeout=(15, 45), allow_redirects=True)
    r.raise_for_status()
    content_type = (r.headers.get("content-type") or "").lower()
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        return [r.url]

    soup = BeautifulSoup(r.text, "html.parser")
    found = []

    def add(u):
        if not u:
            return
        u = urljoin(r.url, u.strip())
        if u.startswith(("http://", "https://")) and u not in found:
            found.append(u)

    # OpenGraph / Twitter metadata
    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in {"og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"}:
            add(tag.get("content"))

    # Explicit HTML media elements
    for tag in soup.find_all(["video", "source", "audio"]):
        add(tag.get("src"))
        for source in tag.find_all("source"):
            add(source.get("src"))

    # Common JSON / JS references to openly exposed media files.
    text = r.text
    patterns = [
        r'https?://[^"\'\\<>\s]+\.(?:mp4|webm|m4v|mov|mp3|m4a|aac|wav)(?:\?[^"\'\\<>\s]*)?',
        r'https?://[^"\'\\<>\s]+\.m3u8(?:\?[^"\'\\<>\s]*)?',
        r'https?://[^"\'\\<>\s]+\.mpd(?:\?[^"\'\\<>\s]*)?',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.I):
            add(match.replace("\\/", "/"))

    return found[:20]


def try_generic_page_fallback(url):
    """Return an openly exposed media URL if one is discoverable."""
    for candidate in discover_media_urls(url):
        try:
            options = common_options()
            options["skip_download"] = True
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(candidate, download=False)
            if info:
                return info, candidate
        except Exception:
            continue
    return None, None


def valid_url(url):
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def format_error_response(exc, status=422):
    info = friendly_error(exc)
    return jsonify({
        "ok": False,
        "error": info["message"],
        "error_code": info["code"],
    }), status


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/analyze")
def analyze():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not valid_url(url):
        return jsonify({
            "ok": False,
            "error": "Please enter a complete HTTP or HTTPS URL.",
            "error_code": "invalid_url",
        }), 400

    options = common_options()
    options["skip_download"] = True

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            return jsonify({
                "ok": False,
                "error": "No media information was returned for this URL.",
                "error_code": "no_media",
            }), 422

        formats = []
        seen = set()

        for f in info.get("formats", []):
            v = f.get("vcodec") not in (None, "none")
            a = f.get("acodec") not in (None, "none")
            height = f.get("height")

            if not v and not a:
                continue

            # UI only needs meaningful video/audio options.
            key = (
                height, f.get("ext"), v, a,
                round(float(f.get("fps") or 0), 1),
            )
            if key in seen:
                continue
            seen.add(key)

            formats.append({
                "format_id": f.get("format_id"),
                "height": height,
                "width": f.get("width"),
                "ext": f.get("ext"),
                "fps": f.get("fps"),
                "has_video": v,
                "has_audio": a,
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "tbr": f.get("tbr"),
            })

        heights = sorted(
            {f["height"] for f in formats if f["height"]},
            reverse=True,
        )

        return jsonify({
            "ok": True,
            "title": info.get("title") or "Untitled",
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader") or info.get("channel"),
            "site": info.get("extractor_key") or info.get("extractor") or "Generic",
            "webpage_url": info.get("webpage_url") or url,
            "qualities": heights,
            "formats": formats[:150],
        })

    except Exception as exc:
        # Second-pass compatibility layer for ordinary webpages that expose
        # an HTML5 video, OpenGraph video, HLS, DASH, or direct media URL.
        try:
            fallback_info, resolved_url = try_generic_page_fallback(url)
            if fallback_info:
                formats = []
                seen = set()
                for f in fallback_info.get("formats", []):
                    v = f.get("vcodec") not in (None, "none")
                    a = f.get("acodec") not in (None, "none")
                    if not v and not a:
                        continue
                    key = (f.get("height"), f.get("ext"), v, a)
                    if key in seen:
                        continue
                    seen.add(key)
                    formats.append({
                        "format_id": f.get("format_id"),
                        "height": f.get("height"),
                        "width": f.get("width"),
                        "ext": f.get("ext"),
                        "fps": f.get("fps"),
                        "has_video": v,
                        "has_audio": a,
                        "filesize": f.get("filesize") or f.get("filesize_approx"),
                        "tbr": f.get("tbr"),
                    })
                return jsonify({
                    "ok": True,
                    "title": fallback_info.get("title") or "Detected media",
                    "thumbnail": fallback_info.get("thumbnail"),
                    "duration": fallback_info.get("duration"),
                    "uploader": fallback_info.get("uploader") or fallback_info.get("channel"),
                    "site": fallback_info.get("extractor_key") or "Generic media",
                    "webpage_url": resolved_url,
                    "source_url": url,
                    "qualities": sorted({f["height"] for f in formats if f["height"]}, reverse=True),
                    "formats": formats[:150],
                    "fallback": True,
                })
        except Exception:
            pass
        return format_error_response(exc)


def progress_hook(job_id):
    def hook(d):
        job = jobs.get(job_id)
        if not job:
            return

        status = d.get("status")

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            percent = (done / total * 100) if total else 0

            job.update({
                "status": "downloading",
                "percent": round(percent, 1),
                "speed": d.get("_speed_str") or d.get("speed_str") or "",
                "eta": d.get("_eta_str") or d.get("eta_str") or "",
                "filename": d.get("filename", ""),
            })

        elif status == "finished":
            job.update({
                "status": "processing",
                "percent": 100,
                "speed": "",
                "eta": "",
            })

    return hook


def latest_download(before_names):
    candidates = [
        p for p in DOWNLOAD_DIR.iterdir()
        if p.is_file() and p.name not in before_names
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def run_download(job_id, url, quality, mode):
    job = jobs[job_id]
    before_names = {p.name for p in DOWNLOAD_DIR.iterdir() if p.is_file()}

    options = common_options()
    options["progress_hooks"] = [progress_hook(job_id)]
    options["merge_output_format"] = "mp4"

    if mode == "audio":
        options["format"] = "bestaudio/best"
        options["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        if quality == "best":
            options["format"] = "bestvideo*+bestaudio/best"
        else:
            try:
                height = int(quality)
                options["format"] = (
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}]/best"
                )
            except (TypeError, ValueError):
                options["format"] = "bestvideo*+bestaudio/best"

    try:
        job["status"] = "starting"

        download_url = url

        # If the page itself is not directly extractable, discover an openly
        # exposed media URL and pass that URL back through yt-dlp.
        try:
            options_probe = common_options()
            options_probe["skip_download"] = True
            with yt_dlp.YoutubeDL(options_probe) as probe:
                probe.extract_info(url, download=False)
        except Exception:
            try:
                _, resolved = try_generic_page_fallback(url)
                if resolved:
                    download_url = resolved
            except Exception:
                pass

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([download_url])

        output = latest_download(before_names)

        if output:
            job.update({
                "status": "completed",
                "percent": 100,
                "file": output.name,
                "error": None,
                "error_code": None,
            })
        else:
            job.update({
                "status": "completed",
                "percent": 100,
                "file": None,
            })

    except Exception as exc:
        info = friendly_error(exc)
        job.update({
            "status": "error",
            "error": info["message"],
            "error_code": info["code"],
        })


@app.post("/api/download")
def start_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    quality = data.get("quality") or "best"
    mode = data.get("mode") or "video"

    if not valid_url(url):
        return jsonify({
            "ok": False,
            "error": "Please enter a complete HTTP or HTTPS URL.",
            "error_code": "invalid_url",
        }), 400

    job_id = uuid.uuid4().hex
    jobs[job_id] = {
        "status": "starting",
        "percent": 0,
        "speed": "",
        "eta": "",
        "file": None,
        "error": None,
        "error_code": None,
    }

    threading.Thread(
        target=run_download,
        args=(job_id, url, quality, mode),
        daemon=True,
    ).start()

    return jsonify({"ok": True, "job_id": job_id})


@app.get("/api/progress/<job_id>")
def progress(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({
            "ok": False,
            "error": "Download job not found.",
        }), 404

    return jsonify({"ok": True, **job})


@app.get("/downloads/<path:filename>")
def download_file(filename):
    return send_from_directory(
        DOWNLOAD_DIR,
        filename,
        as_attachment=True,
    )


@app.post("/api/open-site")
def open_site():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not valid_url(url):
        return jsonify({"ok": False, "error": "Invalid URL."}), 400

    return jsonify({"ok": True, "url": url})


if __name__ == "__main__":
    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000,
        threaded=True,
    )
