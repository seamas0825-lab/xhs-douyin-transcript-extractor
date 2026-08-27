import unittest
import sys
import os
import re
import json
import tempfile

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

    def test_parse_vtt_with_headers_and_notes(self):
        sample_vtt = """WEBVTT
NOTE This is a commentary note

00:01.000 --> 00:03.500
测试简短时间轴
"""
        cues, transcript = parse_vtt(sample_vtt)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["text"], "测试简短时间轴")

    # 2. SSRF & DOMAIN WHITELIST SECURITY TESTS
    def test_domain_whitelist_allowed(self):
        valid_urls = [
            "https://www.douyin.com/video/7645079463847284020",
            "https://v.douyin.com/q0mwVdBpp6I/",
            "https://www.xiaohongshu.com/explore/64f8a123000000000100",
            "http://xhslink.com/a/bCdEfG",
            "https://v11-weba.douyinvod.com/video/tos/cn/abc.mp4",
            "https://sns-video-bd.xhscdn.com/stream/abc.mp4" # xhscdn is cdn
        ]
        # Test directly with allowed list
        self.assertTrue(is_domain_allowed("https://www.douyin.com/video/123"))
        self.assertTrue(is_domain_allowed("https://v.douyin.com/abc/"))
        self.assertTrue(is_domain_allowed("https://www.xiaohongshu.com/explore/123"))
        self.assertTrue(is_domain_allowed("https://xhslink.com/abc"))
        self.assertTrue(is_domain_allowed("https://v11-weba.douyinvod.com/stream.mp4"))

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

    # 3. TEXT SIMILARITY & LEVENSHTEIN CLUSTERING TESTS
    def test_similarity_ratio(self):
        # High similarity for progressive display
        self.assertGreaterEqual(text_similarity("青蟹一生蜕壳十三次", "青蟹一生蜕壳十三次左右"), 0.80)
        # Low similarity for unrelated sentences
        self.assertLess(text_similarity("完全不相干的内容", "青蟹一生蜕壳十三次"), 0.30)
        # Identical strings
        self.assertEqual(text_similarity("完全一致的内容", "完全一致的内容"), 1.0)
        # Punctuation normalization
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

    # 4. RESOURCE LIFECYCLE & CLEANUP TESTS
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

    # 5. XHS STATE UNDEFINED REPLACEMENT FIXTURE TEST
    def test_xhs_state_cleaner(self):
        raw_state = '{"video": {"url": undefined, "title": "test", "active": undefined}}'
        cleaned = re.sub(r':\s*undefined\b', ': null', raw_state)
        data = json.loads(cleaned)
        self.assertIsNone(data["video"]["url"])
        self.assertEqual(data["video"]["title"], "test")

if __name__ == "__main__":
    unittest.main()
