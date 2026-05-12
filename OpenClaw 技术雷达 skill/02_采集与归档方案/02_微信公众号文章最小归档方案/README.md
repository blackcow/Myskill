# 微信公众号文章最小归档方案

## 目标

面向后续 agent，微信公众号文章只保留一个语义主版本，避免同一篇文章同时存在 HTML、PDF、截图、纯文本 Markdown 等重复信息源。

每篇文章的归档目录只允许包含：

```text
README.md
article.structured.local.md
assets/
```

## 后续 agent 读取规则

默认读取顺序：

1. `README.md`：只看来源、标题、发布时间、抓取方式、资源统计、注意事项。
2. `article.structured.local.md`：唯一正文主源，用于摘要、问答、技术雷达打分、技术卡片生成、认知资产沉淀。
3. `assets/`：只有需要看图片、图示、视频封面时才读取。

不要让后续 agent 读取或依赖：

- 原始公众号 HTML。
- 中间正文 HTML。
- PDF。
- 长截图。
- 图片集中在末尾的纯文本 Markdown。

这些格式适合人工验收或调试，不适合作为长期知识库主源。

## 最优处理方案

当前验证后，推荐抓取链路如下：

```text
微信公众号短链 /s/<token>
-> 使用真实浏览器 User-Agent 抓取 HTML
-> 优先抽取 #js_content
-> 若失败，兜底抽取 content_noencode
-> 清理公众号 profile / 脚本 / 非正文组件 / 运营尾巴
-> 下载正文图片到 assets/
-> 将微信视频 iframe 替换为 视频封面 + 原视频链接
-> 生成 article.structured.local.md
-> 生成 README.md
```

关键结论：

- `r.jina.ai` 对微信公众号文章不可靠，常返回“环境异常”。
- 参数化长链接 `s?__biz=...&mid=...&idx=...&sn=...` 不稳定，即使补 `chksm` 也可能失败。
- 短链配真实浏览器 UA 是当前更稳的主路径。
- `content_noencode` 是有价值的正文兜底源。
- 微信视频不能离线保留真实播放能力，应保留视频封面和原视频链接。
- 公众号尾部的“好文推荐”“© THE END”“转载/投稿”等运营块会污染长期知识库，脚本会在正文后半段保守截断这些尾巴，并删除不再被正文引用的本地资源。

## 脚本

脚本路径：

```powershell
.\archive-wechat-article-minimal.ps1
```

推荐用法：

```powershell
Set-Location "D:\project\Myskill\OpenClaw 技术雷达 skill\02_采集与归档方案\02_微信公众号文章最小归档方案"
.\archive-wechat-article-minimal.ps1 `
  -Url "https://mp.weixin.qq.com/s/t09DBqWAlujcUOfa3iWtCQ" `
  -OutRoot "D:\project\Myskill\OpenClaw 技术雷达 skill\03_归档样例"
```

脚本会在 `OutRoot` 下创建一个文章目录，目录内只生成：

```text
README.md
article.structured.local.md
assets/
```

## 归档命名

默认目录名：

```text
yyyy-MM-dd-文章标题
```

如果标题过长，脚本会自动截断并移除 Windows 文件名非法字符。

如果需要指定目录名：

```powershell
.\archive-wechat-article-minimal.ps1 `
  -Url "https://mp.weixin.qq.com/s/..." `
  -OutRoot "D:\project\Myskill\OpenClaw 技术雷达 skill\03_归档样例" `
  -Slug "2026-04-25-anthropic-release"
```

## 验收标准

一篇文章归档完成后，检查：

- 目录内只有 `README.md`、`article.structured.local.md`、`assets/`。
- `README.md` 记录原文链接、标题、发布时间、抓取方式。
- `article.structured.local.md` 中图片按阅读顺序出现。
- Markdown 中不应出现 `https://mmbiz.qpic.cn` 远程图片链接。
- Markdown 中不应出现 `<iframe>`。
- `assets/` 中不应保留正文未引用的资源文件。
- 如果原文有视频，应出现视频封面图和“打开原视频”链接。

## 已验证样例

测试输出位于 `..\..\04_测试用例\03_微信公众号文章\outputs`。

| URL | 标题 | 抓取方式 | 本地资源数 | 结果 |
| --- | --- | --- | ---: | --- |
| `https://mp.weixin.qq.com/s/NmYcxPkGABkwYzG2WwZrSA` | 别再把长文切碎了，HiLight让AI直接在原文里划重点 | `desktop-chrome` | 10 | 成功；远程图片、iframe、script 残留均为 0。 |
| `https://mp.weixin.qq.com/s/hYMj375l9Y29kOhehuOkZQ` | 一年后Claude不需要Harness工程了？产品和工程负责人爆料：搭建Agent的最终难关是基础设施壁垒；Harness和模型正高度配对 | `desktop-chrome` | 2 | 成功；尾部推荐文章块已清理。 |
| `https://mp.weixin.qq.com/s/xzDlmVYH_PXDSkrOW1iX1Q` | GraphRAG的断臂，被OKH-RAG攻克了，让AI读懂因果链条 | `desktop-chrome` | 5 | 成功；正文和图片均可归档。 |

## 已知边界

- 公众号反爬策略可能变化，抓取失败时不要先改知识库产物，先检查脚本抓取阶段。
- 登录限定、付费、被删除、需要验证码的文章不保证可自动抓取。
- 如果图片下载失败，脚本会跳过该图片并在控制台输出 warning；这种情况需要人工决定是否重试。
- 本方案不绕过验证码、不处理登录态、不做任何账号操作。
