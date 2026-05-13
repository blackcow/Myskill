# YouTube 逐字稿原始字幕抓取方案

这个方案用于抓取 YouTube 视频的“原始表述字幕”，并在不下载音频、不做声纹识别的前提下，生成一份更适合阅读和后续 Agent/RAG 使用的分组逐字稿。

- 英文视频优先拿英文原始字幕。
- 中文视频优先拿中文原始字幕。
- 不调用 YouTube 自动翻译接口。
- 优先使用视频已有字幕轨，不下载视频本体。
- 说话人区分只使用字幕文本里的显式线索，属于启发式结果，不等同于真实 diarization。

## 结论

当前最稳的落地方式是：

1. 用 `youtube-transcript-api` 做程序化抓取和轨道选择。
2. 选择策略只在 YouTube 已有 transcript/caption 轨里选，不调用 `translate(...)`。
3. 若视频有人工字幕，优先选择更像原始语言的人工字幕。
4. 若只有自动字幕，选择 YouTube 暴露的原始自动字幕。
5. 在原始字幕输出之外，额外生成启发式分组文件，用于改善自动字幕碎片化和部分访谈场景的说话人阅读体验。
6. 若 YouTube 网络层出现 TLS EOF、429、bot check 等问题，脚本内置重试；仍失败时再考虑 cookies、代理或改用 `yt-dlp` 辅助。

## 目录内容

- `fetch_original_transcript.py`：可运行脚本，负责选择原始字幕轨并导出原始逐字稿和分组逐字稿。
- `requirements.txt`：依赖清单。

## 安装

建议在本文件夹下建独立虚拟环境：

```powershell
cd "D:\project\Myskill\OpenClaw 技术雷达 skill\02_采集与归档方案\03_youtube逐字稿原始字幕方案"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果默认 pip 镜像失败，可以临时指定官方源：

```powershell
.\.venv\Scripts\python.exe -m pip install -i https://pypi.org/simple -r requirements.txt
```

## 使用

```powershell
.\.venv\Scripts\python.exe .\fetch_original_transcript.py `
  --out-dir .\transcripts `
  "https://www.youtube.com/watch?v=V9eI-t3TApE&t=1s" `
  "https://www.youtube.com/watch?v=96jN2OCOfLs&t=25s" `
  "https://www.youtube.com/watch?v=ttkd0t5qTD4"
```

输出文件：

```text
<out-dir>/
└── <video_id>/
    ├── metadata.json
    ├── transcript.md
    ├── transcript.json
    ├── raw_transcript.txt
    └── raw_transcript.json
```

- `transcript.md`：唯一阅读主文件，给后续 Agent 做摘要、技术雷达分级、技术卡片生成。
- `transcript.json`：启发式分组后的结构化数据，保留 group 时间戳、speaker、speaker label 和 speaker confidence。
- `raw_transcript.txt`：原始逐条字幕的可读文本，方便人工快速核对。
- `raw_transcript.json`：原始结构化字幕，保留 YouTube 返回的 `start`、`duration`、`text`，作为片段级证据源。
- `metadata.json`：完整抓取元数据，记录输入 URL、canonical URL、选择轨道、选择原因、分组方法、speaker 标记统计和文件清单。

`transcript.md` 会在文件开头保留必要 YAML frontmatter，包括 `source_type`、`source_url`、`video_id`、字幕轨语言码、轨道类型、抓取时间、分组方法、speaker 统计和 `status`。完整抓取细节放入 `metadata.json`。

## 轨道选择规则

脚本的核心目标是避开“看起来是中文、但其实是英文自动翻译”或“看起来是英文、但带翻译声明”的情况。

选择顺序：

1. 不调用翻译接口，只读取 YouTube 已经暴露的字幕轨。
2. 如果存在自动生成字幕轨，把它视为 YouTube 暴露的源语言线索。
3. 如果存在同语言的人工字幕，优先同语言人工字幕。
4. 如果只有人工字幕，取样前几行，跳过带明显翻译声明的轨道，例如 `Translated by AI`、`for reference only`、`机器翻译`、`仅供参考`。
5. 如果仍无法判断，选择第一条人工字幕，并在结果中标记低置信度。

注意：YouTube 的 `language_code` 是字幕轨元数据，不总是等同于正文真实语言。实际归档时应同时看 `language_code`、选择原因和正文样本。

## 启发式分组与说话人规则

YouTube transcript/caption 的原始片段通常只有 `text`、`start`、`duration`，没有可靠的结构化 speaker 字段。因此当前方案不做音频 diarization，也不下载音频。

脚本只在字幕文本本身包含显式线索时标记说话人：

- 行首 `>>` 或 `- `：只有当同一条字幕里出现足够密集的通用 marker 时，才视为说话人轮换线索；少量 marker 只清理符号，不硬猜 speaker。
- `[music]`、`[laughter]`、`[applause]`、`[screaming]` 等括号内非语音事件会单独成组，不参与 speaker 轮换。
- `主持人：`、`嘉宾：`、`访谈者：`、`受访者：`、`Host:`、`Guest:` 等标签：识别为显式 speaker label。
- 短中文姓名或英文 Title Case 名称后接 `:` / `：`：保守识别为 speaker label；含数字或过长标签会被跳过，避免把比例、章节、普通说明误判成说话人。
- 一旦出现 speaker 标记，后续无标记碎片会继承当前 speaker，直到出现新的 speaker 标记、长停顿、句子边界或时间上限。
- 如果整条字幕没有任何可靠 speaker 标记，只按句子和时间窗口合并，不硬猜对话者。

分组输出会写入这些元数据：

- `speaker_detection: "heuristic"`
- `grouping_method: "speaker-marker-and-sentence"` 或 `"sentence-and-time"`
- `speaker_markers_found`
- `generic_marker_count`
- `generic_marker_alternation_enabled`
- 每个 group 的 `speaker`、`speaker_label`、`speaker_confidence`

## 后续 Agent 读取规则

默认读取顺序：

1. `transcript.md`：唯一阅读主源。
2. `transcript.json`：需要精确时间戳、speaker 轮次或片段级证据时读取。
3. `raw_transcript.json` / `raw_transcript.txt`：需要回到 YouTube 原始字幕片段时读取。
4. `metadata.json`：需要查看轨道选择、抓取方式、warning 或目录契约时读取。

## 已验证样例

测试命令：

```powershell
$env:PYTHONPATH='D:\project\Myskill\_tmp\ytdeps'
python "D:\project\Myskill\OpenClaw 技术雷达 skill\02_采集与归档方案\03_youtube逐字稿原始字幕方案\fetch_original_transcript.py" `
  --out-dir "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\02_youtube逐字稿\outputs" `
  "https://www.youtube.com/watch?v=V9eI-t3TApE&t=1s" `
  "https://www.youtube.com/watch?v=96jN2OCOfLs&t=25s" `
  "https://www.youtube.com/watch?v=ttkd0t5qTD4" `
  "https://www.youtube.com/watch?v=igO8iyca2_g"
```

验证结果：

| 视频 | 选择轨道 | 轨道类型 | 原始片段数 | 分组数 | speaker 标记数 | 分组方法 | 说明 |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| `V9eI-t3TApE` | `zh-Hans` | manual | 6221 | 413 | 0 | `sentence-and-time` | 中文字幕无可靠 speaker 标记，只做句子/时间合并。 |
| `96jN2OCOfLs` | `en` | generated | 893 | 101 | 35 | `speaker-marker-and-sentence` | 自动字幕包含密集 `>>` 标记，启发式区分为 `speaker_0` / `speaker_1`；括号内事件单独成组。 |
| `ttkd0t5qTD4` | `en-US` | manual | 6889 | 435 | 0 | `sentence-and-time` | 另一个 `en-GB` 轨带翻译声明被跳过；`en-US` 轨语言码像英文，但正文是中文访谈原文/混合内容。 |
| `igO8iyca2_g` | `en` | generated | 855 | 114 | 0 | `sentence-and-time` | 只有英文自动字幕轨；少量 `>>` marker 来自舞台事件/段落起点，不启用 speaker 轮换。 |

四条样例均输出为：

```text
outputs/<video_id>/
├── metadata.json
├── transcript.md
├── transcript.json
├── raw_transcript.txt
└── raw_transcript.json
```

## `yt-dlp` 辅助命令

当需要排查字幕轨列表时，`yt-dlp` 很有用：

```powershell
python -m yt_dlp --list-subs "https://www.youtube.com/watch?v=VIDEO_ID"
```

只下载字幕、不下载视频：

```powershell
python -m yt_dlp --skip-download --write-subs --write-auto-subs `
  --sub-langs "zh-Hans,en-US,en-GB,en-orig,en" `
  --sub-format "vtt" `
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

但自动化主路径不建议直接固定 `--sub-langs "zh-Hans,en"`，因为英文视频的 `zh-Hans` 往往是机器翻译轨，容易偏离“原始表述”目标，也更容易触发 429。

## 边界

- YouTube 不总是明确告诉第三方工具“哪条人工字幕是原始语言”，所以多人工字幕场景只能做启发式判断。
- YouTube 原始 transcript/caption 通常没有结构化 speaker 字段；当前 speaker 结果只来自字幕文本中的标记。
- 自动字幕质量取决于 YouTube ASR，本身可能有错字、断句和重复片段。
- YouTube 对频繁请求可能返回 429 或 bot check；批量抓取要限速、重试，并尽量优先原始轨，减少自动翻译请求。
- 如果视频完全没有 transcript/caption 轨，需要另走 ASR：下载音频后用 Whisper/faster-whisper 等转写；如果还要真实区分说话人，需要 WhisperX、pyannote 等 diarization 流程。
