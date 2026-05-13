# 采集与归档方案

本目录收纳不同信息来源的抓取、清洗和归档实现。目录按来源编号，便于在技术雷达 workflow 中稳定引用。

## 目录

| 编号 | 来源 | 方案 |
| --- | --- | --- |
| `01` | 公开普通网页 | [公开网页 Markdown 归档方案](01_公开网页Markdown归档方案/README.md) |
| `02` | 微信公众号文章 | [微信公众号文章最小归档方案](02_微信公众号文章最小归档方案/README.md) |
| `03` | YouTube transcript/caption | [YouTube 逐字稿原始字幕抓取方案](03_youtube逐字稿原始字幕方案/README.md)，按视频目录输出 `transcript.md`、结构化分组和原始字幕；speaker 只来自显式标签或足够密集的通用 marker |
| `04` | 论文 PDF / arXiv | [论文 PDF 归档方案](04_论文PDF归档方案/README.md) |
| `05` | 微信视频号视频 | [微信视频号视频归档方案](05_微信视频号视频归档方案/README.md)，支持公开元数据归档；已验证 `wx_channel` 可取得真实本地 `mp4`；脚本提供 `--auto-download wx_channel --ensure-service --ensure-wechat-pc` 的 Agentic CLI，用户提供或自动取得本地媒体时可显式开启 OpenRouter ASR |

## 输出约定

- 正式归档样例默认输出到 `..\03_归档样例`。
- 测试用例和回归输出统一放到 `..\04_测试用例`。
- 每个来源方案内部保留自己的 README、脚本和依赖清单，避免跨来源混用命令。
- YouTube 逐字稿统一以 `transcript.md` 作为阅读主文件，`metadata.json` / `transcript.json` / `raw_transcript.json` 保存轨道选择、speaker 启发式和片段级证据；少量 `>>`、`[laughter]`、`[applause]` 等事件不应生成假 speaker。
- 论文 PDF 统一以 `paper.md` 作为阅读主文件，`paper.json` 保存 Docling 结构化结果，`source.pdf` 保留原始证据，`assets/` 保存本地图片；复杂 Figure 回退必须按当前 caption 的相关图区裁剪，多面板图可在同一 Figure 内向上吸收图表标签，但不能跨过其它 caption，避免裁入页眉、logo、其它图或上方自然段落，并统一保持图片在前、图注在后。
- 微信视频号统一以 `metadata.json` 作为 Agent 入口，并以 `video.md` 作为唯一阅读主文件；`feed.json` 保存公开预览接口响应；真实媒体只通过 `source_media.<ext>` 进入归档，`transcript.json.text` 保存 ASR 全文，OpenRouter 不提供说话人分离时必须记录 speaker 降级。`metadata.json` 必须记录 `canonical_source`、`agent_reading_order`、`file_roles` 和 `media_acquisition`，避免后续 Agent 在多个文件之间猜入口；自动下载未满足微信侧前置条件时也要生成标准归档并写明结构化失败状态。
