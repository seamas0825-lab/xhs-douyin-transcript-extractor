#!/usr/bin/env python3
"""
Cross-Platform Video Subtitle OCR Processor
- macOS: Apple Vision Framework (Hardware-accelerated via ANE)
- Windows: WinRT Windows.Media.Ocr / PowerShell OCR / RapidOCR (DirectML/ONNX)
- Linux: RapidOCR / Tesseract fallback
"""

import sys
import os
import re
import json
import platform
import tempfile
import subprocess

def clean_text(s: str) -> str:
    return re.sub(r'[\s，。！？、：；“”‘’]', '', s)

def text_similarity(a: str, b: str) -> float:
    ca, cb = clean_text(a), clean_text(b)
    if ca == cb:
        return 1.0
    if not ca or not cb:
        return 0.0
    if ca in cb or cb in ca:
        return 0.85
    set_a, set_b = set(ca), set(cb)
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0

def format_time_seconds(sec: float) -> str:
    hrs = int(sec // 3600)
    mins = int((sec % 3600) // 60)
    secs = int(sec % 60)
    millis = int((sec - int(sec)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d}.{millis:03d}"

def cluster_raw_readings(readings):
    """
    readings: list of (timestamp_seconds, text_string)
    Returns list of dicts: {"start": "00:00:00.000", "end": "00:00:00.000", "text": "..."}
    """
    if not readings:
        return [], ""
    
    cues = []
    current_cue = None

    for t, text in readings:
        if not text.strip():
            continue
        if current_cue is None:
            current_cue = {"start": t, "end": t + 0.25, "text": text}
        else:
            sim = text_similarity(current_cue["text"], text)
            time_diff = t - current_cue["end"]
            if sim >= 0.65 and time_diff <= 1.2:
                # Same subtitle continuing
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
        data = json.loads(res.stdout)
        return {
            "has_subtitles": data.get("has_subtitles", False),
            "cues": data.get("cues", []),
            "transcript": data.get("transcript", ""),
            "engine": "apple_vision_framework"
        }
    return None

def run_windows_winrt_ocr(video_path: str):
    """
    Executes Windows 10/11 Native WinRT OCR (Windows.Media.Ocr).
    Extracts frames via ffmpeg at 5fps and passes to Windows OCR API.
    """
    # 1. Try python winsdk
    try:
        import winsdk.windows.media.ocr as win_ocr
        import winsdk.windows.globalization as glob
        import winsdk.windows.graphics.imaging as imaging
        import winsdk.windows.storage.streams as streams
        import cv2

        lang = glob.Language("zh-Hans-CN")
        if not win_ocr.OcrEngine.is_language_supported(lang):
            lang = win_ocr.OcrEngine.available_recognizer_languages[0]
        engine = win_ocr.OcrEngine.try_create_from_language(lang)

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
            # Crop lower 50% for subtitle detection
            h, w, _ = frame.shape
            crop_frame = frame[int(h * 0.5):, :]
            _, buf = cv2.imencode('.png', crop_frame)
            
            # Create Windows SoftwareBitmap from memory
            stream = streams.InMemoryRandomAccessStream()
            writer = streams.DataWriter(stream)
            writer.write_bytes(buf.tobytes())
            writer.store_async()
            stream.seek(0)
            decoder = imaging.BitmapDecoder.create_async(stream)
            bitmap = decoder.get_software_bitmap_async()
            ocr_result = engine.recognize_async(bitmap)
            
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
    except Exception:
        pass

    # 2. Try RapidOCR (Best cross-platform Windows ML engine)
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
    except Exception:
        pass

    return None

def process_video_ocr(video_path: str):
    """Cross-platform entrypoint for local subtitle extraction."""
    system = platform.system()
    if system == "Darwin":
        res = run_macos_ocr(video_path)
        if res:
            return res
    elif system == "Windows":
        res = run_windows_winrt_ocr(video_path)
        if res:
            return res
    else:
        # Linux or generic fallback
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
