#!/usr/bin/env python3
"""
XHS & Douyin Transcript & Subtitle Extractor (v0.4.0)
- Xiaohongshu: Direct SSR / __INITIAL_STATE__ / mediaV2 JSON decoding -> official .srt download.
- Douyin: Short URL resolving -> TTWID gateway registration -> aweme_detail API -> cla_info (.vtt) or local stream extraction.
- Architecture:
    - CLI Automated Pipeline: 
        Track 1: Official soft subtitles (0.05s instant fast-path)
        Track 2: Local platform OCR (macOS Apple Vision / Windows WinRT & RapidOCR)
    - Agent Interaction Layer:
        Track 3: Multimodal clip payload preparation (view_file fallback)
- Security:
    - Strict verified TLS with certifi
    - SSRF protection via custom SafeRedirectHandler validating every redirect hop against domain whitelist
    - Stream download byte capping & dynamic duration-aware timeouts
- Resource Lifecycle: atexit + signal-trap automatic temporary media cleanup.
"""

import sys
import os
import re
import json
import ssl
import html
import signal
import socket
import atexit
import tempfile
import urllib.parse
import urllib.request
import subprocess
import ipaddress

# ----------------- CONSTANTS & SECURITY CONFIG -----------------

ALLOWED_DOMAINS = {
    # Xiaohongshu core & CDNs
    "xiaohongshu.com",
    "xhslink.com",
    "xhscdn.com",
    "xhscdn.net",
    "fe-video-qc.xhscdn.com",
    "sns-video-bd.xhscdn.com",
    "sns-video-qc.xhscdn.com",
    "sns-video-hw.xhscdn.com",
    # Douyin / ByteDance core & CDNs
    "douyin.com",
    "iesdouyin.com",
    "douyinvod.com",
    "byteoversea.com",
    "ibytedtos.com",
    "pstatp.com",
    "bytedance.com",
    "bytegoofy.com",
    "ixigua.com"
}

MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024  # 250 MB max limit for stream safety
DOWNLOAD_CHUNK_SIZE = 1024 * 1024        # 1 MB chunk

DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"

# ----------------- STRICT TLS & SECURE OPENER -----------------

def get_strict_ssl_context():
    """Creates a strict, verified TLS context using CA certificates."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

ctx = get_strict_ssl_context()

def is_domain_allowed(url: str) -> bool:
    """Verifies that the target hostname belongs to authorized platforms."""
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        return any(hostname == allowed or hostname.endswith("." + allowed) for allowed in ALLOWED_DOMAINS)
    except Exception:
        return False

def is_safe_ip(ip_str: str) -> bool:
    """Blocks loopback, private, link-local, and multicast IP ranges."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved)
    except Exception:
        return False

class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Intercepts all redirect hops (301/302/307) and validates each destination against SSRF rules."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_domain_allowed(newurl):
            parsed = urllib.parse.urlparse(newurl)
            raise ValueError(f"SSRF Security Violation: Redirect to unauthorized domain '{parsed.hostname}' blocked.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def build_secure_opener():
    """Builds a urllib opener equipped with strict TLS and SafeRedirectHandler."""
    https_handler = urllib.request.HTTPSHandler(context=ctx)
    return urllib.request.build_opener(SafeRedirectHandler, https_handler)

secure_opener = build_secure_opener()

# ----------------- CLEANUP & SIGNAL MANAGEMENT -----------------

temp_files_to_clean = set()

def register_temp_file(filepath: str):
    if filepath:
        temp_files_to_clean.add(filepath)

def cleanup_all_temp_files():
    for tmp_path in list(temp_files_to_clean):
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        temp_files_to_clean.discard(tmp_path)

atexit.register(cleanup_all_temp_files)

def _signal_handler(signum, frame):
    cleanup_all_temp_files()
    sys.exit(128 + signum)

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# ----------------- URL VALIDATION -----------------

def validate_and_extract_url(text: str) -> str:
    """Extracts first HTTP(S) URL and asserts domain legitimacy against SSRF attacks."""
    url_match = re.search(r'https?://[^\s<>"]+', text)
    if not url_match:
        raise ValueError("No valid URL found in input string.")
    
    url = url_match.group(0)
    if not is_domain_allowed(url):
        parsed = urllib.parse.urlparse(url)
        raise ValueError(f"Security Error: Domain '{parsed.hostname}' is not in the authorized platform whitelist.")
    return url

# ----------------- PARSERS -----------------

def parse_srt(srt_text: str):
    """Parses SRT format into timestamped cues and clean transcript."""
    cues = []
    blocks = re.split(r'\r?\n\s*\r?\n', srt_text.strip())
    clean_lines = []
    time_pattern = re.compile(r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})')

    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        ts_idx = -1
        start_t, end_t = "", ""
        for i, l in enumerate(lines):
            m = time_pattern.search(l)
            if m:
                ts_idx = i
                start_t, end_t = m.group(1), m.group(2)
                break
        
        if ts_idx != -1 and ts_idx + 1 < len(lines):
            text = " ".join(lines[ts_idx + 1:])
            cues.append({
                "start": start_t,
                "end": end_t,
                "text": text
            })
            clean_lines.append(text)

    return cues, " ".join(clean_lines)

def parse_vtt(vtt_text: str):
    """Parses WebVTT format into timestamped cues and clean transcript."""
    cues = []
    clean_lines = []
    time_pattern = re.compile(r'(\d{2}:\d{2}:\d{2}[,.]\d{3}|\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3}|\d{2}:\d{2}[,.]\d{3})')
    
    blocks = re.split(r'\r?\n\s*\r?\n', vtt_text.strip())
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip() and not l.startswith('WEBVTT') and not l.startswith('NOTE')]
        if not lines:
            continue
        ts_idx = -1
        start_t, end_t = "", ""
        for i, l in enumerate(lines):
            m = time_pattern.search(l)
            if m:
                ts_idx = i
                start_t, end_t = m.group(1), m.group(2)
                break
        if ts_idx != -1 and ts_idx + 1 < len(lines):
            text = " ".join(lines[ts_idx + 1:])
            cues.append({
                "start": start_t,
                "end": end_t,
                "text": text
            })
            clean_lines.append(text)

    return cues, " ".join(clean_lines)

# ----------------- XIAOHONGSHU EXTRACTION -----------------

def extract_xhs_from_state(state: dict) -> dict:
    """Parses XHS hydrated state dictionary."""
    note_map = state.get("note", {}).get("noteDetailMap", {})
    if not note_map:
        raise ValueError("noteDetailMap is empty in XHS state")

    note_id = list(note_map.keys())[0]
    note = note_map[note_id].get("note", {})

    title = note.get("title", "")
    desc = note.get("desc", "")
    user = note.get("user", {})
    author = user.get("nickname") or user.get("nickName") or "未知作者"
    interact = note.get("interactInfo", {})
    likes = interact.get("likedCount", "0")
    collects = interact.get("collectedCount", "0")

    video_obj = note.get("video", {})
    duration = video_obj.get("capa", {}).get("duration", 0)

    media_v2 = video_obj.get("mediaV2")
    if isinstance(media_v2, str):
        try:
            media_v2 = json.loads(media_v2)
        except Exception:
            media_v2 = {}
    elif not media_v2:
        media_v2 = video_obj.get("media", {})

    subtitles = media_v2.get("video", {}).get("subtitles", {}) or video_obj.get("subtitles", {})
    
    srt_url = None
    selected_lang = None
    if isinstance(subtitles, dict):
        for lang in ["zh-CN", "source", "en-US"]:
            if lang in subtitles and subtitles[lang]:
                srt_url = subtitles[lang][0].get("url")
                selected_lang = lang
                break
        if not srt_url and subtitles:
            first_key = list(subtitles.keys())[0]
            if subtitles[first_key]:
                srt_url = subtitles[first_key][0].get("url")
                selected_lang = first_key

    cues = []
    transcript = ""

    if srt_url and is_domain_allowed(srt_url):
        try:
            srt_req = urllib.request.Request(srt_url, headers={"User-Agent": DESKTOP_UA})
            with secure_opener.open(srt_req, timeout=10) as s_resp:
                srt_raw = s_resp.read().decode('utf-8', errors='ignore')
                cues, transcript = parse_srt(srt_raw)
        except Exception:
            cues, transcript = [], ""

    has_subtitles = bool(cues)

    # Get stream URLs
    stream_h264 = media_v2.get("video", {}).get("stream", {}).get("h264", [])
    video_stream_url = None
    smallest_stream_url = None
    smallest_size = float('inf')

    if stream_h264 and isinstance(stream_h264, list):
        video_stream_url = stream_h264[0].get("masterUrl")
        for stream_item in stream_h264:
            s_url = stream_item.get("masterUrl")
            size = stream_item.get("size", 0)
            if s_url and (0 < size < smallest_size or smallest_stream_url is None):
                smallest_size = size
                smallest_stream_url = s_url

    if not smallest_stream_url:
        smallest_stream_url = video_stream_url

    return {
        "platform": "xiaohongshu",
        "note_id": note_id,
        "title": title,
        "desc": desc,
        "author": author,
        "likes": likes,
        "collects": collects,
        "duration_seconds": duration,
        "has_subtitles": has_subtitles,
        "subtitle_lang": selected_lang,
        "subtitle_url": srt_url,
        "video_stream_url": video_stream_url,
        "smallest_stream_url": smallest_stream_url,
        "smallest_size_bytes": smallest_size if smallest_size != float('inf') else 0,
        "cues": cues,
        "transcript": transcript
    }

def extract_xhs(url_or_text: str) -> dict:
    """Extracts XHS video subtitles and metadata from URL."""
    url = validate_and_extract_url(url_or_text)

    req = urllib.request.Request(url, headers={
        "User-Agent": DESKTOP_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    })
    
    with secure_opener.open(req, timeout=15) as resp:
        page_html = resp.read().decode('utf-8', errors='ignore')

    match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?</script>', page_html, re.DOTALL)
    if not match:
        raise ValueError("Could not find window.__INITIAL_STATE__ in XHS page HTML")

    raw_state_str = match.group(1)
    clean_state_str = re.sub(r':\s*undefined\b', ': null', raw_state_str)
    state = json.loads(clean_state_str)
    return extract_xhs_from_state(state)

# ----------------- DOUYIN EXTRACTION -----------------

def get_douyin_ttwid():
    """Acquires a valid TTWID cookie from ByteDance union gateway."""
    post_data = json.dumps({
        'region': 'cn',
        'aid': 1768,
        'needFid': 'false',
        'service': 'www.ixigua.com',
        'migrate_info': {'ticket': '', 'source': 'node'},
        'cbUrlProtocol': 'https',
        'union': 'true'
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://ttwid.bytedance.com/ttwid/union/register/',
        data=post_data,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': DESKTOP_UA
        }
    )
    with secure_opener.open(req, timeout=10) as resp:
        cookies = resp.headers.get_all('Set-Cookie') or []
        for c in cookies:
            if 'ttwid=' in c:
                return c.split(';')[0]
    return ""

def extract_douyin_from_detail(d_json: dict, video_id: str) -> dict:
    """Parses Douyin aweme_detail JSON response."""
    aweme = d_json.get("aweme_detail", {})
    if not aweme:
        raise ValueError(f"Failed to retrieve aweme_detail for Douyin ID {video_id}")

    desc = aweme.get("desc", "")
    author_info = aweme.get("author", {})
    author = author_info.get("nickname", "未知作者")
    stats = aweme.get("statistics", {})
    likes = stats.get("digg_count", 0)
    collects = stats.get("collect_count", 0)

    video = aweme.get("video", {})
    duration = video.get("duration", 0) // 1000

    # Check soft subtitles (cla_info)
    cla_info = video.get("cla_info") or {}
    caption_infos = cla_info.get("caption_infos", []) or video.get("caption_infos", [])
    
    sub_url = None
    cues = []
    transcript = ""

    if caption_infos:
        sub_url = caption_infos[0].get("url")
        if sub_url and is_domain_allowed(sub_url):
            try:
                sub_req = urllib.request.Request(sub_url, headers={"User-Agent": DESKTOP_UA})
                with secure_opener.open(sub_req, timeout=10) as s_resp:
                    raw_text = s_resp.read().decode('utf-8', errors='ignore')
                    if raw_text.strip().startswith("WEBVTT"):
                        cues, transcript = parse_vtt(raw_text)
                    else:
                        cues, transcript = parse_srt(raw_text)
            except Exception:
                cues, transcript = [], ""

    has_subtitles = bool(cues)

    # Find highest quality & smallest direct stream URLs
    play_addr = video.get("play_addr", {}).get("url_list", [])
    video_stream_url = play_addr[0] if play_addr else None

    smallest_stream_url = None
    smallest_size = float('inf')
    for br in video.get("bit_rate", []):
        size = br.get("play_addr", {}).get("data_size", 0)
        urls = br.get("play_addr", {}).get("url_list", [])
        if urls and 0 < size < smallest_size:
            smallest_size = size
            smallest_stream_url = urls[0]

    return {
        "platform": "douyin",
        "video_id": video_id,
        "title": desc,
        "desc": desc,
        "author": author,
        "likes": likes,
        "collects": collects,
        "duration_seconds": duration,
        "has_subtitles": has_subtitles,
        "subtitle_url": sub_url,
        "video_stream_url": video_stream_url,
        "smallest_stream_url": smallest_stream_url or video_stream_url,
        "smallest_size_bytes": smallest_size if smallest_size != float('inf') else 0,
        "cues": cues,
        "transcript": transcript
    }

def extract_douyin(url_or_text: str) -> dict:
    """Extracts Douyin video metadata and subtitles from URL."""
    url = validate_and_extract_url(url_or_text)

    # 1. Resolve redirect to get video ID
    req = urllib.request.Request(url, headers={"User-Agent": DESKTOP_UA})
    with secure_opener.open(req, timeout=12) as resp:
        final_url = resp.geturl()

    m = re.search(r'/video/(\d+)', final_url)
    if not m:
        raise ValueError(f"Could not extract video ID from resolved URL: {final_url}")
    video_id = m.group(1)

    # 2. Get TTWID
    ttwid = get_douyin_ttwid()

    # 3. Query Douyin aweme detail API
    detail_url = f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={video_id}&aid=6383&device_platform=webapp&channel=channel_pc_web"
    detail_req = urllib.request.Request(detail_url, headers={
        "User-Agent": DESKTOP_UA,
        "Referer": f"https://www.douyin.com/video/{video_id}",
        "Cookie": ttwid
    })

    with secure_opener.open(detail_req, timeout=15) as resp:
        d_json = json.loads(resp.read().decode('utf-8', errors='ignore'))

    return extract_douyin_from_detail(d_json, video_id)

# ----------------- STREAM DOWNLOAD WITH LIMITS -----------------

def download_video_stream(stream_url: str, referer: str, dest_path: str):
    """Downloads video stream with strict MAX_DOWNLOAD_BYTES limit and domain safety."""
    if not is_domain_allowed(stream_url):
        raise ValueError(f"Security Error: Stream URL '{stream_url}' not in authorized domain whitelist.")
    
    headers = {
        "User-Agent": DESKTOP_UA,
        "Referer": referer
    }
    req = urllib.request.Request(stream_url, headers=headers)
    total_bytes = 0

    with secure_opener.open(req, timeout=30) as resp, open(dest_path, "wb") as out_f:
        while True:
            chunk = resp.read(DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"Download exceeded maximum safe limit of {MAX_DOWNLOAD_BYTES // (1024*1024)}MB.")
            out_f.write(chunk)

# ----------------- MAIN DISPATCHER -----------------

def main():
    if len(sys.argv) < 2:
        print("Usage: extract.py '<XHS_OR_DOUYIN_URL_OR_SHARE_TEXT>'")
        sys.exit(1)

    input_text = " ".join(sys.argv[1:])

    try:
        if "xiaohongshu.com" in input_text or "xhslink.com" in input_text:
            result = extract_xhs(input_text)
        elif "douyin.com" in input_text:
            result = extract_douyin(input_text)
        else:
            try:
                result = extract_xhs(input_text)
            except Exception:
                result = extract_douyin(input_text)

        # Track 2 Fallback: If no valid subtitles found in Track 1, run Local Platform OCR
        stream_url = result.get("smallest_stream_url") or result.get("video_stream_url")
        duration = result.get("duration_seconds") or 60

        if not result.get("has_subtitles") and stream_url:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            tmp_video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
            register_temp_file(tmp_video)

            try:
                referer = "https://www.xiaohongshu.com/" if result.get("platform") == "xiaohongshu" else "https://www.douyin.com/"
                download_video_stream(stream_url, referer, tmp_video)
                
                # Dynamic timeout proportional to video length (minimum 120s, ~0.6s per video second)
                ocr_timeout = max(120, int(duration * 0.6) + 30)
                
                # Execute OCR Processor
                ocr_processor_path = os.path.join(script_dir, "ocr_processor.py")
                ocr_res = subprocess.run(
                    [sys.executable, ocr_processor_path, tmp_video],
                    capture_output=True,
                    text=True,
                    timeout=ocr_timeout
                )
                if ocr_res.returncode == 0 and ocr_res.stdout.strip():
                    ocr_data = json.loads(ocr_res.stdout)
                    if ocr_data.get("cues"):
                        result["has_subtitles"] = True
                        result["extraction_mode"] = ocr_data.get("engine", "local_ocr")
                        result["cues"] = ocr_data.get("cues", [])
                        result["transcript"] = ocr_data.get("transcript", "")
            except Exception as ocr_err:
                result["ocr_error"] = str(ocr_err)

        # If still no subtitles after Track 1 & Track 2, mark multimodal fallback readiness for Agent layer (Track 3)
        if not result.get("has_subtitles"):
            result["multimodal_fallback_ready"] = bool(stream_url)

        print(json.dumps(result, ensure_ascii=False, indent=2))

    except Exception as e:
        error_result = {
            "error": True,
            "message": str(e)
        }
        print(json.dumps(error_result, ensure_ascii=False, indent=2))
        sys.exit(1)
    finally:
        cleanup_all_temp_files()

if __name__ == "__main__":
    main()
