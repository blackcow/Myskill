# 微信视频号视频归档方案

本方案面向 `https://weixin.qq.com/sph/<short_uri>` 这类微信视频号公开分享页。当前公开预览页可稳定归档作者、发布时间、视频文案、封面图、头像、认证图标、互动计数和原始预览接口响应。

公开 `sph` 链接本身通常不暴露字幕轨、音频地址或可下载视频地址。因此字幕生成采用增强链路：用户提供本地视频/音频文件后，脚本用 `ffmpeg` 抽取音频，再调用 OpenRouter ASR 生成 `transcript.json`，并把逐字稿正文合并进唯一阅读主文件 `video.md`。

2026-05-13 已验证外部媒体获取层：在微信 PC 端打开目标视频号页面后，`wx_channel V5.6.2` 可以通过本地 API 搜索作者、读取视频列表、拉取 Profile 中的 `url + urlToken + decodeKey`，并通过 `batch_start` 下载和解密成本地 `mp4`。归档脚本已支持 `--auto-download wx_channel --ensure-service` 的 Agentic 调用路径，可以自动拉起 `wx_channel`、检测微信 PC 和视频号注入状态、尝试匹配并下载媒体；但仍不能绕过微信登录、代理证书和微信客户端页面注入这些微信侧前置条件。

## 输出契约

每条视频输出到一个独立目录：

```text
outputs/<slug>/
├── metadata.json
├── video.md
├── transcript.json
├── feed.json
├── source_media.<ext>          # 仅在用户提供 --media-file 或自动下载成功时生成
└── assets/
    └── audio/                  # 仅在执行 ASR 时生成
```

- `metadata.json`：Agent 默认入口，记录抓取状态、降级 warning、文件清单、`canonical_source`、`agent_reading_order` 和 `file_roles`。
- `video.md`：唯一阅读主文件，第一行必须是 YAML frontmatter，`canonical_source` 为 `video.md`。ASR 或人工字幕成功时，逐字稿正文也写在本文件的“逐字稿”章节。
- `transcript.json`：结构化字幕结果。没有字幕时 `segments` 为空，`transcript_status` 为 `unavailable_no_public_subtitle_or_video_url`。
- `feed.json`：微信视频号公开预览接口的原始响应。
- `source_media.<ext>`：可选的原始媒体证据文件，角色类似论文归档中的 `source.pdf`。
- `assets/`：本地化封面、头像、认证图标；执行 ASR 时额外保存抽取后的音频切片。

后续 Agent 读取顺序固定为：先读 `metadata.json` 判断状态和 `canonical_source`，再读 `video.md` 理解内容；只有需要时间戳、分段、ASR 细节或 speaker 降级时才读 `transcript.json`；只有需要溯源或 debug 微信字段时才读 `feed.json`。

## 当前能力边界

已验证的 `sph` 公开接口只返回视频元数据和封面，不返回字幕轨、音频地址或可下载视频地址。因此默认模式不做 ASR，也不伪造字幕。

如果用户提供原始视频/音频文件，可以显式传入 `--asr openrouter` 运行 ASR。当前默认 ASR 模型为 `openai/gpt-4o-mini-transcribe`，适合作为成本和质量平衡的归档默认值；高价值视频可手动切到 `openai/gpt-4o-transcribe`。

`--media-file` 单独使用时只复制源媒体，不产生费用；只有同时传入 `--asr openrouter` 才会调用 OpenRouter。

`--auto-download wx_channel` 会把媒体获取结果写入 `metadata.json.media_acquisition`。如果 `wx_channel` 或微信 PC 没准备好，命令仍会输出标准归档目录，并用状态码说明失败层级，例如 `WX_CHANNEL_NOT_RUNNING`、`NEED_WECHAT_LOGIN_OR_CHANNEL_PAGE`、`CONTACT_SEARCH_FAILED`、`FEED_MATCH_FAILED`、`PROFILE_MEDIA_NOT_FOUND`、`DOWNLOAD_FAILED` 或 `DOWNLOAD_TIMEOUT`。如果同时请求 `--asr openrouter` 但媒体获取失败，`transcript_status` 会降级为 `unavailable_media_acquisition_failed`，不会静默伪造字幕。

## 媒体获取工具层

核心归档脚本不内置微信客户端抓包、代理证书安装或解密逻辑。这部分交给外部桌面工具处理，拿到本地 `mp4` / `m4a` / `wav` 后再交给本方案的 `--media-file --asr openrouter`。

当前本机已准备两个外部工具，放在 `D:\project\Myskill\_reference\wechat-media-tools`，该目录在仓库忽略列表中，不进入技能源码：

| 工具 | 本机文件 | 用途 | 验证 |
| --- | --- | --- | --- |
| `wx_channel` | `wx_channel_V5.6.2.exe` | 首选。专门面向微信视频号，支持单个下载、批量下载、加密视频解密、按作者分类和下载记录。 | SHA256 `7CA8977239CC0F796744FDF3BCCB81D9869F762AAB0F80BEF49A897D78AD7D53`，匹配 GitHub Release API；已完成 `AFH1y1aqSF` 真实下载和 ASR 回归。 |
| `res-downloader` | `res-downloader_3.1.3_win_amd64.exe` | 兜底。通用资源嗅探器，支持视频号、小程序、抖音、快手、小红书、m3u8、直播流和音频资源。 | SHA256 `AC0EDE0E25B5FF687AD56098BF9757E428CA0D6126C677546ED77652EF967344`，匹配 GitHub Release API。 |

推荐人工操作链路：

```text
微信 PC 端打开或播放视频号内容
-> 启动外部下载工具并按工具说明安装证书 / 启动代理
-> 下载并解密得到本地 mp4，或至少得到本地音频文件
-> 用 archive_wechat_channel_video.py 传入原始 sph 链接和 --media-file
-> OpenRouter ASR 生成 transcript.json，并把逐字稿正文写入 video.md
```

注意：外部工具通常需要修改系统代理、安装本地证书并读取微信客户端播放时的网络请求。它们只适合用户确认有权归档的内容；不要把这些交互式步骤做成默认自动化，也不要在无人确认时启动。

## Agentic CLI 设计

面向 Agent 的推荐入口是一条命令：

```powershell
python .\archive_wechat_channel_video.py `
  "https://weixin.qq.com/sph/AzRPLyKxfv" `
  --out-dir "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\05_微信视频号视频\outputs" `
  --auto-download wx_channel `
  --ensure-service `
  --ensure-wechat-pc `
  --asr openrouter `
  --asr-language zh
```

执行状态机固定为：

```text
公开 sph 元数据归档
-> 检查/启动微信 PC
-> 检查/启动 wx_channel
-> 检查 2025 health 和 2026 channels status
-> 搜索作者并匹配 username
-> 拉取作者 feed 列表并按公开文案匹配目标视频
-> 拉取 profile 的 url + urlToken + decodeKey
-> batch_start 下载并轮询 batch_progress
-> 复制成本目录 source_media.<ext>
-> 可选 OpenRouter ASR
-> 写入 video.md / transcript.json / metadata.json
```

`--ensure-service` 只解决 `wx_channel.exe` 没启动的问题；`--ensure-wechat-pc` 会尝试启动 `WeChat.exe` 或 `Weixin.exe`，但登录态、扫码、视频号页面注入仍需要用户账号环境。当前脚本会把这些边界变成结构化状态，而不是把整个归档任务变成无输出失败。

### wx_channel 实测链路

`wx_channel` 启动后，本地服务有两层：

- `http://127.0.0.1:2025/api/health`：主服务健康检查和批量下载接口。
- `http://127.0.0.1:2026/api/channels/status`：视频号页面注入状态和 WebSocket API 能力。

实测 `AFH1y1aqSF` 的关键步骤：

1. 微信 PC 端打开视频号页面，`/api/channels/status` 返回 `connected=true`、`ready_clients=1`。
2. 通过 `/api/channels/contact/search?keyword=晓辉博士` 找到作者 `username`。
3. 通过 `/api/channels/contact/feed/list?username=...` 读取作者视频列表，并匹配目标文案。
4. 通过 `/api/channels/feed/profile?object_id=...&nonce_id=...` 取得 `media[0].url`、`media[0].urlToken`、`media[0].decodeKey`。
5. 向 `http://127.0.0.1:2025/__wx_channels_api/batch_start` 提交 `videos[]`，字段包含 `id`、`url + urlToken`、`title`、`authorName`、`key`。
6. 轮询 `http://127.0.0.1:2025/__wx_channels_api/batch_progress`，完成后在 `downloads/<作者>/` 下得到本地 `mp4`。

这一路径得到的是微信可播放的转码版本，不一定等于 Profile 中 `fileSize` 标注的原始/高码率文件。`AFH1y1aqSF` Profile 显示 `fileSize=306545060`，实际下载文件为 `18,151,606` bytes，时长 `00:05:28.49`，足够用于 ASR 归档。

## 使用方式

```powershell
Set-Location "D:\project\Myskill\OpenClaw 技术雷达 skill\02_采集与归档方案\05_微信视频号视频归档方案"
python .\archive_wechat_channel_video.py `
  --out-dir "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\05_微信视频号视频\outputs" `
  "https://weixin.qq.com/sph/AFH1y1aqSF"
```

带人工字幕：

```powershell
python .\archive_wechat_channel_video.py `
  --out-dir "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\05_微信视频号视频\outputs" `
  --manual-transcript "D:\path\to\transcript.txt" `
  "https://weixin.qq.com/sph/AFH1y1aqSF"
```

带本地媒体文件并调用 OpenRouter ASR：

```powershell
python .\archive_wechat_channel_video.py `
  --out-dir "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\05_微信视频号视频\outputs_asr" `
  --media-file "D:\path\to\wechat-channel-video.mp4" `
  --asr openrouter `
  --asr-language zh `
  --asr-context "专有名词：OpenClaw，Agent Teams，AI Research。请保留英文拼写。" `
  "https://weixin.qq.com/sph/AFH1y1aqSF"
```

使用 `wx_channel` 自动获取媒体并调用 OpenRouter ASR：

```powershell
python .\archive_wechat_channel_video.py `
  --out-dir "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\05_微信视频号视频\outputs" `
  --auto-download wx_channel `
  --ensure-service `
  --ensure-wechat-pc `
  --asr openrouter `
  --asr-language zh `
  --asr-context "专有名词：OpenClaw，红杉资本，Sequoia Capital，AI，Agent。请保留英文拼写。" `
  "https://weixin.qq.com/sph/AzRPLyKxfv"
```

ASR 参数建议：

- `--asr-model` 默认 `openai/gpt-4o-mini-transcribe`；质量优先时可改为 `openai/gpt-4o-transcribe`。
- `--asr-language` 可填 `zh`，不填则由模型自动识别。
- `--asr-chunk-seconds` 默认 `60`，用于生成近似时间戳；长视频建议保持切片，避免单次请求过大。
- `--asr-audio-format` 默认 `mp3`，更省上传体积；需要极致保真时可改为 `wav`。
- `--asr-context` 用于补充术语表、人物名和产品名。实测专有名词容易被错识别，提供上下文能明显改善。

## 解析策略

1. 从 `weixin.qq.com/sph/<short_uri>`、`channels.weixin.qq.com/finder-preview/pages/sph?id=<short_uri>` 或裸 `short_uri` 解析短链 ID。
2. 调用 `https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info`，请求体为 `{"baseReq":{"generalToken":""},"shortUri":"..."}`。
3. 提取 `authorInfo`、`feedInfo`、`sceneInfo`、`errMsg`。
4. 下载 `coverUrl`、`headImgUrl`、`authIconUrl` 到 `assets/`。
5. 生成唯一阅读主文件 `video.md`、结构化字幕状态 `transcript.json` 和来源快照 `feed.json`；如果没有公开字幕/视频地址，在 `metadata.json.warnings` 中写明降级原因。
6. 如果传入 `--auto-download wx_channel`，脚本先走本地 `wx_channel` API 尝试下载媒体，并把结果写入 `metadata.json.media_acquisition`。
7. 如果传入 `--media-file --asr openrouter`，或自动下载成功且传入 `--asr openrouter`，从本地媒体抽取音频切片，调用 `https://openrouter.ai/api/v1/audio/transcriptions`，并把文本、近似时间戳、模型、费用和 warning 写入标准输出文件。
8. 如果媒体来自 `wx_channel`、`res-downloader` 或其他外部工具，正文文件名仍统一为 `source_media.<ext>`；工具名、下载方式和授权边界进入 `metadata.json.media_acquisition`。

## ASR 输出规则

- `transcript_status` 为 `asr_openrouter_completed` 或 `manual_provided` 时，`video.md` 的“逐字稿”章节可作为真实逐字稿读取。
- `transcript.json.segments[]` 按音频切片生成，`start` / `end` 是近似时间边界，不是词级时间戳。
- `transcript.json.text` 保存所有分段拼接后的全文，便于后续 Agent 直接读取；分段证据仍以 `segments[]` 为准。
- OpenRouter transcription API 当前返回文本，不提供说话人分离；因此 `speaker_diarization_status` 记录为 `not_available_openrouter_transcriptions`。
- `metadata.json.asr.usage.cost` / `metadata.json.asr.cost_usd` 记录 OpenRouter 返回的费用，便于后续做成本审计。
- 技术词、英文产品名和人物名应写入 `--asr-context`；如果不传，脚本会自动用标题、作者和公开视频文案构造一个简短上下文。
- 脚本会从 `--asr-context` 中提取英文术语，只做大小写归一，例如把 `OpenCLaW` 归一为 `OpenClaw`；不会把不同单词强行替换成术语。

## 已知边界

- 公开 `sph` 页面会提示扫码到微信观看，网页端不展示真实播放控件。
- `feed/get_feed_info` 在 `sph` 模式下没有返回 `videoUrl`、`h264VideoInfo.videoUrl`、`h265VideoInfo.videoUrl` 或字幕字段。
- 作者主页和更多视频列表需要微信内打开，公开网页端没有稳定列表接口。
- 因为缺少源音频/视频，公开链接无法直接运行 ASR；必须由用户提供本地视频/音频文件，或通过 `--auto-download wx_channel` / `res-downloader` 等外部工具取得本地媒体。
- OpenRouter ASR 不等同于视频理解：它只识别音轨，不理解画面内容。
- 当前归档脚本可以调用 `wx_channel` 的本地 API 自动搜索、匹配和触发下载，但不自行抓包、安装代理证书或绕过微信客户端解密；如果微信 PC 没登录或没有 ready 的视频号页面注入客户端，会明确降级为 `NEED_WECHAT_LOGIN_OR_CHANNEL_PAGE`。
