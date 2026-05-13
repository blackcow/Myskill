# 微信视频号视频测试用例

本目录保存 `05_微信视频号视频归档方案` 的测试输入和输出。

## 输出结构

```text
outputs/
└── <slug>/
    ├── metadata.json
    ├── video.md
    ├── transcript.json
    ├── feed.json
    ├── source_media.<ext>    # 仅真实媒体/ASR 用例生成
    └── assets/
        └── audio/            # 仅 ASR 用例生成
```

`outputs_asr_smoke/` 只保留本地合成短音频的 API 冒烟测试；真实微信视频下载和 ASR 回归输出放在 `outputs/2026-03-27-晓辉博士-AFH1y1aqSF/` 和 `outputs/2026-05-05-小Fai哥看世界-AzRPLyKxfv/`。临时 agentic CLI 调优目录不作为长期样例保留，测试结论记录在本文档。

Agent 读取顺序固定为：先读 `metadata.json` 判断状态、`canonical_source`、`agent_reading_order` 和 `file_roles`；再读 `video.md` 理解内容。`transcript.json` 只在需要时间戳、分段、ASR 细节或 speaker 降级时读取；`feed.json` 只在需要溯源或 debug 微信公开接口字段时读取。

## 测试输入

| id | 输入 | 作者 | 类型 | 预期 |
| --- | --- | --- | --- | --- |
| `AFH1y1aqSF` | `https://weixin.qq.com/sph/AFH1y1aqSF` | 晓辉博士 | 视频号 `sph` 公开分享页 | 能归档作者、日期、文案、封面和互动数；若无公开字幕轨/视频地址，必须明确标记字幕不可用。 |
| `AFH1y1aqSF-asr-smoke` | 同上 + `fixtures/asr-smoke-zh.wav` | 晓辉博士 | 用户提供本地媒体后的 ASR 冒烟测试 | 不代表该视频真实字幕，只验证 `--media-file --asr openrouter` 能抽音频、调用 OpenRouter ASR、生成标准逐字稿。 |
| `AFH1y1aqSF-wx-channel-asr` | 同上 + `wx_channel` 下载的真实 `mp4` | 晓辉博士 | 真实微信视频下载 + OpenRouter ASR 回归 | 验证微信 PC 端打开后可通过外部工具取得媒体，并生成真实 ASR 逐字稿。 |
| `AzRPLyKxfv-wx-channel-asr` | `https://weixin.qq.com/sph/AzRPLyKxfv` + `wx_channel` 下载的真实 `mp4` | 小Fai哥看世界 | 真实微信视频下载 + OpenRouter ASR 回归 | 验证只给 `sph` 链接时，可在微信 PC 登录态和 `wx_channel` 服务可用的前提下按作者 feed 匹配、下载并 ASR。 |
| `AzRPLyKxfv-agentic-cli` | `https://weixin.qq.com/sph/AzRPLyKxfv` + `--auto-download wx_channel --ensure-service --ensure-wechat-pc` | 小Fai哥看世界 | Agentic CLI 编排测试 | 验证只给链接时脚本能启动/检查微信 PC 与 `wx_channel`，并在微信侧未 ready 时生成标准归档和结构化失败状态。 |

## 额外用例发现状态

按用户要求，已尝试通过公开网页搜索和搜狗微信搜索查找“晓辉博士”视频号的更多 `sph` 分享链接。公开搜索能找到多篇提到“晓辉博士视频号”的公众号文章和播客页面，但没有暴露稳定的 `weixin.qq.com/sph/...` 短链。作者主页点击也只弹出微信扫码，不给公开更多视频列表。

因此当前测试集先固定用户提供的 `AFH1y1aqSF` 作为强制回归用例；后续如果用户从微信里分享更多“晓辉博士”视频号链接，可直接追加到上表，并用同一脚本归档。

## 媒体获取工具状态

核心堵点不是 ASR，而是公开视频号 `sph` 链接不给视频/音频源。2026-05-13 已补齐本机外部工具层，工具保存在 `D:\project\Myskill\_reference\wechat-media-tools`：

| 工具 | 版本 | 本机文件 | 用途 | 状态 |
| --- | --- | --- | --- | --- |
| `wx_channel` | `V5.6.2` | `wx_channel_V5.6.2.exe` | 首选微信视频号下载、批量下载和加密视频解密工具。 | 已下载，SHA256 与 GitHub Release API 一致；2026-05-13 已启动并完成 `AFH1y1aqSF` 真实下载。 |
| `res-downloader` | `3.1.3` | `res-downloader_3.1.3_win_amd64.exe` | 通用资源嗅探兜底，可覆盖视频号、小红书、抖音、m3u8、直播流和音频。 | 已下载，SHA256 与 GitHub Release API 一致；未自动启动，因为首次运行会涉及证书和代理。 |

后续真实视频号 ASR 测试应使用以下链路：先用外部工具取得本地 `mp4` 或音频文件，再运行 `archive_wechat_channel_video.py --media-file ... --asr openrouter`。如果只拿到音频，也可以直接作为 `--media-file` 输入。

## 验收要求

- 每条成功用例必须生成 `metadata.json`、`video.md`、`transcript.json`、`feed.json` 和 `assets/`；真实媒体用例额外生成 `source_media.<ext>` 和 `assets/audio/`。
- `metadata.json` 必须包含 `canonical_source: "video.md"`、`agent_reading_order` 和 `file_roles`。
- `video.md` 第一行必须为 `---`，frontmatter 含 `source_type: "wechat_channels_video"` 和 `canonical_source: "video.md"`，并作为唯一阅读主 Markdown。
- 没有字幕时，`metadata.json.transcript_status` 和 `transcript.json.transcript_status` 必须为 `unavailable_no_public_subtitle_or_video_url`。
- 没有字幕时，`metadata.json.warnings` 必须包含“不返回字幕轨”和“不返回视频/音频地址”的说明。
- 用户提供媒体并显式传入 `--asr openrouter` 时，`metadata.json.transcript_status` 和 `transcript.json.transcript_status` 应为 `asr_openrouter_completed` 或明确的 partial 状态。
- 使用 `--auto-download wx_channel` 时，`metadata.json.media_acquisition` 必须记录 `status`、`method`、服务健康检查、微信 PC 启动状态、匹配/下载过程或失败原因；即使媒体获取失败，也必须保留 `metadata.json`、`video.md`、`transcript.json`、`feed.json` 和 `assets/`。
- 如果同时传入 `--asr openrouter` 且媒体获取失败，`transcript_status` 必须降级为 `unavailable_media_acquisition_failed`，并在 warnings 中记录 `ASR_SKIPPED`。
- ASR 输出必须记录 `metadata.json.asr.model`、`metadata.json.asr.cost_usd`、`metadata.json.asr.chunk_count` 和 `metadata.json.speaker_diarization_status`。
- `transcript.json.text` 必须保存所有分段拼接后的全文，不能只有 `segments[].text`。
- `feed.json` 必须是合法 JSON，并可回查 `authorInfo`、`feedInfo.description`、`feedInfo.coverUrl` 和 `sceneInfo`。
- 不生成 per-output `README.md`，标准输出也不生成 `transcript.md`。

## 实测记录

2026-05-12 已完成多轮测试：

| 用例 | 输出目录 | 结果 |
| --- | --- | --- |
| `AFH1y1aqSF` | `outputs/2026-03-27-晓辉博士-AFH1y1aqSF/` | 通过。生成 `metadata.json`、`video.md`、`transcript.json`、`feed.json` 和 `assets/`；本地化 `cover.jpg`、`avatar.jpg`、`auth_icon.png`。 |

已额外验证输入形态：

- `https://weixin.qq.com/sph/AFH1y1aqSF`
- `AFH1y1aqSF`
- `https://channels.weixin.qq.com/finder-preview/pages/sph?id=AFH1y1aqSF`

实际结论：

- 公开接口可回查 `authorInfo.nickname = 晓辉博士`、发布时间 `2026-03-27T23:24:42+08:00`、视频文案、互动数和封面 URL。
- 公开接口没有返回字幕轨、音频地址或可下载视频地址；因此 `transcript_status` 正确降级为 `unavailable_no_public_subtitle_or_video_url`。
- `video.md` 以 YAML frontmatter 开头，是唯一阅读主文件；`transcript.json` 记录字幕不可用状态。
- `metadata.json.warnings` 已记录字幕和 ASR 无法执行的原因，没有静默当作完整字幕。

2026-05-12 ASR 增强链路已完成冒烟测试：

| 用例 | 输出目录 | 结果 |
| --- | --- | --- |
| `AFH1y1aqSF-asr-smoke` | `outputs_asr_smoke/2026-03-27-晓辉博士-AFH1y1aqSF/` | 通过。使用本地中文短音频 fixture、OpenRouter `openai/gpt-4o-mini-transcribe`、`--asr-language zh` 和 `--asr-context` 生成逐字稿。 |

ASR 测试命令：

```powershell
python "D:\project\Myskill\OpenClaw 技术雷达 skill\02_采集与归档方案\05_微信视频号视频归档方案\archive_wechat_channel_video.py" `
  --out-dir "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\05_微信视频号视频\outputs_asr_smoke" `
  --media-file "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\05_微信视频号视频\fixtures\asr-smoke-zh.wav" `
  --asr openrouter `
  --asr-language zh `
  --asr-chunk-seconds 30 `
  --asr-context "专有名词：OpenClaw，微信视频号，归档转写。请保留 OpenClaw 的英文拼写。" `
  "https://weixin.qq.com/sph/AFH1y1aqSF"
```

ASR 实测结果：

- `metadata.json.transcript_status = asr_openrouter_completed`
- `metadata.json.asr.model = openai/gpt-4o-mini-transcribe`
- `metadata.json.asr.chunk_count = 1`
- `metadata.json.asr.cost_usd = 0.0001925`
- `metadata.json.asr.canonical_terms = ["OpenClaw"]`
- `metadata.json.speaker_diarization_status = not_available_openrouter_transcriptions`
- `transcript.json.segments[0].text = 你好,OpenClaw,这是一段微信视频号归档转写测试。`
- 逐字稿正文写入 `video.md` 的“逐字稿”章节；`transcript.json` 保留结构化分段。

优化结论：

- 不传术语上下文时，专有名词可能被识别为相近英文词；因此脚本新增 `--asr-context`，并在未传参时自动用标题、作者和公开视频文案构造上下文。
- `--asr-temperature` 默认固定为 `0.0`，用于降低归档场景的随机波动。
- 对 `--asr-context` 中提取出的英文术语，脚本只做大小写归一；这样可以修正 `OpenCLaW` 这类大小写波动，但不会把 `OpenAI` 这类不同单词硬改成目标术语。
- OpenRouter transcription 当前不返回说话人分离和词级时间戳；测试输出只记录近似切片时间边界。

2026-05-13 真实微信视频下载和 ASR 回归已完成：

| 用例 | 输出目录 | 结果 |
| --- | --- | --- |
| `AFH1y1aqSF-wx-channel-asr` | `outputs/2026-03-27-晓辉博士-AFH1y1aqSF/` | 通过。先用 `wx_channel` 从微信 PC 端页面取得并解密本地 `mp4`，再用 OpenRouter ASR 生成真实逐字稿。 |
| `AzRPLyKxfv-wx-channel-asr` | `outputs/2026-05-05-小Fai哥看世界-AzRPLyKxfv/` | 通过。只输入 `sph` 链接后，使用微信 PC 登录态和 `wx_channel` 通过作者搜索、feed 列表匹配、Profile 和 batch 下载取得真实 `mp4`，再用 OpenRouter ASR 生成逐字稿。 |

媒体获取实测：

- `wx_channel` 主服务健康检查：`http://127.0.0.1:2025/api/health` 返回 `status=ok`、`version=5.6.2`。
- 视频号注入状态：`http://127.0.0.1:2026/api/channels/status` 返回 `connected=true`、`ready_clients=1`。
- 作者搜索：`/api/channels/contact/search?keyword=晓辉博士` 命中 `nickname=晓辉博士`、`authProfession=科技博主`。
- 作者视频列表：`/api/channels/contact/feed/list?username=...` 返回 15 条，第一条匹配目标文案。
- 目标 Profile：`object_id=14886634164146145417`，`decodeKey=477919219`，`videoPlayLen=328`。
- 批量下载：`/__wx_channels_api/batch_start` 返回 `success total=1`，`batch_progress` 最终 `status=done progress=100%`。
- 本地媒体：`D:\project\Myskill\_reference\wechat-media-tools\downloads\晓辉博士\Agent Teams设计的两种思路_14886634164146145417.mp4`，`18,151,606` bytes，`00:05:28.49`，HEVC + AAC。

真实 ASR 命令：

```powershell
python "D:\project\Myskill\OpenClaw 技术雷达 skill\02_采集与归档方案\05_微信视频号视频归档方案\archive_wechat_channel_video.py" `
  "https://weixin.qq.com/sph/AFH1y1aqSF" `
  --out-dir "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\05_微信视频号视频\outputs" `
  --media-file "D:\project\Myskill\_reference\wechat-media-tools\downloads\晓辉博士\Agent Teams设计的两种思路_14886634164146145417.mp4" `
  --asr openrouter `
  --asr-language zh `
  --asr-context "专有名词：OpenClaw，Agent Teams，Anthropic，AI Research。请保留英文拼写。"
```

真实 ASR 验收结果：

- `metadata.json.transcript_status = asr_openrouter_completed`
- `metadata.json.asr.model = openai/gpt-4o-mini-transcribe`
- `metadata.json.asr.chunk_count = 6`
- `metadata.json.asr.cost_usd = 0.01127875`
- `metadata.json.transcript_chars = 2181`
- `transcript.json.text` 长度为 `2181`
- `transcript.json.segments` 数量为 `6`
- `metadata.json.speaker_diarization_status = not_available_openrouter_transcriptions`
- `video.md` 包含“逐字稿”章节，`metadata.json.agent_reading_order = ["metadata.json", "video.md", "transcript.json", "feed.json"]`
- `outputs/2026-05-05-小Fai哥看世界-AzRPLyKxfv/` 同步通过新契约校验：`chunk_count=9`，`cost_usd=0.01532`，`transcript_chars=8388`，`segments=9`，且不生成 `transcript.md`。

质量观察：

- 对 `Agent Teams`、`Anthropic`、`multi-agent` 等术语整体可读；个别模型名被转为近音词，例如 `Opus 4.6` 被识别成 `Open4.6`，这类内容应在高价值视频二次校对。
- 当前说话人为单人视频，OpenRouter transcription 没有 diarization 字段，输出中 `speaker` 保持 `null` 是正确降级。
- Profile 中 `fileSize=306545060` 不等于实际下载文件大小；`wx_channel` 批量下载得到的是微信可播放的转码版本，不应把 Profile 原始大小当成本地文件验收依据。

2026-05-13 Agentic CLI 编排链路已完成测试：

| 用例 | 输出目录 | 结果 |
| --- | --- | --- |
| `AzRPLyKxfv-agentic-cli` | 临时调优目录 | 通过。只输入 `sph` 链接，脚本可自动检查/启动微信 PC 与 `wx_channel`，公开视频归档仍生成完整标准文件；当前环境下 `wx_channel` 服务健康，但视频号注入客户端未 ready，因此结构化降级为 `NEED_WECHAT_LOGIN_OR_CHANNEL_PAGE`。 |
| `AzRPLyKxfv-agentic-cli-asr-failed` | 临时调优目录 | 通过。传入 `--asr openrouter` 但媒体获取失败时，命令不再崩溃，`transcript_status=unavailable_media_acquisition_failed`，warnings 记录 `ASR_SKIPPED`。 |
| `AFH1y1aqSF-agentic-cli-local-media` | 临时调优目录 | 通过。继续使用本地合成短音频验证 `--media-file --asr openrouter` 路径未被 agentic 改动破坏，`transcript_status=asr_openrouter_completed`。 |

Agentic CLI 推荐命令：

```powershell
python "D:\project\Myskill\OpenClaw 技术雷达 skill\02_采集与归档方案\05_微信视频号视频归档方案\archive_wechat_channel_video.py" `
  "https://weixin.qq.com/sph/AzRPLyKxfv" `
  --out-dir "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\05_微信视频号视频\outputs" `
  --auto-download wx_channel `
  --ensure-service `
  --ensure-wechat-pc `
  --asr openrouter `
  --asr-language zh
```

Agentic CLI 设计结论：

- Agent 以后可以只收到 `sph` 链接后直接调用脚本；脚本负责启动/探测微信 PC 与 `wx_channel`，并把服务健康、ready 状态、作者搜索、feed 匹配、Profile、下载进度和本地媒体文件写入 `metadata.json.media_acquisition`。
- 如果微信 PC 未登录、代理证书未确认或视频号页面注入客户端未 ready，脚本不会伪造逐字稿，也不会让任务无产物失败；它会保留公开视频归档，并用 `NEED_WECHAT_LOGIN_OR_CHANNEL_PAGE` 等状态提示需要用户补齐前置条件。
- 真正可完全自动的部分是“工具编排、状态检查、匹配下载、标准归档、ASR”；不可由 Agent 绕过的是微信账号登录、客户端安全确认和页面注入环境。
