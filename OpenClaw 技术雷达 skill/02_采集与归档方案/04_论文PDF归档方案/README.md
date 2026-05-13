# 论文 PDF 归档方案

本方案用于把 arXiv、Hugging Face 或普通 PDF 链接归档成后续 Agent 可以稳定读取的论文目录。

## 目标

论文 PDF 不是普通网页，也不是视频逐字稿。第一版目标是保留一份阅读主文件，同时保留结构化证据和原始 PDF：

```text
论文 PDF / arXiv URL
-> 下载或复制 source.pdf
-> 使用 Docling 解析为 Markdown 和结构化 JSON
-> 按高分辨率策略导出图片到 assets/，并在 paper.md 原位插入本地图片引用
-> 对复杂矢量 Figure 触发整块裁剪回退，清理散落进正文的图内标签
-> Docling pipeline 降级时，用 pypdf 追加尾页文本兜底
-> 写入 paper.md / paper.json / metadata.json
-> 保留原始 PDF 和 assets/
```

## 目录内容

- `archive_paper_pdf.py`：可运行 CLI，输入 arXiv、PDF URL 或本地 PDF，输出论文归档目录。
- `requirements.txt`：依赖清单。

## 安装

建议使用独立虚拟环境。Windows 上 Docling 的底层 `docling-parse` 对中文路径兼容性不好，虚拟环境路径建议放在 ASCII 路径下：

```powershell
cd "D:\project\Myskill\OpenClaw 技术雷达 skill\02_采集与归档方案\04_论文PDF归档方案"
python -m venv "D:\project\Myskill\_tmp\paper_pdf_docling_venv"
D:\project\Myskill\_tmp\paper_pdf_docling_venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果默认 pip 镜像失败，可以临时指定官方源：

```powershell
D:\project\Myskill\_tmp\paper_pdf_docling_venv\Scripts\python.exe -m pip install -i https://pypi.org/simple -r requirements.txt
```

## 使用

```powershell
D:\project\Myskill\_tmp\paper_pdf_docling_venv\Scripts\python.exe .\archive_paper_pdf.py `
  --out-root "D:\project\Myskill\OpenClaw 技术雷达 skill\04_测试用例\04_论文PDF归档\outputs" `
  --image-scale 4.0 `
  "https://arxiv.org/abs/2602.07055"
```

支持输入：

- arXiv abs URL，例如 `https://arxiv.org/abs/2602.07055`
- arXiv PDF URL，例如 `https://arxiv.org/pdf/2602.07055`
- Hugging Face PDF blob/resolve URL
- 普通 PDF URL
- 本地 PDF 路径

Hugging Face `blob` URL 会自动规范化为 `resolve` 下载 URL。arXiv 元数据优先读 `export.arxiv.org/api/query`；如果 API 限流或失败，回退到 `arxiv.org/abs/...` 页面的 citation meta 标签。

当前默认关闭 OCR、图片描述、图表抽取和公式增强，但会导出 PDF 中的图片资产，并在 `paper.md` 中保留图片出现位置。这是为了匹配 arXiv / 技术报告这类 born-digital PDF，并避免 Windows 上底层 OCR/外部组件触发 `sh` 关联弹窗。关闭的能力会写入 `metadata.json.warnings`。

图片分辨率策略：

- 默认使用 `--image-scale 4.0` 导出图片资产。Docling 按 PDF 的 72 点/英寸坐标体系计算图片尺度，因此默认等效约 `288 DPI`。
- 旧的 `images_scale = 2.0` 等效约 `144 DPI`，现有样例主图多在 800 到 900 像素宽，对论文图表中的坐标轴、小字标注和复杂流程图偏紧。
- `--image-scale` 只影响 Docling 生成或裁剪的图片资产，不开启整页图片导出，也不导出表格图片。若 PDF 内嵌位图本身分辨率很低，提高 scale 不能凭空恢复原始细节，只能减少渲染和裁剪阶段的损失。
- 需要节省解析时间和磁盘空间时，可以显式传入 `--image-scale 2.0`；需要优先保证图表可读性时，保留默认 `4.0` 或按单篇论文提高。
- `paper.md` frontmatter 和 `metadata.json` 会记录 `image_scale` 与 `image_effective_dpi`，便于后续判断该归档是否适合直接交给多模态模型读图。

复杂 Figure 回退策略：

- 有些论文 Figure 是矢量图和文本框组合。Docling 可能把坐标轴、图例、panel 标识和流程框文字识别成普通正文，导致 `paper.md` 出现大量短碎片，同时只导出 Figure 的局部图片。
- 解析器会用 `paper.json` 中的 caption、页码和 bbox 检测这种“图内标签散落”模式：同页 Figure caption 上方存在大量短文本块，且这些文本块更像图表标签而不是自然段落时，触发回退。若 Docling 提供 picture bbox，回退裁剪会优先使用和当前 caption 有结构引用的 picture；缺少引用时，只取 caption 上方最近、横向重叠的一组 picture，避免把同页 logo、页眉图或其它 Figure 合并进来。
- 裁剪范围会以相关 picture bbox 作为水平和垂直锚点，并向下延伸到 caption 上缘以包含必要的子图说明；如果 picture bbox 只覆盖多面板 Figure 的局部子图，工具会继续向上吸收同一 Figure 范围内的坐标轴、图例、panel 标识和无句读类别串，直到遇到同页其它 caption 边界为止，避免漏掉上方 a-d 面板或跨过其它 Figure。若完全没有可用 picture bbox，只允许用 caption 上方最近的短文本图表簇触发回退，不做整页上收。
- 触发后工具会直接从 `source.pdf` 按对应页的 Figure 区域渲染整块图片，写入 `assets/figure_fallback_page_*.png`，在 caption 前插入该图，保持 PDF 中“图片在上、图注在下”的阅读顺序，并从 `paper.md` 中移除被误排为正文的图内标签。Docling 原生导出的相邻图片如果被排在 caption 后，也会在 Markdown 后处理阶段统一调整为图片在前、caption 在后。
- 被回退替换后不再被 `paper.md` 引用的 Docling 局部图片会被清理，避免 `assets/` 留下无法追溯到正文的冗余文件。
- 每次触发都会写入 `metadata.json.figure_fallbacks` 和 `warnings`，并在 frontmatter 中记录 `figure_fallback_count`。这类 warning 表示已做质量修复，不等同于解析失败。

OCR 策略：

- 默认不启用 OCR，避免 born-digital PDF 出现重复正文、识别错误和额外慢路径。
- 图片先作为本地资产保留，由后续问答阶段按需交给多模态 LLM 理解。
- 只有扫描版 PDF、图片文字需要索引、或用户明确要求时，才应增加单独的 OCR 输出，不应直接污染 `paper.md` 正文。

## 输出契约

每篇论文输出一个目录：

```text
yyyy-or-title-slug/
├── metadata.json
├── paper.md
├── paper.json
├── source.pdf
└── assets/
```

默认读取顺序：

1. `paper.md`：唯一阅读主源，用于摘要、问答、技术雷达打分、技术卡片生成、认知资产沉淀。
2. `paper.json`：Docling 结构化输出，用于回查章节、页码、表格、图示和证据块。
3. `metadata.json`：完整抓取元数据和解析状态，用于查看 PDF URL、解析器版本、warning、文件哈希和目录契约。
4. `source.pdf`：原始证据文件，仅在解析质量存疑时回查。
5. `assets/`：PDF 图片资产；没有资源时也保留空目录。`paper.md` 中使用 `assets/...png` 相对路径引用，默认按 `image_scale=4.0` 生成更适合读图表小字的本地图片。

当 Docling 返回 pipeline error 时，`paper.md` 会在正文末尾追加 `## pypdf Tail Text Fallback`，补齐 PDF 尾页原始文本；对应页码范围会写入 `metadata.json.warnings`。

当检测到复杂矢量 Figure 被拆成正文碎片时，`paper.md` 会保留 caption，并在 caption 前插入整块回退裁剪图；Docling 原生图片和表格的相邻 caption 也会统一规范为“媒体在前、caption 在后”。具体页码、裁剪框、被替换图片和清理行数写入 `metadata.json.figure_fallbacks`。

`paper.md` 会在文件开头保留必要 YAML frontmatter：

```yaml
---
source_type: "paper_pdf"
source_url: "原始输入 URL"
pdf_url: "实际 PDF URL"
title: "论文标题"
authors: ["作者"]
arxiv_id: "2602.07055"
version: null
published: "发布时间"
captured_at: "抓取时间"
parser: "docling"
page_count: 0
content_chars: 0
asset_count: 0
picture_count: 0
markdown_image_refs: 0
image_scale: 4.0
image_effective_dpi: 288
figure_fallback_count: 0
canonical_source: "paper.md"
status: "raw"
---
```

## 验收标准

一篇论文归档完成后，检查：

- 目录内只有 `metadata.json`、`paper.md`、`paper.json`、`source.pdf`、`assets/`。
- `paper.md` 以 YAML frontmatter 开头，正文紧随其后。
- `metadata.json` 记录输入、PDF URL、Docling 版本、页数、正文长度、图片 scale、等效 DPI、文件哈希、warning 和文件清单。
- `paper.json` 是合法 JSON，并能用于结构化证据回查。
- `assets/` 中的图片文件能被 `paper.md` 的相对链接解析；`asset_count` 应等于 `markdown_image_refs`。触发 Figure 回退后，`asset_count` 可能大于 Docling 原始 `picture_count`。
- 如果 `figure_fallback_count > 0`，抽查对应 `assets/figure_fallback_page_*.png` 是否覆盖完整 Figure，而不是只覆盖局部 panel、同页无关图片或上方正文；多面板图还要确认没有越过其它 caption 边界；同时确认回退图位于 caption 前，图内标签没有继续散落在正文段落里。
- 不生成每篇论文自己的 `README.md`。
- 如果表格、公式、图片或 OCR 有明显降级，必须写入 `warnings`，不能静默当成完整解析。
- 如果 Docling 对部分页面解析失败，应追加 pypdf 文本兜底，或明确记录失败原因。
- 如果需要判断图片是否足够读图，优先查看 `metadata.json.image_scale` / `image_effective_dpi`，再抽查 `assets/` 中主图的实际像素尺寸；图标类小图可以保留，但不能把它们误判为论文主图质量。

## 已知边界

- 第一版面向文本型或常规学术 PDF。
- 当前默认不启用 OCR；扫描版 PDF 可能只有极少正文，后续需要显式打开 OCR 或接入专用 OCR 工具。
- 解析目标是 Agent 可读和可追溯，不追求完全复刻 PDF 排版。
- 图片资产会以默认 `image_scale=4.0` 导出到 `assets/` 并在 `paper.md` 原位引用，但不做图片语义解释；需要视觉理解时由后续问答阶段调用多模态模型读取图片或回查 `source.pdf`。
- Docling 可能导出很小的图标或装饰性图片；未触发 Figure 回退时保真留存。触发 Figure 回退后，未被 `paper.md` 引用的旧局部图片会清理。
- 复杂 Figure 回退依赖 caption、bbox 和短文本块启发式，优先解决图内标签污染正文的问题；有 picture bbox 时会优先使用与当前 caption 结构关联或最近邻的图区锚点收窄裁剪，并在局部 picture 只覆盖子面板时向上扩展同一 Figure 的图表标签，但不能跨过其它 caption 边界；无 picture bbox 时只使用靠近 caption 的短文本簇，宁可不触发回退也不做整页裁剪。若论文没有规范图注，仍需要回查 `source.pdf`。
- 更高的 `--image-scale` 会增加 Docling 解析时间和 `assets/` 体积；批量回归或只做文本阅读时可以临时调低到 `2.0`。
- 公式、复杂表格和跨页表格可能需要回查 `paper.json` 或 `source.pdf`。

## 已验证用例

实测结果见：[论文 PDF 归档测试用例](../../04_测试用例/04_论文PDF归档/README.md)。

- DeepSeek V4 官方技术报告：Hugging Face `blob` URL 可规范化下载，当前输出 15 张本地图片资产，其中 8 张是复杂 Figure 回退图；已验证 Figure 1 裁剪不再带入页眉、标题或摘要，Figure 2 等原生图片保持图片在前、图注在后；Docling 对末尾部分页面有 pipeline error，已追加 pypdf 尾页文本兜底。
- `https://arxiv.org/abs/2602.07055`：arXiv API 限流时可回退到 abs 页面 citation meta；当前输出 33 张本地图片资产，其中 13 张是复杂 Figure 回退图；已验证 Figure 5 裁剪不再带入页眉，Figure 7 不再触发包含上方正文的整页回退裁剪。
- `https://arxiv.org/abs/2603.21687`：图表较多论文可生成可读正文、结构化 JSON 和 15 张本地图片资产，其中 7 张是复杂 Figure 回退图；已验证 Figure 4 从 PDF 第 10 页裁成 a-e 完整多面板图，并清理原先散落在 `paper.md` 第 121-177、191-233 行附近的轴标签、类别标签和图例文字；Figure 5 不再裁入上方自然段落。
