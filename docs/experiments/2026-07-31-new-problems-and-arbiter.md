## 2026-07-31 新问题 runs 合并与结果（2 题 × 2 系统 general_prompt）

Pool Server 对 2 个新问题（imo_2019_p1、numbertheory_x5neqy2p4）的 general_prompt run
已完成并合并：`runs/isabellegym/general_prompt/`（mathd 10 + imo 10 + numbertheory 10，
30 rows / 30 artifacts / 30 logs，路径已修正指向子目录）。I/Q 侧同构（合并历史见
2026-07-30 节尾：imo rep6–9 为 jEdit 挂起+token 失效的 0 轮坏尝试，已用重跑的
rep1–4 替换并备份于内部工作笔记，未纳入版本库）。

### 结果总表（general_prompt，各 10 reps，DeepSeek-v4-pro @0.3）

| 题目 | 系统 | pass@1 | 平均轮数(解出) | 平均 wall_s | 平均 tokens | sh 使用尝试 |
|---|---|---|---|---|---|---|
| imo_2019_p1 | Pool Server | 7/10 | 52.7 | 820 | 2.12M | 2/10 |
| imo_2019_p1 | I/Q | 9/10 | 37.4 | 854 | 9.02M | — |
| numbertheory_x5neqy2p4 | Pool Server | 9/10 | 31.1 | 377 | 0.42M | 2/10 |
| numbertheory_x5neqy2p4 | I/Q | 9/10 | 20.3 | 420 | 1.20M | — |

### 发现

1. **imo_2019_p1 明显更难**：Pool Server 3 个失败（rep5/rep9 撞 100 轮上限、rep7 撞 2400s
   wall 上限，均为如实记录的真磨失败）；I/Q 9/10。Pool Server 轮数仍高于 I/Q（52.7 vs 37.4），
   但 tokens 便宜 4 倍（2.12M vs 9.02M——I/Q 整文件重写在难题上代价急剧放大）。
2. **numbertheory_x5neqy2p4 较易**：两边 9/10；Pool Server 轮数 31.1 vs I/Q 20.3；tokens
   Pool Server 0.42M vs I/Q 1.20M。
3. **Pool Server sledgehammer 使用率低**（两题各仅 2/10 尝试调用）：escalation 规则
   存在但模型在简单题上不需要、难题上倾向手工硬磨——后续分析时值得按题难度分层看。
4. 失败模式注意（Pool Server numbertheory rep7）：模型把目标定理**整个删掉了**（最终文件
   只剩 theory 头，113 字节），arbiter 报 "target theorem not found"——不是证明失败，
   是文件被破坏，属模型行为事故，如实计入未解出。

---

## 2026-07-31 补记：I/Q sledgehammer 使用数据 + arbiter 问题（暂停）

### I/Q sledgehammer 使用（补 2026-07-31 表中 "—"）

上节表格的 I/Q sh 列留了 "—"，是我当时只对 Pool Server 跑了 sh 计数、漏算了 I/Q，
并非 I/Q 没有使用。实际数据（log 中 `explore(query="sledgehammer")` 计数）：

| 题目 | 系统 | 用过 sh 的尝试 | 明细（rep → 次数） |
|---|---|---|---|
| imo_2019_p1 | I/Q | **0/10** | 全 0——但伴随大量直接 metis 违规（rep3: 21×、rep0: 20×、rep9: 13×、rep1: 8×、rep5: 7×） |
| numbertheory_x5neqy2p4 | I/Q | **3/10** | rep5: 1, rep6: 1, rep7: 2；另有 rep2 用 `smt (verit)` 5×、rep0 6× metis 违规 |

注：Pool Server 同题为 2/10 + 2/10。I/Q imo 零调用的原因：直接 metis 在数论/代数题
上随手可得 + explore(sledgehammer) 历史上有 30s 超时名声 + solver rule 仅 prompt 层
无强制——即"轮数优势部分来自违规捷径"，分析时应把 solver-rule 合规度作为独立维度。

### arbiter 问题（暂停中，2026-07-31）

**现象**：I/Q numbertheory rep7 代理证明正确（mod-11 枚举矛盾，46 行无 sorry），
arbiter 报 "Cannot load theory HOL-Number_Theory.Cong"。

**已查明**：
1. 容器内已补建 `HOL-Number_Theory` heap（36s 完成）——但报错的根因不是 heap：
   代理在模板导入之外**自行新增了 `HOL-Number_Theory.Cong` 导入**，而 arbiter
   （`common/arbiter.py`）只从模板导入推导 parent session（→ HOL-Computational_Algebra），
   生成的 ROOT 缺少 HOL-Number_Theory，于是 "need to include sessions … in ROOT"。
2. `server/app/services/build_verify.py:write_root` 目前**只支持单 parent**。
   多 parent ROOT 手工验证时遇到 "bad input line 1"（未查清是语法还是引号/编码问题）；
   改单 parent `HOL-Number_Theory` 后 Cong 能加载了，但在 theorem 第 8 行出现新的
   "Failed to parse prop"（未调查）。

**待定**：arbiter 应从**最终文件**的导入推导全部 parent session（多 parent ROOT），
并修正多 parent ROOT 生成；rep7 行暂未改判（保持 solved=False），待 arbiter 修复后重判。
