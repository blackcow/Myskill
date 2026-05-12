# 公开网页归档测试用例

本目录保存 `01_公开网页Markdown归档方案` 的真实测试输出，来自前一轮对 Obsidian Clipper/Defuddle 思路借鉴后的回归测试。

## 输出位置

- `outputs/`：每个子目录是一条网页归档结果。
- 每条结果通常包含 `README.md`、`article.md`，下载图片用例还包含 `assets/`。

## 用例清单

| 用例目录 | 来源 | 验证点 |
| --- | --- | --- |
| `python-argparse` | `https://docs.python.org/3/library/argparse.html` | 长技术文档、元数据、正文完整性。 |
| `rfc9110` | `https://www.rfc-editor.org/rfc/rfc9110.html` | 超长规范文档、DOM 扩展兜底、partial extraction warning。 |
| `claude-prompt-caching` | `https://claude.com/blog/lessons-from-building-claude-code-prompt-caching-is-everything` | 博客正文抽取、去除分享/复制等噪声。 |
| `claude-managed-agents-images` | `https://claude.com/blog/new-in-claude-managed-agents` | 图片下载、`assets/` 本地化、Markdown 图片链接重写。 |
| `obsidian-help-variables-rendered` | `https://help.obsidian.md/web-clipper/variables` | `--render-js` 渲染后抽取动态页面。 |

## 回归检查

重点检查 `article.md` 的正文长度、frontmatter、`extraction_method`、`asset_count` 和 `warnings`。如果新脚本输出明显更短或混入导航/页脚，优先看对应目录的 `README.md` 和 `article.md` 差异。
