import unittest
import sys
import os

# Add scripts directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts')))

from extract import parse_srt, parse_vtt
from ocr_processor import text_similarity, cluster_raw_readings, is_valid_subtitle

class TestTranscriptExtractor(unittest.TestCase):

    def test_parse_srt(self):
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

    def test_parse_vtt(self):
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

    def test_similarity_ratio(self):
        self.assertAlmostEqual(text_similarity("青蟹一生蜕壳十三次", "青蟹一生蜕壳十三次左右"), 0.85, delta=0.1)
        self.assertAlmostEqual(text_similarity("完全不相干的内容", "青蟹一生蜕壳十三次"), 0.0, delta=0.2)
        self.assertEqual(text_similarity("完全一致的内容", "完全一致的内容"), 1.0)

    def test_is_valid_subtitle(self):
        self.assertTrue(is_valid_subtitle("美食特种兵"))
        self.assertFalse(is_valid_subtitle("a"))
        self.assertFalse(is_valid_subtitle("12345"))
        self.assertFalse(is_valid_subtitle("？"))

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

if __name__ == "__main__":
    unittest.main()
