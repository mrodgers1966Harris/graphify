# Video transcription using faster-whisper
# Converts video/audio files to text transcripts for graph extraction
from __future__ import annotations

import os
from pathlib import Path

from graphify.paths import out_path as _out_path


VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v', '.mp3', '.wav', '.m4a', '.ogg'}
URL_PREFIXES = ('http://', 'https://', 'www.')

_DEFAULT_MODEL = "base"
_TRANSCRIPTS_DIR = str(_out_path("transcripts"))
_FALLBACK_PROMPT = "Use proper punctuation and paragraph breaks."


def _model_name() -> str:
    return os.environ.get("GRAPHIFY_WHISPER_MODEL", _DEFAULT_MODEL)


def _get_whisper():
    try:
        from faster_whisper import WhisperModel
        return WhisperModel
    except ImportError as exc:
        raise ImportError(
            "Video transcription requires faster-whisper. "
            "Run: pip install 'graphifyy[video]'"
        ) from exc


def _get_yt_dlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError as exc:
        raise ImportError(
            "YouTube/URL download requires yt-dlp. "
            "Run: pip install 'graphifyy[video]'"
        ) from exc


def _guarded_youtube_dl_class(yt_dlp):
    """Return a YoutubeDL subclass whose every network connection is SSRF-checked.

    yt-dlp follows redirects, playlist entries and fragment URLs on its own, so
    validating only the URL handed to it is not enough. This class:

    - routes all requests through one urllib-based handler (no requests /
      curl_cffi / websockets handlers, no proxy, ftp or file support) whose
      sockets are opened by ``_connect_validated``: the address that is
      checked is the exact address that is connected to, so redirects,
      media URLs and DNS rebinding cannot reach a private/reserved IP;
    - refuses downloads that yt-dlp would hand to an external program
      (ffmpeg, rtmpdump, aria2c, ...), since those open their own sockets.
    """
    import functools
    import http.client
    import ipaddress
    import urllib.request

    from yt_dlp.downloader import get_suitable_downloader
    from yt_dlp.downloader.dash import DashSegmentsFD
    from yt_dlp.downloader.hls import HlsFD
    from yt_dlp.downloader.http import HttpFD
    from yt_dlp.networking import _helper, _urllib
    from yt_dlp.utils import DownloadError

    from graphify.security import _ip_is_blocked

    def _connect_validated(addrinfo, timeout, source_address):
        addr = addrinfo[4][0]
        try:
            blocked = _ip_is_blocked(ipaddress.ip_address(addr))
        except ValueError:
            blocked = True
        if blocked:
            raise OSError(f"SSRF blocked: refusing to connect to private/reserved address {addr}")
        return _helper._socket_connect(addrinfo, timeout, source_address)

    _create_guarded_connection = functools.partial(
        _helper.create_connection, _create_socket_func=_connect_validated,
    )

    def _guarded_http_connection(http_class, source_address, *args, **kwargs):
        conn = http_class(*args, **kwargs)
        conn._create_connection = _create_guarded_connection
        if source_address is not None:
            conn.source_address = (source_address, 0)
        return conn

    class _GuardedHTTPHandler(_urllib.HTTPHandler):
        def http_open(self, req):
            return self.do_open(functools.partial(
                _guarded_http_connection, http.client.HTTPConnection, self._source_address), req)

        def https_open(self, req):
            return self.do_open(functools.partial(
                _guarded_http_connection, http.client.HTTPSConnection, self._source_address),
                req, context=self._context)

    class GraphifyGuardedRH(_urllib.UrllibRH):
        _SUPPORTED_URL_SCHEMES = ('http', 'https')
        RH_NAME = 'graphify-guarded-urllib'

        def _create_instance(self, proxies, cookiejar, legacy_ssl_support=None):
            opener = urllib.request.OpenerDirector()
            for handler in (
                _GuardedHTTPHandler(
                    debuglevel=int(bool(self.verbose)),
                    context=self._make_sslcontext(legacy_ssl_support=legacy_ssl_support),
                    source_address=self.source_address),
                urllib.request.HTTPCookieProcessor(cookiejar),
                urllib.request.UnknownHandler(),
                urllib.request.HTTPDefaultErrorHandler(),
                urllib.request.HTTPErrorProcessor(),
                _urllib.RedirectHandler(),
            ):
                opener.add_handler(handler)
            opener.addheaders = []
            return opener

    native_downloaders = (HttpFD, HlsFD, DashSegmentsFD)

    class GuardedYoutubeDL(yt_dlp.YoutubeDL):
        def build_request_director(self, handlers, preferences=None):
            return super().build_request_director([GraphifyGuardedRH], preferences)

        def dl(self, name, info, subtitle=False, test=False):
            if info.get('url'):
                fd = get_suitable_downloader(info, self.params, to_stdout=(name == '-'))
                if fd not in native_downloaders:
                    raise DownloadError(
                        f"refusing {info.get('protocol')!r} download: it needs "
                        f"{getattr(fd, '__name__', fd)}, which bypasses graphify's network guard")
            return super().dl(name, info, subtitle=subtitle, test=test)

    return GuardedYoutubeDL


def is_url(path: str) -> bool:
    """Return True if the string looks like a URL rather than a file path."""
    return any(path.startswith(p) for p in URL_PREFIXES)


def download_audio(url: str, output_dir: Path) -> Path:
    """Download audio-only stream from a URL using yt-dlp.

    Returns the path to the downloaded audio file (.m4a or .opus).
    Uses cached file if already downloaded.
    """
    import tempfile

    from graphify.security import validate_url
    validate_url(url)  # blocks private IPs, bad schemes before yt-dlp runs
    yt_dlp = _get_yt_dlp()
    GuardedYoutubeDL = _guarded_youtube_dl_class(yt_dlp)
    output_dir.mkdir(parents=True, exist_ok=True)

    # yt-dlp uses %(title)s which can be long/weird — use a stable name based on URL hash
    import hashlib
    url_hash = hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()[:12]
    out_template = str(output_dir / f"yt_{url_hash}.%(ext)s")

    # Check for already-downloaded file
    for ext in ('.m4a', '.opus', '.mp3', '.ogg', '.wav', '.webm'):
        candidate = output_dir / f"yt_{url_hash}{ext}"
        if candidate.exists():
            print(f"  cached audio: {candidate.name}")
            return candidate

    print(f"  downloading audio: {url[:80]} ...", flush=True)
    # An empty ffmpeg_location makes ffmpeg unavailable to yt-dlp, so the native
    # HLS downloader cannot fall back to an ffmpeg process that fetches URLs
    # outside the guarded connection.
    with tempfile.TemporaryDirectory(prefix="graphify-no-ffmpeg-") as no_ffmpeg_dir:
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'outtmpl': out_template,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'postprocessors': [],  # no ffmpeg needed — use native audio
            'proxy': '',
            'ffmpeg_location': no_ffmpeg_dir,
        }
        with GuardedYoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        ext = info.get('ext', 'm4a')
        downloaded = output_dir / f"yt_{url_hash}.{ext}"
        if not downloaded.exists():
            # yt-dlp may have picked a different extension
            for p in output_dir.glob(f"yt_{url_hash}.*"):
                downloaded = p
                break
        return downloaded


def build_whisper_prompt(god_nodes: list[dict]) -> str:
    """Build a domain hint for Whisper from god nodes extracted from the corpus.

    Formats the top god node labels into a topic string for Whisper.
    The coding agent (Claude Code, Codex, etc.) generates the actual one-sentence
    domain hint from these labels and passes it via GRAPHIFY_WHISPER_PROMPT or
    as initial_prompt — no separate API call needed here.
    """
    if not god_nodes:
        return _FALLBACK_PROMPT

    override = os.environ.get("GRAPHIFY_WHISPER_PROMPT")
    if override:
        return override

    labels = [n.get("label", "") for n in god_nodes[:10] if n.get("label")]
    if not labels:
        return _FALLBACK_PROMPT

    topics = ", ".join(labels[:5])
    return f"Technical discussion about {topics}. Use proper punctuation and paragraph breaks."


def transcribe(
    video_path: Path | str,
    output_dir: Path | None = None,
    initial_prompt: str | None = None,
    force: bool = False,
) -> Path:
    """Transcribe a video/audio file or URL to a .txt transcript.

    If video_path is a URL, audio is downloaded first via yt-dlp.
    Returns the path to the saved transcript file.
    Uses cached transcript if it exists unless force=True.

    initial_prompt: domain hint for Whisper (built from corpus god nodes).
    force: re-transcribe even if transcript already exists.
    """
    out_dir = Path(output_dir) if output_dir else Path(_TRANSCRIPTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    if is_url(str(video_path)):
        audio_path = download_audio(str(video_path), out_dir / "downloads")
    else:
        audio_path = Path(video_path)

    transcript_path = out_dir / (audio_path.stem + ".txt")
    if transcript_path.exists() and not force:
        return transcript_path

    WhisperModel = _get_whisper()
    model_name = _model_name()
    prompt = initial_prompt or _FALLBACK_PROMPT

    print(f"  transcribing {audio_path.name} (model={model_name}) ...", flush=True)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=5,
        initial_prompt=prompt,
    )

    lines = [segment.text.strip() for segment in segments if segment.text.strip()]
    transcript = "\n".join(lines)

    transcript_path.write_text(transcript, encoding="utf-8")
    lang = info.language if hasattr(info, "language") else "unknown"
    print(f"  transcript saved -> {transcript_path} (lang={lang}, {len(lines)} segments)")
    return transcript_path


def transcribe_all(
    video_files: list[str],
    output_dir: Path | None = None,
    initial_prompt: str | None = None,
) -> list[str]:
    """Transcribe a list of video/audio files or URLs, return paths to transcript .txt files.

    Already-transcribed files are returned from cache instantly.
    initial_prompt is shared across all files — built once from corpus god nodes.
    """
    if not video_files:
        return []

    transcript_paths = []
    for vf in video_files:
        try:
            t = transcribe(vf, output_dir, initial_prompt=initial_prompt)
            transcript_paths.append(str(t))
        except Exception as exc:
            print(f"  warning: could not transcribe {vf}: {exc}")
    return transcript_paths
