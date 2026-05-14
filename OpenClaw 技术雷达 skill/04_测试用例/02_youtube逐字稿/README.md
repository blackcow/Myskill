# YouTube 逐字稿测试用例

本目录保存 `03_youtube逐字稿原始字幕方案` 的真实测试输出，用于验证原始字幕轨选择、分组 Markdown、分组 JSON 和启发式 speaker 识别。

## 输出位置

```text
outputs/
└── <video_id>/
    ├── metadata.json
    ├── transcript.md
    ├── transcript.json
    ├── raw_transcript.txt
    └── raw_transcript.json
```

- `transcript.md`：唯一阅读主文件。
- `transcript.json`：分组结构化数据。
- `raw_transcript.txt` / `raw_transcript.json`：YouTube 原始字幕片段。
- `metadata.json`：轨道选择、分组方法、speaker 统计和文件清单。

## 用例清单

| 视频 | URL | 选择轨道 | 类型 | 分组方法 | speaker 标记 | 验证点 |
| --- | --- | --- | --- | --- | ---: | --- |
| `V9eI-t3TApE` | `https://www.youtube.com/watch?v=V9eI-t3TApE&t=1s` | `zh-Hans` | manual | `sentence-and-time` | 0 | 中文人工字幕，无可靠 speaker 标记时不硬猜说话人。 |
| `96jN2OCOfLs` | `https://www.youtube.com/watch?v=96jN2OCOfLs&t=25s` | `en` | generated | `speaker-marker-and-sentence` | 35 | 自动字幕包含 `>>` 标记，启发式区分 `speaker_0` / `speaker_1`。 |
| `ttkd0t5qTD4` | `https://www.youtube.com/watch?v=ttkd0t5qTD4` | `en-US` | manual | `sentence-and-time` | 0 | `en-GB` 带翻译声明被跳过；`en-US` 轨语言码像英文，但正文是中文访谈原文/混合内容。 |
| `igO8iyca2_g` | `https://www.youtube.com/watch?v=igO8iyca2_g` | `en` | generated | `sentence-and-time` | 0 | 英文自动字幕，少量 `>>` marker 和括号内舞台事件不应生成假 speaker。 |

## 回归检查

重点检查 `metadata.json` 和 `transcript.json` 中的 `speaker_markers_found`、`generic_marker_count`、`generic_marker_alternation_enabled`、`grouping_method`、`groups[*].speaker`、`groups[*].speaker_confidence`。没有显式 speaker 标记的中文访谈不应生成 `speaker_0`；英文密集 `>>` 标记用例应保留 speaker 轮次；少量 `>>` 和 `[laughter]` / `[applause]` 等事件不应生成假 speaker。`transcript.md` 必须以 YAML frontmatter 开头。

## 验收结果

- `outputs/` 根目录不再保留平铺的 `*.original.*` 文件。
- 四条用例均包含 `metadata.json`、`transcript.md`、`transcript.json`、`raw_transcript.txt`、`raw_transcript.json`。
- 四条用例的 `transcript.md` 均以 YAML frontmatter 开头，且 `canonical_source` 为 `transcript.md`。
- `96jN2OCOfLs` 保留 `35` 个 speaker 标记；`V9eI-t3TApE`、`ttkd0t5qTD4` 和 `igO8iyca2_g` 没有可靠 speaker 标记时不生成 speaker。
