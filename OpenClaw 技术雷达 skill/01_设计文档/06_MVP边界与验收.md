# 06 MVP 边界与验收

## 什么时候读

当需要讨论“第一版到底做多大”“怎么证明它有效”时，读这篇。

## MVP 目标

第一版只验证一件事：

> 技术雷达 Agent 是否能帮助用户减少低价值阅读，并把少量高价值信息沉淀为认知资产。

## 第一版核心链路

```text
输入一条链接或原文
-> 识别来源
-> 调用来源工具抓取并生成标准归档
-> 对归档结果做轻量验收
-> 方向分类
-> L0-L4 重要性分级
-> 推荐注意力投入
-> 生成技术卡片
-> L4 才进入认知资产库
```

## 第一版必须支持

- 普通网页文章。
- 微信公众号短链自动归档。
- YouTube 已有 transcript/caption 的视频。
- arXiv / PDF 论文自动归档。
- 微信视频号 `sph` 公开分享页的元数据、文案和封面归档。
- 手工粘贴正文作为所有来源的降级输入。

## 第一版半自动支持

- 更复杂的论文结构理解：
  - 第一版保留 Docling 结构化 JSON 和原始 PDF。
  - 第一版按默认 `image_scale=4.0` 导出图片资产并在 `paper.md` 原位引用，但不提前做图片语义理解。
  - 复杂矢量 Figure 如果被拆成正文碎片，第一版允许按 caption 和 bbox 生成整块裁剪回退图；有 picture bbox 时优先使用当前 caption 关联或最近邻的图区锚点收窄裁剪，若 picture bbox 只覆盖多面板 Figure 的局部子图，可向上扩展同一 Figure 的图表标签，但必须以其它 caption 作为边界，避免带入页眉、logo、其它图或上方自然段落；无 picture bbox 时只允许靠近 caption 的短文本图表簇触发回退；`paper.md` 保持图片在前、图注在后，并清理散落的图内标签。
  - Docling 部分页面失败时，用 pypdf 追加文本兜底并记录页码。
  - 不急于做公式语义理解、图表问答、默认 OCR 或跨页表格精修。
- GitHub 仓库：
  - 支持 README / docs / release 优先。
  - 不急于完整仓库深度代码分析。
- 微信公众号链接：
  - 主路径是短链自动归档。
  - 登录、付费、验证码、被删除文章允许手工复制原文或网页快照。
- 微信视频号：
  - 主路径是 `sph` 公开分享页的元数据、文案、封面和互动数归档。
  - 公开预览页无字幕轨或视频/音频地址时，只记录字幕不可用，等待用户提供媒体或字幕。
  - 用户提供本地视频/音频文件，或先通过外部工具取得本地媒体时，可显式开启 OpenRouter ASR，生成半自动逐字稿。
  - 外部工具层首选 `wx_channel`，通用兜底为 `res-downloader`；`wx_channel` 已实测可通过本地 API 取得并解密 `mp4`，脚本提供 `--auto-download wx_channel --ensure-service --ensure-wechat-pc` 的 Agentic CLI，负责检查/启动微信 PC 与 `wx_channel`、匹配作者和视频、触发下载并把结果写入 `metadata.json.media_acquisition`。登录态、代理证书确认和视频号页面注入失败仍是微信侧前置条件，必须结构化降级，不纳入完全无人值守 MVP。
- X / Twitter：
  - 先处理单条内容和外链。
  - 不急于完整线程分析。
- YouTube：
  - 主路径是已有 transcript/caption。
  - 只选择 YouTube 已暴露的原始字幕轨，不调用自动翻译轨。
  - speaker 只来自显式标签或足够密集的通用 marker；少量 `>>` 和括号内舞台事件不能生成假 speaker。
  - 无字幕视频只记录为信号或等待用户提供 transcript。

## 第一版暂不作为核心

- YouTube 音视频下载和 ASR 转写。
- 微信视频号完全无人值守音视频下载、抓包、代理证书安装和绕过客户端解密；第一版只做可由 Agent 调用的本地工具编排，并在微信侧前置条件不满足时保留标准归档和失败状态。
- 真实说话人 diarization / 声纹区分。
- 自动监控全网来源。
- 自动生成周报。
- 自动生成汇报材料。
- 团队协作权限。
- 完整前端产品。
- 复杂图谱可视化。

## MVP 输出

每条信息至少输出：

- 来源类型。
- 所属方向。
- 一句话摘要。
- L0-L4 重要性等级。
- 推荐注意力投入。
- 核心理由。
- 关键证据。
- 推荐下一步。

L2 以上输出技术卡片。

L4 输出认知资产条目。

## 验收标准

第一版是否有效，看这些指标：

- 是否能稳定过滤掉低价值信息。
- 是否能解释为什么某条信息值得或不值得读。
- 是否能避免只做摘要。
- 是否能把少数高价值信息转成可复用判断。
- 用户一周后能否通过资产库快速回忆“这条信息为什么重要”。

来源工具还需要满足以下工程验收：

- 公开网页输出 `metadata.json`、`article.md`、`assets/`；`article.md` 以必要 frontmatter 开头，正文不应混入明显导航、广告、页脚。
- 微信公众号输出 `metadata.json`、`article.md`、`assets/`；正文文件以必要 frontmatter 开头，不应残留远程 `mmbiz.qpic.cn` 图片、`iframe`、`script` 或明显运营尾巴。
- YouTube 输出 `metadata.json`、`transcript.md`、`transcript.json`、`raw_transcript.txt`、`raw_transcript.json`；`transcript.md` 以必要 frontmatter 开头且 `canonical_source` 为 `transcript.md`；无可靠 speaker 标记时不硬猜说话人；显式标签或足够密集的 `>>` / `- ` marker 才可以启发式标记 speaker；`[music]`、`[laughter]`、`[applause]` 等事件应单独成组且不生成 speaker；`metadata.json` 必须记录 `speaker_markers_found`、`generic_marker_count` 和 `generic_marker_alternation_enabled`。
- 论文 PDF 输出 `metadata.json`、`paper.md`、`paper.json`、`source.pdf`、`assets/`；`paper.md` 以必要 frontmatter 开头并原位引用本地图片，复杂 Figure 不应把坐标轴、图例、panel 标识、流程框文字或长类别标签当作普通正文散落，回退图应覆盖完整 Figure，不能只覆盖局部 panel，也不应裁入页眉、logo、其它图或上方自然段落，无 picture bbox 的页面不应整页上收，并应保持图片在前、图注在后；`metadata.json` 记录 parser、页数、正文长度、图片数量、图片 scale、等效 DPI、Figure 回退、warning 和文件哈希；Docling 降级时必须记录 warning，必要时追加 pypdf 尾页文本兜底。
- 微信视频号输出 `metadata.json`、`video.md`、`transcript.json`、`feed.json`、`assets/`；真实媒体用例额外保留 `source_media.<ext>` 和 `assets/audio/`；`metadata.json` 必须记录 `canonical_source: "video.md"`、`agent_reading_order`、`file_roles` 和 `media_acquisition`；`video.md` 以必要 frontmatter 开头并作为唯一阅读主 Markdown，引用本地封面，ASR 或人工字幕成功时包含“逐字稿”章节；无公开字幕轨或视频/音频地址时，`transcript_status` 必须明确降级，`metadata.json.warnings` 必须写明原因；`--auto-download wx_channel` 未满足微信侧前置条件时，`media_acquisition.status` 必须写明 `NEED_WECHAT_LOGIN_OR_CHANNEL_PAGE` 等原因，不能把公开文案当成逐字稿；用户提供媒体或外部工具获取媒体并执行 ASR 时，`metadata.json.asr` 必须记录模型、费用、切片数、语言、上下文提示和 speaker 降级状态，`transcript.json.text` 必须保留可直接读取的全文。
- 每个来源都要记录原始 URL、抓取方式、发布时间或语言等可用元数据，以及失败/降级原因。

## 非目标

第一版不是：

- AI 新闻聚合器。
- 知识库搜索系统。
- 自动周报系统。
- 自动研究助手全家桶。
- 企业情报平台。

## 后续增强

后续再考虑：

- 更完整的多源自动抓取和监控。
- 历史内容聚类。
- 技术趋势追踪。
- 技术路线对比。
- 自动生成 POC backlog。
- 自动生成阶段性回顾、周报或团队分享初稿。
- 接入 OpenClaw / Harness 工作流。
- 接入企业交付智能化知识资产。
- YouTube 无字幕视频的音频 ASR 与 diarization。
- 微信视频号 ASR 后的更精细时间戳、说话人 diarization 和多模态画面理解。

## 后续阅读

- 如果要继续讨论形态，读：[05_工作流与系统形态.md](05_工作流与系统形态.md)
- 如果要继续讨论开放问题，读：[07_待讨论问题.md](07_待讨论问题.md)
