# 苏格拉底 Gate 1 意图契约 + 阶段式记忆 设计 v1

> 状态：v1 已确认（2026-09-27）。M1–M5、M7 已实现（`action_gate.py` / `episodes.py` / `lessons.py` / `consolidate.py`，接入真实循环见 `loop_gate.py` / `loop_memory.py` / `supervisor.py`）；M6 的追问部分由 `dialogue/agent_bridge.py`（Wave C3）完成：ask_user 成为苏格拉底追问，破坏性动作成为 Gate 2 挑战；记忆确认经 `ControlPacket.confirm_memory` 送达，节点端处理在 Wave D 接上。实现时的修订见文末“附录：实现修订”。

## 0. 定位与红线

记忆让 Agent 从过去学到东西：哪一步屡次失败、操作员澄清过什么、某个控件在哪。双重苏格拉底门则负责在每一步之前追问。两者结合起来，就是**带着记忆去追问**。

四条红线：
1. **记忆只能让门更谨慎，不能更宽松。** 与“没有记忆”的基线相比，有记忆时门的判决只能相同或更严（allow → rewrite / reject / reobserve），不存在反方向。这条用性质测试强制。
2. **Gate 2 永不读记忆。** “上次操作员批准过删这个文件”不构成这次的授权。破坏性动作每次都要一枚新的、绑定该动作哈希的操作员签名。HITL 不会因为记忆变成自动批准。
3. **记忆不存原始观测与秘密。** 不存截图、AX 树全文、DIB 内容，只存提炼后的教训，以及对 run / step 的引用。每一层入口都复用 `looks_like_secret` 过滤，命中即拒。
4. **来源可追溯、可撤回、随撤销失效。** 每条长期记忆都记着作者 DID、依据（证据 run 或操作员确认）。它是签名日志事件，所以被撤销作者写的记忆会被 `TrustPolicy` 自动拒收；操作员也可以显式撤回某条记忆。

---

## 一、阶段模型（阶段式存放）

```
 S0 工作记忆 ──► S1 情景记忆 ──► S2 候选记忆 ──► S3 巩固记忆
 进程内/易失      日志的投影        本地隔离区        签名日志事件
 (本次 run)       (已签名、已复制)   (不复制、不可信)  (复制到全网、可撤回)
                                  ▲ 模型 remember        │
                                  │ 从情景中提炼          ▼
                                  └──── 记忆的苏格拉底门（晋升审查）
```

| 阶段 | 内容 | 存放 | 寿命 | 信任 | 谁读 |
| --- | --- | --- | --- | --- | --- |
| S0 工作 | 本次 run 的近期动作、观测代际、未决追问 | 进程内 | run 结束即弃 | 本次运行内有效 | 门（`recent_actions` 等） |
| S1 情景 | 每个 run 的每一步：动作 key、门判决、结果 | **Citadel 日志的投影**（不复制数据，只是视图） | 同日志 | 已签名、防篡改 | 提炼器 |
| S2 候选 | 从情景中提炼出的教训；模型 `remember` 写入的笔记 | 本节点本地 SQLite，**不复制** | TTL（默认 30 天无新证据即过期） | **不可信、隔离** | 晋升门、Dialogue App（待确认列表） |
| S3 巩固 | 通过晋升审查的记忆 | Citadel 日志 `memory` 事件（签名、全网复制） | 直到被撤回 | 可信（有依据、可追溯） | 门（只能加谨慎）、规划提示词 |

每条记忆都带 `scope`：`global` / `app:<bundle>` / `goal:<id>`。所以记忆也可以按任务阶段（目标、子目标）隔离存放，不会串到无关任务里。

---

## 二、记忆的苏格拉底门（S2 → S3 晋升审查）

晋升的门槛与“这条记忆能做什么”成正比：

| 记忆类 | 例子 | 能做什么 | 晋升条件 |
| --- | --- | --- | --- |
| **CAUTION 警示** | “点这个按钮在 3 个 run 里都没反应” | 只能让门更严 | **证据自动晋升**：≥ `min_runs` 个不同 run 出现失败或被拒，且最后一次失败之后没有成功 |
| **FACT 事实** | “这个应用里的‘保存’指工具栏那个” | 会影响规划 | **必须操作员确认**（Dialogue App 追问：“记住这条吗？”） |
| **PREFERENCE 偏好** | “导出默认用 PDF” | 会影响规划 | **必须操作员确认** |

- 任何类别命中秘密过滤，直接丢弃，永不晋升。
- CAUTION 的撤回也看证据：同一动作 key 此后在 ≥ `min_runs` 个 run 里成功，就写 `retract`。撤回只是回到基线，不会低于基线，与红线 1 一致。
- 模型通过 `remember` 写入的一律进 S2，`source="model"`。它可能转述屏幕上其他应用的文字，等于一个持久化提示注入的入口，所以只有经过操作员确认才能进入 S3。

---

## 三、Gate 1 意图契约（扩展 `action_gate.py`）

动作执行前，门要能回答四个问题：为什么做、预期看到什么、失败了怎么退、前提现在还成立吗。“预期看到什么”已经由 `expected_observation` 覆盖，这里补上其余三问，并接入记忆。

```python
@dataclass(frozen=True)
class IntentContract:
    purpose: str = ""                       # 服务于哪个目标（goal id）/ 为什么做这一步
    rollback: str = ""                      # 失败时如何回退；空 = 未声明
    irreversible: bool = False              # 明确承认不可逆（此时仍必须过 Gate 2）
    requires_present: tuple[str, ...] = ()  # 执行前必须仍在当前观测中的 target id（门可核验）

# PlannedAction 新增（不进 action_hash：它描述理由，不改变效果）
    intent: IntentContract = IntentContract()

# GateContext 新增（全部默认关闭 / 为空，旧调用方行为不变）
    require_intent: bool = False
    active_goal_ids: frozenset[str] = frozenset()   # 目标树里仍活跃的目标
    known_failures: frozenset[str] = frozenset()    # 来自 S3 CAUTION：屡败动作的 action_key
```

新增 finding（均为纯函数检查，登记进 `_CHECKS`）：

| finding | 判决 | 触发条件 |
| --- | --- | --- |
| `intent-unproven` | REJECT → 发起追问 | `require_intent` 开启时：可变动作没有 `purpose`；或破坏性动作既没有 `rollback` 也没声明 `irreversible` |
| `intent-contradiction` | REJECT | 声明了 `irreversible` 却又给出 `rollback`；或 `purpose` 指向一个不在 `active_goal_ids` 里的目标（在为已完成或已删除的目标做事） |
| `precondition-failed` | REQUEST_REOBSERVE | `requires_present` 里有目标不在 `target_present_ids` 中（仅当已知当前观测时判定） |
| `known-failure` | REJECT | `authz.action_hash(action)` ∈ `known_failures`，提示“先修原因，别重试” |

- `action_key` 直接复用 `authz.action_hash`：它只看影响效果的字段，稳定，与 Gate 2 用的是同一个 key。
- 契约缺失时不会自动补一个 rollback（门不能替 Agent 编造回退路径），所以判 REJECT。随后由 Agent 通过 Dialogue 通道发起 `SocraticQuestion(gate_finding="intent-unproven")`；操作员的澄清补全契约后，Agent 重新提交动作，门再判一次。**澄清永远不会直接把判决改成 allow。**

---

## 四、数据结构（S1 / S2 / S3）

```python
# ---- S1 情景：日志投影（episodes.py，同 goals.py 的纯折叠）----
# step 事件的 payload 追加三个字段（只是追加，state_hash 链的输入不变）：
#   action_key: str   = authz.action_hash(planned)
#   outcome: str      = "ok" | "failed" | "no_change" | "rejected" | "unknown"
#   findings: list[str]  门给出的 finding 种类

@dataclass(frozen=True)
class StepRecord:
    run_id: str
    seq: int
    action_key: str
    verdict: str
    findings: tuple[str, ...]
    outcome: str

@dataclass(frozen=True)
class Episode:
    run_id: str
    goal_id: str
    author: str                    # 记录这个 run 的节点 DID
    state: str                     # run 生命周期状态
    code: int | None
    steps: tuple[StepRecord, ...]

def build_episodes(events) -> dict[str, Episode]: ...

# ---- S2 候选：本地隔离区（lessons.py，SQLite，不复制）----
class MemoryClass(str, Enum):
    CAUTION = "caution"
    FACT = "fact"
    PREFERENCE = "preference"

@dataclass(frozen=True)
class Candidate:
    candidate_id: str              # sha256(canonical({mclass, scope, key, value}))
    mclass: MemoryClass
    scope: str
    key: str                       # CAUTION: action_key；FACT/PREF：稳定名
    value: str                     # 人读描述（已去秘、截断）
    source: str                    # "consolidation" | "model" | "operator"
    evidence: tuple[str, ...]      # 支持它的 run_id（去重）
    contradictions: tuple[str, ...]  # 反证的 run_id
    first_seen: float
    last_seen: float

def extract_cautions(episodes, *, scope="global") -> list[Candidate]: ...  # 按 action_key 聚合失败/被拒

# ---- S3 巩固：日志 memory 事件 + 投影（consolidate.py）----
@dataclass(frozen=True)
class MemoryRecord:                # journal 事件体，kind="memory"
    op: str                        # "assert" | "retract"
    memory_id: str                 # = candidate_id
    mclass: MemoryClass
    scope: str
    key: str
    value: str
    basis: str                     # "evidence" | "operator"
    evidence: tuple[str, ...]      # run_id
    confirmed_by: str = ""         # 操作员确认时：澄清包签名者 DID
    confirmation_ref: str = ""     # 对应的 probe_id

@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    basis: str                     # "evidence" | "operator" | ""
    needs_operator: bool
    reason: str

def review_candidate(c: Candidate, *, min_runs: int = 3) -> PromotionDecision: ...

@dataclass(frozen=True)
class MemoryView:
    known_failures: frozenset[str]                     # → GateContext.known_failures
    facts: dict[tuple[str, str], MemoryRecord]         # (scope, key) → 已确认的事实 / 偏好

def build_memory(events, *, scope: str | None = None) -> MemoryView: ...
```

---

## 五、数据流闭环

```
 run 的每一步 ──► RunRecorder.record_step（追加 action_key / outcome / findings）
                         │ 签名日志
                         ▼
                 S1 build_episodes（投影）
                         │
                         ▼
                 S2 extract_cautions / 模型 remember ──► 本地隔离区
                         │
                         ▼
                 记忆的苏格拉底门 review_candidate
                  ├─ CAUTION 证据足够 ──────────────► journal "memory" assert（节点签名）
                  └─ FACT / PREF ──► Dialogue 追问 ──► 操作员澄清 ──► assert（confirmed_by）
                                                                       │ 全网复制
                                                                       ▼
                 S3 build_memory ──► MemoryView
                  ├─ known_failures ──► GateContext ──► Gate 1 只加谨慎
                  └─ facts ──────────► 规划提示词（仅已确认）
                                        Gate 2：不读记忆
```

---

## 六、不变量与测试

- **单调谨慎（性质测试）**：对大量生成的 (action, ctx, memory) 组合，`precedence(gate(a, ctx+memory)) ≥ precedence(gate(a, ctx))`。
- **Gate 2 隔离**：记忆字段取任何值，`_check_authorization` 的结果都不变。
- **未确认不外泄**：S2 的任何内容都不会出现在 `MemoryView` 或提示词渲染里；FACT/PREF 没有 `confirmed_by` 就不能 assert。
- **撤销生效**：被撤销作者签的 `memory` 事件不进投影（日志层 `TrustPolicy`）；`retract` 之后门回到基线。
- **去秘**：每个入口（`remember`、提炼、assert）都拒绝秘密。
- 每片都做变异测试，变体必须快速失败，不能靠超时。

---

## 七、实现切片

| 片 | 内容 | 依赖 |
| --- | --- | --- |
| **M1** | Gate 1 意图契约：`IntentContract` + 4 个新检查（默认关闭） | — |
| **M2** | S1：step payload 追加 `action_key` / `outcome` / `findings`；`episodes.py` 折叠 | — |
| **M3** | S2：`lessons.py` 本地隔离区 + `extract_cautions` + 去秘 + TTL | M2 |
| **M4** | S3：`memory` 日志事件 + `review_candidate` 晋升门 + `build_memory` 投影 + 撤回 | M3 |
| **M5** | 接线：`MemoryView.known_failures` → Supervisor / loop_gate 的 GateContext；单调谨慎性质测试 | M1, M4 |
| **M6** | Dialogue 对接（与 D7 合并）：`intent-unproven` / `precondition-failed` → 追问；FACT 候选 → 确认追问 → assert | M4, D7 |
| **M7** | Agent 的 `remember` 改为写入 S2（行为变更，见决策点 3） | M3 |

M1–M4 都是纯逻辑，完全可以 headless 测试。M5 才开始接入真实循环。

---

## 八、待确认的决策点

1. **阶段划分**：S0 工作 → S1 情景 → S2 候选 → S3 巩固（每条带 scope，可按任务阶段隔离），这样理解“阶段式存放”对吗？
2. **FACT / PREF 晋升要什么级别的确认**：会话钥发出的澄清就够（推荐：这类记忆只影响规划，门和 Gate 2 照旧），还是要操作员钥签名？
3. **现有 `remember`**：改为写入 S2 候选、不再直接注入提示词（推荐：堵住持久化提示注入），还是暂时保持现状？

**已定（2026-09-27）**：1 按四阶段做；2 会话钥澄清即可；3 `remember` 改为写入 S2 候选（M7）。

---

## 附录：实现修订

落地 M1–M4 时对上文做了以下收紧与补全，设计意图不变：

1. **CAUTION 晋升 / 撤回规则落成计数**：在 ≥ `min_runs`（默认 3）个可用 run 里失败或无效，且**从未成功**，就晋升；只要成功过就视为“不稳定”，不算已知失败。晋升后，成功 run 累计 ≥ `min_runs` 才撤回，1 次成功不够。**操作员给出的警示不会被证据自动撤回**，只能显式 `retract`。
2. **操作员确认 = 可验证的签名陈述**：App 会话钥签发 `secdogie/memory-confirmation/v1`，内容 `{memory_id, subject=节点 DID, confirmed_at}`。每个节点在投影时都会重新校验：签名有效、签名者在 `confirmers` 里、`memory_id` 匹配、`subject` 等于该事件的作者。否则 `confirmed_by` 只是节点的一面之词，而 S3 会复制到全网。没有配置 `confirmers` 时，所有操作员依据的记忆都不计入（fail closed）。
3. **`memory_id` 必须等于内容哈希**（`sha256(canonical({mclass, scope, key, value}))`），投影时重算核对。针对 A 内容的确认无法挪到 B 内容上（有测试）。
4. **S2 只是待办队列，不是证据**：警示晋升时，证据一律用 `lessons.tally` 从 S1 已验证的情景里重新计算，不读候选里存的 evidence 字段。往本地文件里塞一条带 50 个假 run_id 的候选，什么也得不到（有测试）。
5. **门拒绝不算失败证据**：`outcome="rejected"`（门拒了，动作根本没执行）既不算失败也不算成功。否则一条“已知失败”警示会不断拒绝动作、又用这些拒绝喂养自己。
6. **`Episode` 不带 `author`**（StateStore 物化时不保留作者），改为带 `verified` / `usable`（已结束且链完整）。只有 usable 的情景参与提炼。
7. **撤销追溯**：`build_memory(trust=TrustPolicy)` 会把此后被撤销作者写的 `memory` 事件从视图里剔除，即使这些事件在撤销之前已经合入日志。
8. **`run.check_chain`**：把 `verify_run` 的链校验抽成对已物化实体的纯函数，折叠多个 run 时只物化一次；遇到畸形 step 时返回失败，而不是抛异常。

M1 的四个检查、S1 / S2 / S3 的各项核对都做了变异测试（M1 16、M2 13、M3 18、M4 27，共 74 个变异体全部被杀，每个都在 1 秒内失败）。

### 接入真实循环（第二阶段 Wave A，M5 + M7）

- **每步关联**：`loop_gate.make_plan_gate(observer=…)` 在门判定时交给 `loop_memory.StepCorrelator` 该动作的 `action_hash` 与 findings；循环紧接着写的那条 trace 只有在动作 kind 对得上时才取用它，且只取一次。没过门的步骤（done / look / ask_user…）不带 key，其 outcome 不计入记忆。
- **outcome**：`secdogie_agent.loop.classify_result` 与它解析的结果字符串放在同一处：门拒 / 操作员拒 / `refused:` → rejected；`error:` → failed；无可见变化 → no_change；提权未启动 → failed；其余已执行 → ok。
- **记忆进门**：`Supervisor(memory=MemoryConfig(…))`。每次 run 开始时读一次 S3 → `known_failures`；`purpose` = 当前 goal_id；`active_goal_ids` 来自目标树；`require_intent` 默认开。循环里 `known-failure` / `intent-unproven` / `intent-contradiction` 会阻断（拒绝原因进入模型历史），其余启发式仍只作提示。
- **意图字段**：模型动作可带 `rollback` / `irreversible`（只认字面 `true`），系统提示已说明；高风险一步两者皆无时被拒，模型据此重提。
- **自动巩固**：每个目标结束后跑一次 `consolidate`；失败只记日志，不影响目标结果。
- **M7**：`remember` 经 `remember_hook` 进 S2 隔离区；提示词里的记忆来自 `memory_block`（只有已确认的 S3）。独立 agent CLI 的 `--memory` 也改为“先隔离、确认后才注入”，管理命令为 `secdogie-agent memory list|confirm|forget`；按机主决定，迁移前已有条目视为已确认。

变异测试：citadel 18 个、agent 15 个变异体全部被杀。
