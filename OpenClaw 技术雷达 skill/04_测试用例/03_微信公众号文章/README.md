# 微信公众号文章测试用例

本目录保存 `02_微信公众号文章最小归档方案` 的测试输入和输出。

## 输出位置

成功用例写入：

```text
outputs/
└── yyyy-MM-dd-文章标题/
    ├── README.md
    ├── article.structured.local.md
    └── assets/
```

## 本轮测试用例

| 编号 | URL | 标题 | 抓取方式 | 图片源数 | 本地资源数 | 正文字符数 | 结果 |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| `wechat-01` | `https://mp.weixin.qq.com/s/NmYcxPkGABkwYzG2WwZrSA` | 别再把长文切碎了，HiLight让AI直接在原文里划重点 | `desktop-chrome` | 11 | 10 | 3709 | 成功；尾部授权/投稿信息已清理。 |
| `wechat-02` | `https://mp.weixin.qq.com/s/hYMj375l9Y29kOhehuOkZQ` | 一年后Claude不需要Harness工程了？产品和工程负责人爆料：搭建Agent的最终难关是基础设施壁垒；Harness和模型正高度配对 | `desktop-chrome` | 3 | 2 | 12843 | 成功；尾部“好文推荐”块已清理。 |
| `wechat-03` | `https://mp.weixin.qq.com/s/xzDlmVYH_PXDSkrOW1iX1Q` | GraphRAG的断臂，被OKH-RAG攻克了，让AI读懂因果链条 | `desktop-chrome` | 5 | 5 | 3004 | 成功；正文和图片均完整保留。 |

## 本轮发现和修复

初次测试时，`wechat-02` 的尾部保留了“——好文推荐——”和推荐文章标题，`wechat-01` 的尾部保留了“© THE END / 转载 / 投稿”等运营信息。这些内容不适合作为长期知识库主源。

已在脚本中补充保守清理规则：

- 只在文档后半段识别运营尾巴，避免误删正文。
- 遇到“好文推荐”“相关推荐”“© THE END”“转载请联系”“投稿或寻求报道”等标记时，截断其后的内容。
- 尾部截断后自动删除正文不再引用的本地资源。

## 验收结果

三条用例重跑后的结果：

- 每个目录均包含 `README.md`、`article.structured.local.md`、`assets/`。
- `article.structured.local.md` 中 `https://mmbiz.qpic.cn` 远程图片残留数为 `0`。
- `<iframe>` 残留数为 `0`。
- `<script>` 残留数为 `0`。
- 本地 `assets/` 文件数与正文 Markdown 图片引用数一致。
- 未发现验证码、登录页、环境异常页被误当正文。

## 后续补充用例

| 类型 | 验证点 |
| --- | --- |
| 带视频文章 | 视频 iframe 替换为本地封面和原视频链接。 |
| 图片特别多的文章 | 大量图片下载、顺序和失败告警。 |
| 抓取失败文章 | 反爬/验证码/登录限制时是否明确失败，不生成误导性正文。 |
