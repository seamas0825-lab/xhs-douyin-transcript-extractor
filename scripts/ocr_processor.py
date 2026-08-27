#!/usr/bin/env python3
"""
Cross-Platform Video Subtitle OCR Processor (v0.2.0)
- macOS: Apple Vision Framework (Hardware-accelerated via ANE)
- Windows: WinRT Windows.Media.Ocr (Proper Async handling) / RapidOCR (DirectML/ONNX)
- Linux: RapidOCR / Tesseract fallback
- Subtitle clustering with Levenshtein/SequenceMatcher ratio and noise filtering
"""

import sys
import os
import re
import json
import difflib
import platform
import asyncio
import tempfile
import subprocess

def clean_text_for_comparison(s: str) -> str:
    """Normalizes text by stripping whitespace and common punctuation."""
    return re.sub(r'[\s，。！？、：；“”‘’\"\'\(\)\[\]\-~]', '', s)

def text_similarity(a: str, b: str) -> float:
    """Calculates sequence similarity ratio between two subtitle candidate strings."""
    ca, cb = clean_text_for_comparison(a), clean_text_for_comparison(b)
    if ca == cb:
        return 1.0
    if not ca or not cb:
        return 0.0
    # Direct substring inclusion (e.g. progressive subtitle display)
    if ca in cb or cb in ca:
        return 0.85
    # Sequence matcher similarity ratio
    return difflib.SequenceMatcher(None, ca, cb).ratio()

def format_time_seconds(sec: float) -> str:
    hrs = int(sec // 3600)
    mins = int((sec % 3600) // 60)
    secs = int(sec % 60)
    millis = int((sec - int(sec)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"

def is_valid_subtitle(text: str) -> bool:
    """Filters out common noise, single characters, and pure punctuation."""
    cleaned = clean_text_for_comparison(text)
    if len(cleaned) < 2:
        return False
    # Reject pure numbers or timestamp noise
    if re.match(r'^\d+$', cleaned):
        return False
    return True

def cluster_raw_readings(readings):
    """
    Clusters raw frame OCR readings into coherent subtitle cues.
    readings: list of (timestamp_seconds, text_string)
    """
    if not readings:
        return [], ""
    
    cues = []
    current_cue = None

    for t, text in readings:
        if not is_valid_subtitle(text):
            continue
        
        if current_cue is None:
            current_cue = {"start": t, "end": t + 0.25, "text": text}
        else:
            sim = text_similarity(current_cue["text"], text)
            time_diff = t - current_cue["end"]
            
            if sim >= 0.60 and time_diff <= 1.2:
                # Merge into existing cue, keeping the richer text
                if len(text) > len(current_cue["text"]):
                    current_cue["text"] = text
                current_cue["end"] = t + 0.25
            else:
                if current_cue["end"] - current_cue["start"] >= 0.25:
                    cues.append(current_cue)
                current_cue = {"start": t, "end": t + 0.25, "text": text}

    if current_cue and (current_cue["end"] - current_cue["start"] >= 0.25):
        cues.append(current_cue)

    formatted_cues = []
    for c in cues:
        formatted_cues.append({
            "start": format_time_seconds(c["start"]),
            "end": format_time_seconds(c["end"]),
            "text": c["text"]
        })

    full_transcript = " ".join(c["text"] for c in formatted_cues)
    return formatted_cues, full_transcript

# ==================== PLATFORM SPECIFIC OCR ENGINES ====================

def run_macos_ocr(video_path: str):
    """Executes macOS native Apple Vision OCR engine."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ocr_bin = os.path.join(script_dir, "ocr_engine")
    swift_src = os.path.join(script_dir, "ocr_engine.swift")

    if os.path.exists(ocr_bin) and os.access(ocr_bin, os.X_OK):
        cmd = [ocr_bin, video_path]
    elif os.path.exists(swift_src):
        cmd = ["swift", swift_src, video_path]
    else:
        return None

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if res.returncode == 0 and res.stdout.strip():
        try:
            data = json.loads(res.stdout)
            return {
                "has_subtitles": data.get("has_subtitles", False),
                "cues": data.get("cues", []),
                "transcript": data.get("transcript", ""),
                "engine": "apple_vision_framework"
            }
        except json.JSONDecodeError:
            pass
    return None

async def _run_winrt_ocr_async(video_path: str):
    """Async worker for Windows WinRT OCR with proper await handling."""
    import winsdk.windows.media.ocr as win_ocr
    import winsdk.windows.globalization as glob
    import winsdk.windows.graphics.imaging as imaging
    import winsdk.windows.storage.streams as streams
    import cv2

    lang = glob.Language("zh-Hans-CN")
    if not win_ocr.OcrEngine.is_language_supported(lang):
        supported_langs = win_ocr.OcrEngine.available_recognizer_languages
        if supported_langs:
            lang = supported_langs[0]
        else:
            return None

    engine = win_ocr.OcrEngine.try_create_from_language(lang)
    if not engine:
        return None

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    interval = max(1, int(fps / 5))
    frame_idx = 0
    raw_readings = []

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        if frame_idx % interval != 0:
            continue

        t_sec = frame_idx / fps
        h, w, _ = frame.shape
        # Crop lower 50% for subtitle band
        crop_frame = frame[int(h * 0.5):, :]
        _, buf = cv2.imencode('.png', crop_frame)
        
        # Async stream and bitmap creation
        stream = streams.InMemoryRandomAccessStream()
        writer = streams.DataWriter(stream)
        writer.write_bytes(buf.tobytes())
        await writer.store_async()
        stream.seek(0)

        decoder = await imaging.BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        ocr_result = await engine.recognize_async(bitmap)
        
        lines = [line.text for line in ocr_result.lines if line.text.strip()]
        if lines:
            raw_readings.append((t_sec, " ".join(lines)))

    cap.release()
    cues, transcript = cluster_raw_readings(raw_readings)
    return {
        "has_subtitles": bool(cues),
        "cues": cues,
        "transcript": transcript,
        "engine": "windows_winrt_ocr"
    }

def run_windows_ocr(video_path: str):
    """Executes Windows OCR via WinRT async or RapidOCR fallback."""
    # 1. Try Windows Native WinRT OCR
    try:
        res = asyncio.run(_run_winrt_ocr_async(video_path))
        if res and res.get("has_subtitles"):
            return res
    except Exception as e:
        sys.stderr.write(f"[WinRT OCR fallback] {e}\n")

    # 2. Try RapidOCR (ONNX Runtime + DirectML/CUDA/CPU)
    try:
        from rapidocr_onnxruntime import RapidOCR
        import cv2

        engine = RapidOCR()
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        interval = max(1, int(fps / 5))
        frame_idx = 0
        raw_readings = []

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frame_idx += 1
            if frame_idx % interval != 0:
                continue

            t_sec = frame_idx / fps
            h, w, _ = frame.shape
            crop = frame[int(h * 0.45):, :]
            result, _ = engine(crop)
            if result:
                texts = [item[1] for item in result if item[1].strip()]
                if texts:
                    raw_readings.append((t_sec, " ".join(texts)))

        cap.release()
        cues, transcript = cluster_raw_readings(raw_readings)
        return {
            "has_subtitles": bool(cues),
            "cues": cues,
            "transcript": transcript,
            "engine": "windows_rapidocr"
        }
    except Exception as e:
        sys.stderr.write(f"[RapidOCR fallback] {e}\n")

    return None

def process_video_ocr(video_path: str):
    """Cross-platform entrypoint for local subtitle extraction."""
    system = platform.system()
    if system == "Darwin":
        res = run_macos_ocr(video_path)
        if res:
            return res
    elif system == "Windows":
        res = run_windows_ocr(video_path)
        if res:
            return res
    else:
        # Linux / container fallback
        try:
            from rapidocr_onnxruntime import RapidOCR
            import cv2
            engine = RapidOCR()
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            interval = max(1, int(fps / 5))
            frame_idx = 0
            raw_readings = []
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                frame_idx += 1
                if frame_idx % interval != 0:
                    continue
                t_sec = frame_idx / fps
                h, w, _ = frame.shape
                crop = frame[int(h * 0.45):, :]
                result, _ = engine(crop)
                if result:
                    texts = [item[1] for item in result if item[1].strip()]
                    if texts:
                        raw_readings.append((t_sec, " ".join(texts)))
            cap.release()
            cues, transcript = cluster_raw_readings(raw_readings)
            return {
                "has_subtitles": bool(cues),
                "cues": cues,
                "transcript": transcript,
                "engine": "rapidocr_linux"
            }
        except Exception:
            pass

    return {
        "has_subtitles": False,
        "cues": [],
        "transcript": "",
        "engine": "none"
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: ocr_processor.py <video_path>")
        sys.exit(1)
    
    result = process_video_ocr(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
