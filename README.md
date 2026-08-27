# XHS & Douyin Transcript Extractor (小红书/抖音短视频字幕与剧本提取工具)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![CI & Quality Gate](https://github.com/seamas0825-lab/xhs-douyin-transcript-extractor/actions/workflows/ci.yml/badge.svg)](https://github.com/seamas0825-lab/xhs-douyin-transcript-extractor/actions)
[![macOS](https://img.shields.io/badge/macOS-Apple%20Vision%20%2B%20ANE-black.svg?logo=apple)](https://developer.apple.com/documentation/vision)
[![Windows](https://img.shields.io/badge/Windows-WinRT%20%2B%20DirectML-0078D6.svg?logo=windows)](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr)
[![Token Efficiency](https://img.shields.io/badge/Token%20Efficiency-High-brightgreen.svg)](#-为什么选择本项目)

用于提取 **小红书（RED）** 与 **抖音（Douyin）** 视频的时间戳字幕（SRT/WebVTT）、无水印流地址与口播分镜剧本的轻量级工具。

优先获取平台原生软字幕；对于仅压制了硬字幕的视频，调用本地硬件加速 OCR（macOS **Apple Vision** / Windows **WinRT OCR**）进行帧扫描，避免将完整大体积视频直接送入 LLM 上下文所带来的高额 Token 消耗与长文本遗漏问题。

---

## 🌟 核心特性 (Key Features)

- **⚡ 软字幕优先解析**：通过小红书原生 SSR 状态与字节网关 `ttwid` 鉴权，优先拉取平台官方 `.srt` / `.vtt` 软字幕。
- **👁️ 本地平台 OCR 加速与画面变化检测**：
  - **macOS**：原生 Swift + Apple Vision Framework，支持可用硬件加速（ANE/GPU），内置像素级字幕区域指纹变化比对，自动跳过静态画面以提升长视频处理速度；
  - **Windows**：WinRT `Windows.Media.Ocr` (异步事件循环) / RapidOCR (DirectML / ONNX Runtime)；
  - **动态采样与去噪**：根据视频真实 FPS 动态计算 5fps 均匀采样，结合编辑距离（Levenshtein Ratio）聚合连续字幕行并过滤短小杂标点。
- **🛡️ 自动资源回收机制 (Lifecycle Cleanup)**：通过 `atexit` 与进程信号陷阱（`SIGINT`/`SIGTERM`）在脚本退出时尝试清理临时媒体切片。
- **🔒 严格网络安全与重定向防护**：强制启用 CA 证书链 TLS 校验，配合 `SafeRedirectHandler` 对所有重定向跳数执行域名白名单校验，防范 SSRF 探测，并设单流最大下载上限保护。
- **🤖 适配 AI Agent / Antigravity / Claude Code / Codex**：既可作为独立命令行工具调用，也提供了标准 Agent Skill 定义。

---

## 🏗️ 架构分层设计 (Architecture)

本方案明确区分为 **CLI 命令行引擎（2 轨自动闭环）** 与 **Agent Skill 交互层（3 轨全流程覆盖）**：

```mermaid
flowchart TD
    subgraph CLI ["CLI 命令行引擎 (extract.py)"]
        A[输入小红书 / 抖音分享链接] --> B{是否存在官方软字幕?}
        B -- 是 --> C[【第一轨】抓取官方 .srt / .vtt 软字幕]
        B -- 否 --> D{视频是否存在硬字幕?}
        D -- 是 --> E[【第二轨】本地硬件加速 OCR 逐帧提取字幕]
        D -- 否 --> F[输出结构化 JSON 并标记多模态就绪]
    end

    subgraph Agent ["Agent Skill 交互层 (SKILL.md)"]
        F --> G[【第三轨】ffmpeg 轻量切片 -> Agent 多模态音画深度识别]
        C --> H[输出标准 4 段式专业剧本报告]
        E --> H
        G --> H
    end
```

---

## 🚀 快速上手 (Quick Start)

### 1. 克隆仓库
```bash
git clone https://github.com/seamas0825-lab/xhs-douyin-transcript-extractor.git
cd xhs-douyin-transcript-extractor
```

### 2. 编译本地加速引擎 (macOS)
```bash
# macOS 编译原生 Apple Vision OCR 引擎 (仅需一次)
swiftc -O -o scripts/ocr_engine scripts/ocr_engine.swift
chmod +x scripts/ocr_engine
```

*(Windows / Linux 用户可运行 `pip install -r requirements.txt` 安装可选 OCR 库)*

### 3. 执行提取
```bash
python3 scripts/extract.py "<小红书或抖音分享链接/口令>"
```

**示例输入**：
```bash
python3 scripts/extract.py "美食特种兵之长沙！不吃辣星人怎么吃！ https://v.douyin.com/q0mwVdBpp6I/"
```

**JSON 输出结构**：
```json
{
  "platform": "douyin",
  "video_id": "7645079463847284020",
  "title": "美食特种兵之长沙！不吃辣星人怎么吃！",
  "author": "神奇海挪",
  "likes": 50845,
  "collects": 5720,
  "duration_seconds": 489,
  "has_subtitles": true,
  "extraction_mode": "apple_vision_framework",
  "cues": [
    {
      "start": "00:00:00.166",
      "end": "00:00:01.566",
      "text": "美食特种兵之三天两夜"
    }
  ],
  "transcript": "美食特种兵之三天两夜 长沙人是不是喝得太好了..."
}
```

---

## 🧪 运行单元测试 (Unit Tests)

测试集包含了完整的真实 Payload Fixture、SRT/VTT 解析器、SSRF 重定向拦截器、Levenshtein 聚类算法与临时文件生命周期管理验证：

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

---

## ⚙️ 技术边界与局限说明 (Limitations & Notes)

1. **软字幕 vs 硬字幕**：官方软字幕提取效率最高且最精确；硬字幕提取依赖画质与字迹清晰度，如遇模糊、极小字体或复杂动态花字，识别可能存在微小偏差。
2. **画面贴纸与 UI 干扰**：算法已对下半屏区域与文本连续性做启发式过滤，但在满屏覆盖大量促销贴纸、弹幕或大型花字时，可能偶发将非口播文本纳入候选。
3. **平台接口变动**：小红书与抖音网页接口可能随平台更新调整，若遇解析失败，请检查网络环境或提交 Issue。
4. **清理机制边界**：临时文件清理属于 Best-Effort 机制，在进程被操作系统 `SIGKILL` 强杀或断电等异常情况下可能无法触发回调。

---

## 💻 跨平台支持矩阵 (Platform Support)

| 平台 | OCR 底层实现 | 硬件加速支持 | 外部依赖 |
| :--- | :--- | :--- | :--- |
| **macOS** | **Apple Vision Framework** (Swift) | 可利用 Apple Neural Engine / Apple GPU | 系统自带 (0 外部依赖) |
| **Windows** | **WinRT `Windows.Media.Ocr`** / **RapidOCR** | 可利用 Windows NPU (Copilot+) / DirectML / CUDA | `winsdk` / `rapidocr_onnxruntime` |
| **Linux** | **RapidOCR** / **Tesseract** | 可利用 CPU / CUDA | `rapidocr_onnxruntime` |

---

## 📜 开源协议 (License)

本项目采用 [MIT License](LICENSE) 开源协议。
