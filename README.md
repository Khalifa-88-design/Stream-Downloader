# StreamForge — Fixed Version

This version fixes the two errors shown in the screenshots as far as the application can safely handle them.

## Error 1: Googlevideo / ConnectionPool Read timed out

This is a network/CDN timeout while yt-dlp is downloading the media bytes. The app now uses:

- 90-second socket timeout
- 10 HTTP retries
- 10 fragment retries
- 5 file-access retries
- retry backoff
- one fragment at a time for better reliability
- clean browser error messages instead of raw ANSI traceback text

A timeout cannot be guaranteed to disappear because the remote media server/CDN is outside the Flask application.

## Error 2: Unsupported URL

A URL such as:

https://netfilm.world/spa/videoPlayPage/movies/...

can produce `Unsupported URL` when yt-dlp has no extractor for that page. yt-dlp's own FAQ says support must be tested by passing the URL to yt-dlp; its generic extractor can match URLs but may still end with `Unsupported URL`.

The app now handles this gracefully and gives an **Open in browser** button.

It does NOT scrape or bypass DRM, paywalls, authentication, geo restrictions, or protected streaming.

## Windows installation

PowerShell:

```powershell
cd streamforge_fixed

py -m venv .venv
.venv\Scripts\activate

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Install Deno:

```powershell
irm https://deno.land/install.ps1 | iex
```

Close/reopen PowerShell, then:

```powershell
deno --version
```

Install FFmpeg:

```powershell
winget install Gyan.FFmpeg
```

Verify:

```powershell
ffmpeg -version
```

Run:

```powershell
python app.py
```

Open:

http://127.0.0.1:5000

## Updating yt-dlp later

```powershell
python -m pip install -U "yt-dlp[default]"
```

## If YouTube still times out

Try again first. A different quality can also reduce load. If the same Googlevideo host repeatedly times out, test:

```powershell
python -m yt_dlp -vU "YOUR_URL"
```

The verbose output is useful for diagnosing whether the problem is the local network, proxy/VPN, CDN, or extractor.


## Universal URL mode

This release adds a second-pass resolver. When yt-dlp cannot extract the original page, StreamForge checks the public HTML for:

- HTML5 `<video>` / `<source>` / `<audio>` URLs
- OpenGraph `og:video` metadata
- Twitter player stream metadata
- openly referenced `.mp4`, `.webm`, `.m4v`, `.mov`, `.mp3`, `.m4a`, `.m3u8`, and `.mpd` URLs

If a media URL is found, it is passed back to yt-dlp. This makes the application more compatible with ordinary webpages that expose their media directly.

It is **not technically possible to guarantee every URL will work**. yt-dlp itself says that its extractor list is not a guarantee, and that unsupported sites can change. Some players require JavaScript execution, login/session cookies, signed URLs, or DRM. StreamForge does not bypass DRM, authentication, paywalls, or access controls.
