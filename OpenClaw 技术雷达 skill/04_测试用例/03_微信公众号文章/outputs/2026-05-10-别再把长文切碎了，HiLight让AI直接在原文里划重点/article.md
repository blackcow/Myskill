---
source_type: "wechat_article"
source_url: "https://mp.weixin.qq.com/s/NmYcxPkGABkwYzG2WwZrSA"
title: "别再把长文切碎了，HiLight让AI直接在原文里划重点"
published: "2026-05-10 10:55:07 +08:00"
wechat_account_id: "gh_dbc0a5474692"
captured_at: "2026-05-12T14:17:54+08:00"
fetch_method: "desktop-chrome"
text_source: "#js_content with content_noencode fallback"
image_count: 11
video_count: 0
asset_count: 10
content_chars: 3445
content_sha256: "323bf0f725c17123da1edb31c43970bf714ac7574af8db6e27f0f4e9c9a4f042"
status: "raw"
---

# 别再把长文切碎了，HiLight让AI直接在原文里划重点

![图片](assets/image-01.png)

在实际应用中，模型常常会忽略关键线索，这就是 “Lost in the Middle” 现象，即模型对出现在输入中间位置的信息关注度明显下降。现有的优化思路大致分为两类：

硬选择：先检索或裁剪出相关片段，再送入模型，但可能会丢失对推理至关重要的上下文。

软选择：通过摘要或压缩来缩短输入，但有损压缩难免引入失真。

两类方法都在 “动” 原始输入或原始权重。那么，能不能既保留完整的上下文，又能准确地告诉模型 “重点看哪里”？HiLight 提出一条 “输入侧干预” 的新路径：在原文中插入少量高亮标签，引导模型的注意力。

方法概述

在实际部署当中，大模型往往是 API 付费调用、规模巨大，甚至权重不开放的黑盒服务，直接对它做 SFT 或 RL 微调往往不现实。因此，HiLight 选择了一条更实用的路径：冻结推理模型，训练一个轻量的 “助手模型” 来帮助它划重点。

![图片](assets/image-02.png)

论文标题：Learning Evidence Highlighting for Frozen LLMs

论文地址：https://arxiv.org/abs/2604.22565

作者：Shaoang Li1,∗, Yanhang Shi1,∗, Yufei Li2, Mingfu Liang2, Xiaohan Wei2, Yunchen Pu2, Fei Tian2, Chonglin Sun2, Frank Shyu2, Luke Simon2, Sandeep Pandey2, Xi Liu2,†, Jian Li1,†

机构：1 石溪大学（Stony Brook University），2 Meta AI

说明：∗ 共同第一作者；† 共同通讯作者

流程如下：

1. 轻量模型（Emphasis Actor）阅读完整的上下文，为每个 token 打出重要性分数。

2. 轻量模型在得分最高的片段两边插入高亮标签，如 < start_important > 和 < end_important>。

3. 冻结的推理模型（Solver LLM）接收带标签的文本，完成推理并输出结果。

![图片](assets/image-03.png)

该训练过程只用 Solver 的任务奖励作为反馈信号，不需要任何人工标注的证据。在训练方式上，因为没有 token 级别的证据标注，研究者将高亮选择建模为强化学习问题，用下游任务指标（如 HR@10、EM、F1）作为奖励信号，通过分组策略梯度来更新 Actor。

为了防止 Actor “全部高亮” 的偷懒行为，该框架还引入了高亮预算机制：轻量语言模型最多只能标注一定比例的 token，并通过 span 合并策略将零散的 token 级选择合并为语义连贯的片段。

![图片](assets/image-04.png)

实验表明，HiLight 对预算取值并不敏感。这意味着，在实际部署时无需精细调参，选取一个合理的中间值即可。

实验结果

研究者在四个任务上进行了评测：Amazon-Beauty（序列推荐）、HotpotQA（多跳问答）、SQuAD 2.0（阅读理解）和 PubMedQA（生物医学分类）。对比方法涵盖了当前主流的 prompt optimization 方法，包括 PRL、BFRS、OPRO、DSPy（MIPROv2）和 APE。

![图片](assets/image-05.png)

提升幅度最大的是序列推荐（Amazon-Beauty），在其它任务上，虽然提升相对温和，但依然一致正向。

高亮＞裁剪，保留上下文的优势

消融实验做了一个有趣的对比：把 Actor 选择的高亮片段单独裁剪出来喂给 Solver，会怎么样？

![图片](assets/image-06.png)

结果显示，在 Amazon-Beauty 上，裁剪也能取得不错的效果。但在 HotpotQA 上出现了相反的情况。因为多跳问答推理需要保留连接性的上下文，裁剪虽然能选出关键证据，却破坏了语义的完整性。而 HiLight 在标注重点的同时保留了完整语境。

一个高亮模型，服务多个大模型

Actor 学习到的高亮策略具有很强的迁移能力。研究者用 Qwen3-14B 作为 Solver 训练 Actor，直接将其应用到五个从未见过的 Solver 上。与之相对比的做法是让目标 Solver 自己先高亮证据再作答。

![图片](assets/image-07.png)

结果显示，HiLight 的 Actor 高亮在五个 Solver 上的效果都明显优于自我高亮。原因也很简单，专门训练的轻量模型，比大模型自己猜 “哪里重要” 更靠谱。HiLight 的 Actor 是通过任务奖励显式训练出来的，知道什么样的证据能真正提升下游指标。

没有人工标注，却与人工高度重合

尽管训练过程中没有任何 token 级别的证据标注，但 Actor 的高亮区域与 HotpotQA 数据集中人工标注的支持事实高度重合，最高达到 0.78 F1。随着 Actor 规模从 0.6B 增大到 8B，F1 从 0.68 单调上升到 0.78。

![图片](assets/image-08.png)

如图所示，Precision、Recall、F1 三项指标都随 Actor 规模单调提升，Precision 甚至达到 0.84，说明 Actor 高亮的 token 中，绝大多数都是人工判定的关键证据。

![图片](assets/image-09.png)

上图展示了一个 HotpotQA 样本上的 token 级分数分布：蓝色曲线是 Actor 打出的重要性分数，红色阴影区是人工标注的支持事实所在区间。在一个包含 1200 多个 token 的长上下文中，Actor 只在两个狭窄的区域打出高分，而这两个区域正是数据集标注的 ground-truth 证据所在。

低部署成本

Solver 端 token 开销：< 1.01 倍（仅插入少量标签 token）。

Actor 推理延迟：0.6B 模型约 0.05 秒，4B 模型约 0.23 秒（p50），相比 Solver 的 8 至 18 秒可忽略不计。

训练成本：仅需约 12K 次 Solver 调用，而 PRL 需要 120K 次，APE 需要 60K 次。

一个直观案例：序列推荐优化

在 Amazon-Beauty 的一个典型案例中，模型需要通过给定的用户历史购买摘要和一批候选商品，依据用户下一个可能感兴趣的商品，对候选商品进行重排序。Actor 精准地高亮标记了两个关键内容。这两个信号帮助 Solver 将真实目标商品（一款主打 “Grips Makeup To Last” 的底妆产品）的推荐排序从第 14 名提升到第 5 名，是一个显著的排序改进。

![图片](assets/image-10.png)

与黑盒注意力机制不同，HiLight 直接告诉用户：模型之所以提升该商品的排名，是因为看到了这两段高亮文本。这大大提升了模型推荐结果的可信度。

结语

HiLight 的思路非常简单，用一个轻量模型划重点，让大模型集中精力推理。这种方式带来了几个好处：

性能提升：推荐任务性能提升可达 27%，问答任务也正向提升。

不用改模型：Solver 冻结，API 友好。

可解释：高亮标签能够直接告诉人类 “模型在看哪里”，以及模型决策的依据。

可迁移：一个 Actor 可以服务于多个不同的大模型。

低成本：训练成本低，额外延迟和推理成本小。

随着越来越多系统通过 API 调用大模型，HiLight 提供了一种不必改动 Solver 也能实现性能提升的办法。

值得一提的是，本文作者名单与 Meta 的 GR2（Generative Reasoning Re-ranker，arXiv:2602.07774）团队有相当程度的重叠，HiLight 这套做法很可能在不远的将来被用进 GR2 这样的生产级 re-ranking 系统里。

