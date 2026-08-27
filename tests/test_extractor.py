import unittest
import sys
import os
import re
import json
import tempfile
import urllib.parse
import urllib.request

# Add scripts directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))

from extract import (
    parse_srt,
    parse_vtt,
    is_domain_allowed,
    validate_and_extract_url,
    register_temp_file,
    cleanup_all_temp_files,
    temp_files_to_clean,
    SafeRedirectHandler,
    extract_xhs_from_state,
    extract_douyin_from_detail,
    MAX_DOWNLOAD_BYTES
)
from ocr_processor import (
    text_similarity,
    cluster_raw_readings,
    is_valid_subtitle,
    clean_text_for_comparison
)

class TestTranscriptExtractor(unittest.TestCase):

    # 1. PARSER TESTS
    def test_parse_srt_normal(self):
        sample_srt = """1
00:00:01,000 --> 00:00:03,500
美食特种兵之长沙！

2
00:00:04,000 --> 00:00:06,200
不吃辣星人怎么吃！
"""
        cues, transcript = parse_srt(sample_srt)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "美食特种兵之长沙！")
        self.assertEqual(cues[0]["start"], "00:00:01,000")
        self.assertEqual(cues[1]["text"], "不吃辣星人怎么吃！")
        self.assertIn("美食特种兵之长沙！ 不吃辣星人怎么吃！", transcript)

    def test_parse_srt_empty_and_malformed(self):
        cues, transcript = parse_srt("")
        self.assertEqual(len(cues), 0)
        self.assertEqual(transcript, "")

        malformed_srt = "Just some random text\nwithout timestamps"
        cues, transcript = parse_srt(malformed_srt)
        self.assertEqual(len(cues), 0)

    def test_parse_vtt_normal(self):
        sample_vtt = """WEBVTT

00:00:01.200 --> 00:00:03.800
青蟹一生蜕壳十三次左右

00:00:04.100 --> 00:00:07.500
黄油蟹是其中的极品
"""
        cues, transcript = parse_vtt(sample_vtt)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "青蟹一生蜕壳十三次左右")
        self.assertEqual(cues[1]["text"], "黄油蟹是其中的极品")

    # 2. SSRF & DOMAIN WHITELIST SECURITY TESTS (INCLUDING CDNs)
    def test_domain_whitelist_allowed(self):
        valid_urls = [
            "https://www.douyin.com/video/7645079463847284020",
            "https://v.douyin.com/q0mwVdBpp6I/",
            "https://www.xiaohongshu.com/explore/64f8a123000000000100",
            "http://xhslink.com/a/bCdEfG",
            "https://v11-weba.douyinvod.com/video/tos/cn/abc.mp4",
            "https://sns-video-bd.xhscdn.com/stream/abc.mp4",
            "https://fe-video-qc.xhscdn.com/stream/video.mp4",
            "https://p3.pstatp.com/aweme/video.mp4"
        ]
        for url in valid_urls:
            self.assertTrue(is_domain_allowed(url), f"Should allow valid URL: {url}")

    def test_domain_whitelist_blocked_ssrf(self):
        malicious_urls = [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8080/admin",
            "http://localhost:3000",
            "https://evil-attacker.com/payload.mp4",
            "https://douyin.com.attacker.com/phish"
        ]
        for url in malicious_urls:
            self.assertFalse(is_domain_allowed(url), f"Should block: {url}")
            with self.assertRaises(ValueError):
                validate_and_extract_url(f"Check this link {url}")

    def test_safe_redirect_handler_blocks_malicious_hop(self):
        handler = SafeRedirectHandler()
        req = urllib.request.Request("https://www.douyin.com/video/123")
        with self.assertRaises(ValueError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/secret")

    # 3. RECORDED FIXTURES TESTS (XHS & DOUYIN REAL PAYLOADS)
    def test_extract_xhs_from_fixture(self):
        sample_state = {
            "note": {
                "noteDetailMap": {
                    "note_12345": {
                        "note": {
                            "title": "香港必吃大排档探店",
                            "desc": "今天带大家吃隐藏在大坑的经典美食 #美食探店",
                            "user": {"nickname": "食神阿杰"},
                            "interactInfo": {"likedCount": "12800", "collectedCount": "3400"},
                            "video": {
                                "capa": {"duration": 120},
                                "mediaV2": {
                                    "video": {
                                        "stream": {
                                            "h264": [
                                                {"masterUrl": "https://sns-video-bd.xhscdn.com/stream_high.mp4", "size": 15000000},
                                                {"masterUrl": "https://sns-video-bd.xhscdn.com/stream_low.mp4", "size": 4500000}
                                            ]
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        res = extract_xhs_from_state(sample_state)
        self.assertEqual(res["platform"], "xiaohongshu")
        self.assertEqual(res["author"], "食神阿杰")
        self.assertEqual(res["title"], "香港必吃大排档探店")
        self.assertEqual(res["smallest_stream_url"], "https://sns-video-bd.xhscdn.com/stream_low.mp4")
        self.assertFalse(res["has_subtitles"]) # No soft subtitles in fixture -> eligible for Track 2 OCR

    def test_extract_douyin_from_fixture(self):
        sample_detail = {
            "aweme_detail": {
                "desc": "三天两夜美食特种兵 #旅行",
                "author": {"nickname": "神奇海挪"},
                "statistics": {"digg_count": 50845, "collect_count": 5720},
                "video": {
                    "duration": 489000,
                    "play_addr": {"url_list": ["https://v11-weba.douyinvod.com/raw.mp4"]},
                    "bit_rate": [
                        {"play_addr": {"url_list": ["https://v11-weba.douyinvod.com/360p.mp4"], "data_size": 34000000}},
                        {"play_addr": {"url_list": ["https://v11-weba.douyinvod.com/1080p.mp4"], "data_size": 120000000}}
                    ]
                }
            }
        }
        res = extract_douyin_from_detail(sample_detail, "7645079463847284020")
        self.assertEqual(res["platform"], "douyin")
        self.assertEqual(res["author"], "神奇海挪")
        self.assertEqual(res["duration_seconds"], 489)
        self.assertEqual(res["smallest_stream_url"], "https://v11-weba.douyinvod.com/360p.mp4")
        self.assertFalse(res["has_subtitles"])

    # 4. TEXT SIMILARITY & LEVENSHTEIN CLUSTERING TESTS
    def test_similarity_ratio(self):
        self.assertGreaterEqual(text_similarity("青蟹一生蜕壳十三次", "青蟹一生蜕壳十三次左右"), 0.80)
        self.assertLess(text_similarity("完全不相干的内容", "青蟹一生蜕壳十三次"), 0.30)
        self.assertEqual(text_similarity("完全一致的内容", "完全一致的内容"), 1.0)
        self.assertEqual(text_similarity("你好，世界！", "你好 世界"), 1.0)

    def test_is_valid_subtitle(self):
        self.assertTrue(is_valid_subtitle("美食特种兵"))
        self.assertFalse(is_valid_subtitle("a"))
        self.assertFalse(is_valid_subtitle("12345"))
        self.assertFalse(is_valid_subtitle("？"))
        self.assertFalse(is_valid_subtitle("   "))

    def test_cluster_raw_readings(self):
        readings = [
            (1.0, "美食特种兵之"),
            (1.2, "美食特种兵之长沙"),
            (1.4, "美食特种兵之长沙"),
            (3.0, "不吃辣星人"),
            (3.2, "不吃辣星人怎么吃")
        ]
        cues, transcript = cluster_raw_readings(readings)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "美食特种兵之长沙")
        self.assertEqual(cues[1]["text"], "不吃辣星人怎么吃")

    # 5. RESOURCE LIFECYCLE & CLEANUP TESTS
    def test_temp_file_lifecycle(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.write(b"dummy video data")
        tmp.close()
        
        self.assertTrue(os.path.exists(tmp.name))
        register_temp_file(tmp.name)
        self.assertIn(tmp.name, temp_files_to_clean)

        cleanup_all_temp_files()
        self.assertFalse(os.path.exists(tmp.name))
        self.assertEqual(len(temp_files_to_clean), 0)

if __name__ == "__main__":
    unittest.main()
