---
name: xhs-douyin-transcript-extractor
description: 一键提取小红书（RED）与抖音（Douyin）视频的官方带时间戳字幕（SRT/WebVTT）、高清无水印直链与口播文案。小红书直取官方 mediaV2 软字幕，抖音优先提取 CC 软字幕并支持流式转写，解析完成后自动清理临时文件，严格基于真实音画数据，绝不编造虚构。当用户发送小红书/抖音链接，或要求“提取视频文案”、“获取视频脚本”、“小红书字幕”、“抖音台词提取”、“短视频转文字”时触发。
---

# XHS & Douyin Transcript Extractor (小红书/抖音极速字幕与剧本提取大师)

本 Skill 专门用于对 **小红书（Xiaohongshu）** 和 **抖音（Douyin）** 视频进行底层数据直取、官方软字幕解析、流式音频转写与剧本级结构化还原。

---

## ⚡ 核心原则与安全红线 (Core Rules)

> [!IMPORTANT]
> 1. **严禁编造虚构（Zero-Hallucination Policy）**：
>    - 绝不允许仅凭视频标题、封面或简介凭空臆测台词、剧情、菜单价格或人物对白！
>    - 必须 100% 以实际抓取到的官方字幕（`.srt` / `.vtt`）或真实音视频数据流为唯一准绳。
>    - 若遇风控或数据异常，必须如实向用户说明，严禁胡乱填补内容。
> 2. **零磁盘泄漏（Zero-Leakage Cleanup）**：
>    - 遇到无外挂软字幕的视频进行本地音视频流式解析时，**解析完成后必须立即彻底清理所有临时下载的音视频文件（`scratch/*.mp4` / `scratch/*.mp3`）**，确保本地磁盘 0 残留。

---

## 🛠️ 核心工作流 (Execution Workflow)

当收到用户发送的小红书或抖音分享链接时，执行以下 3 步：

### 第一步：运行底层提取引擎
在终端运行本 Skill 内置的提取脚本：

```bash
/Users/seamaslee/.gemini/config/skills/xhs-douyin-transcript-extractor/scripts/extract.py "<URL_OR_SHARE_TEXT>"
```

* **小红书机制**：通过原生 SSR (`__INITIAL_STATE__`) 解析 `mediaV2` 对象，秒级直取官方 `.srt` 字幕文本（仅几 KB 流量）。
* **抖音机制**：通过字节网关注册 `ttwid` 鉴权，优先获取 `cla_info` 软字幕；无软字幕时自动返回最小无水印流地址 `smallest_stream_url`。

---

### 第二步：智能分流与跨平台双轨提取处理

1. **第一轨：官方软字幕直取（0.05秒极速通道）**：
   - 若视频包含官方字幕（XHS `mediaV2` / 抖音 `cla_info`），`extract.py` 瞬间获取 100% 真实的 `cues`（毫秒时间戳）与 `transcript`。

2. **第二轨：跨平台本地原生硬件加速 OCR（硬字幕高精通道）**：
   - 若视频无官方软字幕（`has_subtitles: false`，博主仅压制了硬字幕），`extract.py` 会自动分发至 `ocr_processor.py`，根据当前操作系统自适应调用本地硬件加速引擎：
     - **macOS**：自动调用本地编译的 `ocr_engine`（基于 **Apple Vision Framework** 与 **Apple Neural Engine** 硬件加速）；
     - **Windows**：自动调用 **Windows 10/11 原生 WinRT `Windows.Media.Ocr`** 或 **RapidOCR (DirectML/ONNX Runtime)** 进行本地毫秒级加速；
     - 自动以 5fps 采样扫描视频字幕图层，动态合并连续台词、生成毫秒级时间戳、去除闪烁重复；
     - 全程临时媒体文件自动清理，**严格保证 0 磁盘泄漏、0 冗余 Token 与 0 幻觉**。

3. **第三轨：极低码率多模态音画理解备用通道**：
   - 针对无字幕但有复杂画面动作的解说/影视类长视频，可使用 `ffmpeg` 抓取轻量切片配合 `view_file` 进行多模态动作识别，并在解析后立即清理临时文件。

---

### 第三步：输出标准化的 4 段式专业报告

严格按照以下格式呈现给用户：

```markdown
### 📊 1. 视频核心基础信息 (Metadata)
- **视频标题/正文**: [完整 Caption 及话题 Hashtags]
- **创作者**: @博主名称 (平台: 小红书 / 抖音)
- **视频时长**: [分:秒]
- **互动数据**: 点赞 | 收藏
- **字幕提取模式**: 官方软字幕直取 (0.05s) / 流式音频解析

---

### 🎙️ 2. 完整版口播文案 (Polished Transcript)
> [连续、通顺、排版清晰的完整口播纯文本段落]

---

### 🎬 3. 分镜头与角色对白剧本 (Timestamped Dialogue Script)
| 时间轴 | 说话人 / 画面动线 | 原生口播台词 / 关键动作 |
| :--- | :--- | :--- |
| **00:00 - 00:05** | 【主角】[动作/神态] | “台词内容……” |
| **00:06 - 00:15** | 【搭档/背景音】 | “互动台词……” |
| ... | ... | ... |

---

### 💡 4. 核心亮点与配方/干货拆解 (Key Takeaways)
- [提炼视频中涉及的核心干货，如大厨配方比例、旅行避坑点、好物评测结论等]
```

---

## 📖 参考资料
- 深入的逆向数据结构、`mediaV2` 二次解析原理与常见避坑点请查阅 `references/guidelines.md`。
