# 论文 PDF 归档测试用例

本目录保存 `04_论文PDF归档方案` 的测试输入和输出，用于验证 arXiv、Hugging Face PDF 和图表较多论文的解析效果。

## 输出位置

```text
outputs/
└── 论文标题或指定 slug/
    ├── metadata.json
    ├── paper.md
    ├── paper.json
    ├── source.pdf
    └── assets/
```

## 本轮测试用例

| 编号 | 来源 | 输入 | 验证点 |
| --- | --- | --- | --- |
| `paper-01` | DeepSeek V4 官方技术报告 | `https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/blob/main/DeepSeek_V4.pdf` | Hugging Face `blob` URL 规范化为 `resolve` PDF；技术报告正文、表格和章节可读。 |
| `paper-02` | Theory of Space | `https://arxiv.org/abs/2602.07055` | arXiv `abs` URL 规范化为 PDF；提取 arXiv ID、标题、作者和发布时间。 |
| `paper-03` | MIRAGE | `https://arxiv.org/abs/2603.21687` | 图表较多的多模态/医学 benchmark 论文；验证正文、图注、表格和 warning。 |

## 回归检查

- 每个目录均包含 `metadata.json`、`paper.md`、`paper.json`、`source.pdf`、`assets/`。
- `paper.md` 第一行是 `---`，frontmatter 含 `source_type: "paper_pdf"` 和 `canonical_source: "paper.md"`。
- `metadata.json` 中 `parser` 为 `docling`，`page_count > 0`，`content_chars > 0`。
- `metadata.json` 中 `image_scale` 为 `4.0`，`image_effective_dpi` 为 `288`。
- `metadata.json` 中 `figure_fallback_count` 与 `assets/figure_fallback_page_*.png` 数量一致；触发回退时抽查整块裁剪图是否覆盖完整 Figure。
- `paper.json` 是合法 JSON，可回查结构化块。
- `assets/` 中的图片能被 `paper.md` 的 `assets/...png` 相对链接解析。
- 不生成每篇论文自己的 `README.md`。

## 验收结果

已于 2026-05-13 使用 Docling `2.93.0` 和 `--image-scale 4.0` 重跑完三条用例。默认图片等效约 `288 DPI`，并启用复杂 Figure 整块裁剪回退，用于改善论文图表小字、坐标轴、流程图和矢量组合图的可读性。

| 用例 | 输出目录 | 页数 | 正文字符 | 图片资产 | Figure 回退 | 图片尺寸抽查 | 结构化块 | 验收结论 |
| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |
| DeepSeek V4 官方技术报告 | `outputs/deepseek-v4-technical-report/` | 58 | 174595 | `asset_count=15`，`markdown_image_refs=15`，`image_scale=4.0` | `figure_fallback_count=8`，清理未引用资产 `8` 个 | 宽度 `487-1839px`，中位宽 `1653px`，最大图 `1816x1590` | `texts=831`，`tables=9`，`pictures=15` | 通过。Hugging Face `blob` 已规范化为 `resolve`；`figure_fallback_page_0001_1_scale_4.png` 已确认只保留 Figure 1 图表，不再裁入页眉、标题或摘要；`paper.md` 第 130-132 行保持 Figure 2 图片在前、图注在后；Docling 对末尾部分页面出现 pipeline error，已写入 warning，并追加 `pypdf Tail Text Fallback` 补齐第 52-58 页文本。 |
| Theory of Space | `outputs/theory-of-space/` | 34 | 125215 | `asset_count=33`，`markdown_image_refs=33`，`image_scale=4.0` | `figure_fallback_count=13`，清理未引用资产 `11` 个 | 宽度 `42-1610px`，中位宽 `1479px`，最大图 `1445x1824` | `texts=1092`，`tables=12`，`pictures=31` | 通过。图表主图保持约 1500px 量级；抽查 `figure_fallback_page_0011_5_scale_4.png` 已去掉页眉，并保持图片在 Figure 5 图注之前；Figure 7 保留 Docling 原生双图，不再生成包含上方正文的整页回退裁剪。 |
| MIRAGE | `outputs/mirage-illusion-of-visual-understanding/` | 29 | 77591 | `asset_count=15`，`markdown_image_refs=15`，`image_scale=4.0` | `figure_fallback_count=7`，清理未引用资产 `7` 个 | 宽度 `1769-1846px`，中位宽 `1812px`，最大图 `1833x1815` | `texts=1830`，`pictures=15`，多图论文正文和图注可读 | 通过。PDF 第 10 页 Figure 4 被裁成 a-e 完整多面板图，`paper.md` 原第 121-177 行与 191-233 行附近不再混入坐标轴、图例、类别标签和 panel 文本；`figure_fallback_page_0011_5_scale_4.png` 不再裁入 Figure 5 上方自然段落，并保持图片在图注之前。 |

统一检查结果：

- 三个输出目录均包含 `metadata.json`、`paper.md`、`paper.json`、`source.pdf`、`assets/`。
- 三个 `paper.md` 第一行均为 `---`，frontmatter 含 `source_type: "paper_pdf"` 和 `canonical_source: "paper.md"`。
- 三个 `metadata.json` 均满足 `parser: "docling"`、`page_count > 0`、`content_chars > 0`、`image_scale: 4.0`、`image_effective_dpi: 288`，并记录 `figure_fallback_count` / `figure_fallbacks`。
- 三个 `paper.json` 均为合法 JSON，可读取 `texts`、`tables`、`pictures` 等结构化块。
- 三个输出目录中的图片引用均可解析，无缺失图片；图片文件可被正常打开。
- 三个输出目录均未发现真实的相邻 `caption -> image` 反序；连续多图版式中允许上一图注后接下一张图，但下一张图自身仍位于对应图注之前。
- `outputs/` 下没有 per-output `README.md`。

已知边界：

- 当前方案默认关闭 OCR、图片描述、图表抽取和公式增强，避免 Windows 环境下触发外部 `sh` / OCR 组件；这些降级会写入 `metadata.json.warnings`。
- `paper.md` 以文本阅读和证据引用为主，不复刻 PDF 视觉排版。图片本体保留在 `assets/`，复杂 Figure 回退图用于降低正文污染，并按 PDF 常见版式保持图片在前、图注在后；相邻多图场景可能表现为“上一图注后接下一图”，验收时应按对应图片和对应图注成对判断；复杂公式和复杂跨页表格仍需回查 `paper.json` 或 `source.pdf`。
- `--image-scale 4.0` 会显著增加图片像素尺寸和处理成本；批量文本回归可以临时降到 `--image-scale 2.0`。
- Theory of Space 会导出少量站点、代码和数据集小图标；未触发 Figure 回退的小图保真留存，不按尺寸过滤，验收时应单独看主图尺寸。
- 当 Docling 出现 pipeline error 时，工具会用 pypdf 追加尾页文本兜底，但这部分文本没有 Docling 的表格结构。
