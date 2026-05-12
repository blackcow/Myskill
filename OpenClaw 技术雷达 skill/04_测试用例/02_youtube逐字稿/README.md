# YouTube 逐字稿测试用例

本目录保存 `03_youtube逐字稿原始字幕方案` 的真实测试输出，用于验证原始字幕轨选择、分组 Markdown、分组 JSON 和启发式 speaker 识别。

## 输出位置

- `outputs/*.original.txt`：原始逐条字幕。
- `outputs/*.original.json`：原始结构化字幕。
- `outputs/*.original.grouped.md`：按句子/时间和可选 speaker 分组后的阅读版。
- `outputs/*.original.grouped.json`：分组结构化数据。

## 用例清单

| 视频 | URL | 选择轨道 | 类型 | 分组方法 | speaker 标记 | 验证点 |
| --- | --- | --- | --- | --- | ---: | --- |
| `V9eI-t3TApE` | `https://www.youtube.com/watch?v=V9eI-t3TApE&t=1s` | `zh-Hans` | manual | `sentence-and-time` | 0 | 中文人工字幕，无可靠 speaker 标记时不硬猜说话人。 |
| `96jN2OCOfLs` | `https://www.youtube.com/watch?v=96jN2OCOfLs&t=25s` | `en` | generated | `speaker-marker-and-sentence` | 35 | 自动字幕包含 `>>` 标记，启发式区分 `speaker_0` / `speaker_1`。 |
| `ttkd0t5qTD4` | `https://www.youtube.com/watch?v=ttkd0t5qTD4` | `en-US` | manual | `sentence-and-time` | 0 | `en-GB` 带翻译声明被跳过；`en-US` 轨语言码像英文，但正文是中文访谈原文/混合内容。 |

## 回归检查

重点检查 `speaker_markers_found`、`grouping_method`、`groups[*].speaker`、`groups[*].speaker_confidence`。没有显式 speaker 标记的中文访谈不应生成 `speaker_0`；英文 `>>` 标记用例应保留 speaker 轮次。
