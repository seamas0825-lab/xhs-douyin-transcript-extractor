# XHS & Douyin Extraction Technical Reference

## 1. 小红书 (Xiaohongshu) 逆向与解析深度指南

### 1.1 数据定位与水合对象
小红书网页端（SSR）将全局状态注入在 HTML 的 `<script>window.__INITIAL_STATE__ = {...};</script>` 中。

### 1.2 关键陷阱与防坑指南
1. **`undefined` 语法陷阱**：
   小红书的 JavaScript 对象中大量直接写入裸 `undefined`。解析前必须执行正则替换：
   ```python
   clean_state_str = re.sub(r':\s*undefined\b', ': null', raw_state_str)
   ```
2. **`mediaV2` 字符串序列化陷阱**：
   在 `note.video` 下，`mediaV2` 字段通常是一个被 JSON 转义的字符串（如 `"{\"video\": ...}"`）。必须进行二次反序列化：
   ```python
   media_v2 = video_obj.get("mediaV2")
   if isinstance(media_v2, str):
       media_v2 = json.loads(media_v2)
   ```
3. **字幕优先级**：
   ```python
   subtitles = media_v2.get("video", {}).get("subtitles", {})
   # 优选顺序: zh-CN -> source -> en-US -> 其他
   ```

---

## 2. 抖音 (Douyin) 逆向与解析深度指南

### 2.1 TTWID 统一注册网关
抖音网页 API 要求携带 `ttwid` 身份 Cookie，可通过字节跳动公开的 Union 注册网关免登陆获取：
* **Endpoint**: `https://ttwid.bytedance.com/ttwid/union/register/`
* **Payload**:
  ```json
  {
    "region": "cn",
    "aid": 1768,
    "needFid": "false",
    "service": "www.ixigua.com",
    "migrate_info": {"ticket": "", "source": "node"},
    "cbUrlProtocol": "https",
    "union": "true"
  }
  ```

### 2.2 视频详情与字幕字段
* **API**: `https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={video_id}&aid=6383&device_platform=webapp&channel=channel_pc_web`
* **软字幕路径**: `aweme_detail.video.cla_info.caption_infos[0].url`
* **直链路径**: `aweme_detail.video.play_addr.url_list[0]`

---

## 3. 跨平台硬件加速 OCR 架构设计 (macOS & Windows)

### 3.1 macOS (Apple Vision Framework + ANE)
* **API**: `VNRecognizeTextRequest` + `AVAssetReader`
* **硬件**: Apple Neural Engine (ANE) + Apple GPU / CPU
* **特点**: 原生内置 0 依赖，毫秒级快速流式处理，多语言自适应。

### 3.2 Windows (WinRT + DirectML / ONNX)
* **原生 API**: `Windows.Media.Ocr.OcrEngine` (Windows 10/11 内置)
* **ML 高精度引擎**: `RapidOCR` (ONNX Runtime + DirectML / CUDA)
* **特点**: 支持 Windows Copilot+ NPU 硬件加速与 GPU DirectML，无缝支持高难度花字与竖排文字。

---

## 4. 严格数据治理准则
1. **真实性第一**：任何口播稿与分镜必须与抓取结果严格对应，禁止凭空臆断。
2. **环境清洁**：临时创建的任何测试与多模态切片必须在退出前通过 `finally` 块彻底清除。
