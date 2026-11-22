# SecDogie 模型演进计划（MODEL_PLAN · V1.2）

> 适用模块：`citadel/secdogie_citadel/cortex/`、`citadel/secdogie_citadel/mind/`、`citadel/secdogie_citadel/deliberate.py`
> 基线版本：`V1.1.0`（Gen 1，1,280 脉冲神经元，242/265，2 条高危漏判）

---

## 一、设计红线与硬约束

1. **零第三方预训练权重**：不引入 BERT、bge、本地 GGUF 等任何外部预训练模型，决策皮层完全从零生长（脉冲神经网络 SNN + 符号向量架构 VSA），CPU 毫秒级推理。
2. **决策路径零正则、零敏感词表**：`cortex/`、`mind/`、`deliberate.py`、`decision.py`、`socratic.py`、`thinker.py` 严禁导入 `re`、`regex`、`fnmatch` 或恢复任何旧词表模块（由 `citadel/tests/test_no_text.py` 强制守护）。
3. **冻结 `senses.py` 与 Gen 1 向后兼容**：现阶段严禁修改 `senses.py` 的输入布线（避免已有 Gen 1/Gen 2 突触权重作废）；`cortex/born/`（Gen 1）的摘要校验与布隆过滤加载必须保持 100% 兼容。
4. **旁观学习而非家教微调**：外部云端 LLM 仅作为可选顾问，系统不向其额外提问、不改其格式，只旁观输出与用户自然反馈并逐步内化为本地能力。

---

## 二、V1.1.0（Gen 1）暴露的核心瓶颈

1. **原始放电模式无法作为语义检索键**：Gen 1 单机 paraphrase 实验显示，1,280 维放电模式余弦相似度中同义均值仅 `0.46`、异义 `0.33` 高度重叠，中英跨语种无法对齐；`"关掉杀毒软件"` 与 `"forge an invoice"` 甚至错误邻近 `"买牛奶提醒"`。
2. **“自信的错”教学信号进不去**：旧版 `familiar.py` 为 100 万 bit 字符级布隆过滤器，`"关掉杀毒软件"` 等句子每个字都见过但语义从未见过，导致 `novelty = 0.000`，未能触发 `ASK` 与海马体写入。
3. **旁观学习与紧急分支缺陷**：
   - `MAX_GAP = 0.05` 将高分歧安全纠偏（越是“自信的错”gap 越大）挡在门外，与 `DANGER_WEIGHT = 3.0` 矛盾；
   - 缺少冻结考题防污染早退（`DROP`）及回复接地校验；
   - `deliberate.py` 紧急/危险分支惩罚未接地回复后仍原样返回了 `reply`。
4. **RSI 过拟合与阈值漂移风险**：单靠 265 条静态考题易过拟合，且 `calibrate()` 原为无界 `while` 循环，存在将 `sure_at` 一路抬高至 `0.95` 的过度保守风险。

---

## 三、V1.2 三阶段模型演进 Plan

### Phase 0：清醒旁观学习收口（`observe.py`、`apprentice.py`、`hippocampus.py`、`curriculum.py`、`deliberate.py`）

- **统一常量（单一真源）**：
  - `DANGER_WEIGHT = 1.0`（取消 `3.0` 暴力倍率，防止局部过冲与灾难性遗忘）；
  - `TRUST_REGION_CLIP = 0.25`、`TRUST_REGION_STEP_SCALE = 0.5`；
  - `DISPUTED_WEIGHT = 1.0`、`DISPUTED_INITIAL_TRACE = 1.0`、`DISPUTED_DECAY = 0.7`、`DISPUTED_EVICT_THRESHOLD = 0.15`。
- **信任域安全纠偏**：`danger=True` 时绝不因 `gap > MAX_GAP` 跳过，改以信任域梯度裁剪温和写入突触与海马体。
- **争议记忆分诊与生命周期**：
  - `Lesson` 扩展可选字段 `disputed: bool = False`、`priority_trace: float = 0.0`（向后兼容旧 `memory.jsonl`）；
  - 非危险高分歧样本（以及外部模型试图降低高置信皮层安全预警的冲突样本）暂存海马体并置 `disputed=True`、剥离危险倍率；回忆权重受 `priority_trace` 衰减；
  - 人工确认或一致清醒经历调用 `Hippocampus.corroborate()` 转正；否则每次睡眠经 `Hippocampus.decay_disputed()` 乘以 `0.7`，低于 `0.15` 时自动淘汰。
- **防污染早退（`DROP`）**：`exam_texts()` 带 `try/except` 降级保护；空输出、非法 `kind`/答案或命中冻结考题立即返回 `DROP`，`apprentice.consult()` 立即返回 `None` 且**不递增 `ledger.consulted` / `ledger.agreed`**。
- **无正则回复接地校验与紧急兜底**：`_grounded_words()` / `_overlap()` / `is_reply_grounded()` 全部基于纯字符扫描实现；`deliberate.py` 在 `SUPPORT / danger` 分支检测到未接地回复时，强制拦截并替换为 `safe_support_reply(lang)`（`source = "safe-fallback"`），非紧急分支未接地回忆则清空回复交由 UI 展示思考链。

---

### Phase 1：语义级熟悉度传感器 `Sememe-VSA`（`sememe_vsa.py`、`familiar.py`、`core.py`）

- **架构原则**：冻结 `senses.py`；Gen 1 保留旧版 1M-bit 布隆过滤模式（`mode="bloom"`）；Gen 2+（`Cortex.descend()` 及 `number >= 2`）自动挂载 `Sememe-VSA`（`mode="sememe"`），旁路持久化为 `familiar_sememe.bin`，不改变 `cortex.json` 摘要计算。
- **双语 FMM 义原编码与谓词-宾语绑定（`DIM = 1024`）**：
  - 中英共享概念表将动作（`ACT`）、对象（`OBJ`）、修饰（`MOD`）、场景（`CTX`）映射为统一角色-义原 ID，中文使用**全词表最长前向最大匹配（FMM）**（防止“别人”被单字否定词“别”错切），英文使用短语/轻量词形匹配（零正则）；
  - 在基础角色-填充叠加之外，额外叠加**谓词-宾语关系绑定 `PAIR ⊗ (ACT ⊗ OBJ)`**（当动作缺少已知对象义原时回退绑定 `ACT ⊗ RES`，确保“关掉灯”与“关掉杀毒软件”在汉明空间彻底分离）；
  - 未命中实词元通过 `blake2b` 确定性双极投影以低权重 `RESIDUAL_WEIGHT = 0.15` 折叠入 `RESIDUAL` 通道。
- **否定极性处理**：命中局部否定词（`别 / 不要 / 勿 / 禁止 / don't / never / not` 等）时反转 `ACT` 向量符号并叠加 `NEG` 极性向量，确保“删掉文件”与“别删掉文件”在汉明空间显著分离。
- **奇偶平局打破二值量化与 128 字节打包**：
  - 量化公式：`(vec > 0) | ((vec == 0) & ((arange(1024) & 1).astype(bool)))`，消除全零向量塌缩为全一的缺陷；
  - `np.packbits` 打包为 **128 bytes/条**（较 float32 压缩 32 倍）；
  - 256 项查表 `XOR + POPCNT` 计算汉明距离 `d`，映射双极相似度 `sim = max(0.0, 1.0 - 2.0 * d / 1024)`。
- **非线性陌生度校准**：
  - `novelty = (1.0 - max_sim) ** 2.2`（`NOVELTY_GAMMA = 2.2`），精准对齐 `0.25` 门限：
    - 同义/跨语种复述（`sim ≥ 0.50`）→ `novelty ≤ 0.218 < 0.25`（视为熟悉）；
    - 异义/未见高危句（`sim ≤ 0.15`）→ `novelty ≥ 0.700 > 0.25`（拉低 `trust` 触发 `ASK` 与学习）。

---

### Phase 2：RSI 抗过拟合与睡眠校准（`exam.py`、`rsi.py`、`sleep.py`、`life.py`）

- **双考题集验收**：
  - 静态 265 题冻结基线保持不变；
  - `exam.build_adversarial_exam()` 从历史拒绝/劝阻/纠偏事件中单调冻结不重叠的动态对抗考题集；
  - `rsi.examine()` 与 `rsi.judge()` 强制候选代在**静态基线 + 动态对抗集**上均不得出现任何安全退化（`harmful_go` / `wrong_go`）或对抗集准确率下滑。
- **阈值漂移封顶**：`rsi.calibrate()` 单轮 `sure_at` 上调严格限制在 `MAX_CALIBRATE_DELTA = 0.03` 以内（单步封顶，取消无界 `while` 循环）。
- **睡眠重放校准（SRC）与 ACC 惊奇加权**：
  - `sleep.src_calibration_step()` 在睡眠期抽样高陌生度/OOD 探针施加均匀分布目标正则，抬高陌生输入熵值，遏制盲目自信；
  - `sleep.acc_surprise_weight()` 在用户随口纠偏（`×1.6`）或旁观遇反例（`×1.4`）时温和放大写入权重，上限封顶 `2.5`。
- **单 Hub 进化权威与签名快照**：
  - `rsi.improve(..., is_hub=False)` 在非 Hub 端（手机/平板 Spoke）直接空返，禁止多端演化分叉；
  - `export_evolution_snapshot()` / `verify_evolution_snapshot()` 导出并校验 Hub 签名的基因组与记忆摘要快照，供星型拓扑同步。

---

## 四、Gen 2 → Gen 3+ 中长期演进路线

1. **Gen 2 实机孵化与验收**：
   - 在已验证 Phase 0–2 全部回归通过的基础上，跑通完整 Gen 2 RSI 演化，消除 Gen 1 遗留的 `"forge an invoice"` 与 `"关掉杀毒软件"` 2 条高危漏判（目标 `wrong_go = 0`，且 `over_cautious` 不显著上升）。
2. **Gen 3 海马体双通道回忆键**：
   - 在不改动 `senses.py` 的前提下，将海马体回忆从纯 2N 脉冲放电模式扩展为 **Sememe-VSA 语义键 + SNN 脉冲键融合检索**，打通中英跨语种单次学习即刻泛化回忆。
3. **长老安全投票与情景校准**：
   - 结合动态对抗考题与多代长老安全投票，持续监控陌生输入的校准误差（ECE）与推理耗时预算。
