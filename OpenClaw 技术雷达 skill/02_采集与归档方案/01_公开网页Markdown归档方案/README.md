# 公开网页 Markdown 归档方案

## 目标

这个方案用于把公开普通网页归档成后续 Agent 可以稳定读取的 Markdown 文件。

它借鉴 Obsidian Web Clipper 的核心模式：

```text
网页 URL
-> 提取标题、作者、发布时间、站点、正文
-> 套用固定 Markdown / frontmatter 模板
-> 写入本地 raw source
-> 返回本地路径给 OpenClaw / Hermes workflow
```

当前实现参考了 Obsidian Web Clipper/Defuddle 的几个关键做法：

- 优先抽取主正文，而不是保存完整网页外壳。
- 同时读取常规 meta、Open Graph、Twitter card 和 schema.org JSON-LD 元数据。
- 固定输出 frontmatter + Markdown 正文，便于后续 Agent 稳定消费。
- 抽取失败时明确报告“可能需要 JavaScript 渲染或站点专用 selector”，避免把空壳页面误当正文；动态页面可重跑 `--render-js`。

## 目录内容

- `archive_public_web_page.py`：可运行 CLI，输入公开网页 URL，输出 Markdown 归档目录。
- `requirements.txt`：依赖清单。

## 安装

建议在本文件夹下建独立虚拟环境：

```powershell
cd "D:\project\Myskill\OpenClaw 技术雷达 skill\02_采集与归档方案\01_公开网页Markdown归档方案"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果默认 pip 镜像失败，可以临时指定官方源：

```powershell
.\.venv\Scripts\python.exe -m pip install -i https://pypi.org/simple -r requirements.txt
```

## 使用

```powershell
.\.venv\Scripts\python.exe .\archive_public_web_page.py `
  --out-root "D:\project\Myskill\OpenClaw 技术雷达 skill\03_归档样例\web" `
  "https://example.com/article"
```

如果希望下载正文里的远程图片到本地：

```powershell
.\.venv\Scripts\python.exe .\archive_public_web_page.py `
  --download-images `
  "https://example.com/article"
```

启用 `--download-images` 后，脚本会把正文图片写入 `assets/` 并重写 Markdown 图片链接。若原网页图片缺少 `alt`，脚本会尝试从图片文件名生成可读的 Markdown 图片说明。

如果页面必须经过浏览器渲染才有正文，可启用 `--render-js`：

```powershell
.\.venv\Scripts\python.exe .\archive_public_web_page.py `
  --render-js `
  "https://example.com/app-rendered-page"
```

`--render-js` 会优先用本机 Microsoft Edge 渲染页面，并在不可用时尝试 Chrome；如果两者都不可用，需要先运行 `.\.venv\Scripts\python.exe -m playwright install chromium`。它更接近 Obsidian Web Clipper 在浏览器扩展里的输入环境，但速度比普通 HTTP 抓取慢。

脚本会在输出根目录下创建：

```text
yyyy-MM-dd-文章标题/
├── README.md
├── article.md
└── assets/
```

## 后续 Agent 读取规则

默认读取顺序：

1. `README.md`：只看来源、最终 URL、抓取方式、资源统计、注意事项。
2. `article.md`：唯一正文主源，用于摘要、问答、技术雷达打分、技术卡片生成、认知资产沉淀。
3. `assets/`：只有需要看图片、图示时才读取。

`article.md` 会包含 YAML frontmatter：

```yaml
source_type: public_web_page
source_url: 原始输入 URL
final_url: 重定向后的最终 URL
canonical_url: 页面 canonical URL
title: 页面标题
site: 站点
author: 作者
published: 发布时间
description: 页面描述
image: 社交分享图
language: 页面语言
captured_at: 抓取时间
extraction_method: trafilatura 或 dom-fallback
word_count: 正文字数估算
content_chars: 正文字符数
content_sha256: 正文内容哈希
asset_count: 本地图片数量
status: raw
```

## OpenClaw 调用约定

CLI 标准输出为 JSON。单 URL 时返回对象，多 URL 时返回数组。

关键字段：

- `OutDir`：归档目录。
- `Title`：页面标题。
- `FinalUrl`：最终 URL。
- `CanonicalUrl`：页面 canonical URL。
- `ExtractionMethod`：正文提取方式。
- `ContentChars`：正文 Markdown 字符数。
- `WordCount`：正文字数估算。
- `ContentSha256`：正文内容哈希。
- `AssetCount`：本地图片数量。
- `Warnings`：降级或图片下载失败信息。
- `Files`：目录契约。

## 验收标准

归档完成后检查：

- 目录内包含 `README.md`、`article.md`、`assets/`。
- `README.md` 记录原始链接、最终链接、站点、作者、发布时间和抓取方式。
- `article.md` 有 frontmatter 和正文。
- 正文不应包含明显导航、页脚、脚本、广告等页面噪声。
- OpenClaw 可以只读取 `article.md` 继续执行技术雷达分级。

## 已知边界

- 本方案只处理公开普通网页。
- 不绕过登录、付费墙、验证码或访问控制。
- 默认不下载图片；需要本地图片时使用 `--download-images`。
- 默认依赖 HTTP HTML 正文抽取；纯前端渲染页面可尝试 `--render-js`，仍失败时需要站点专用 selector 或人工选择正文范围。
