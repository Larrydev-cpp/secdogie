# Secdogie 协同应用系统架构设计与数据通信协议规范 v1.0

> 状态：v1.0 已确认（2026-09-27），按第四节分片实现中；实现时的修订见文末“附录：v1.0 实现修订”。
> 不引入任何截图 / OCR / 中心化服务器。

## 0. 定位与红线

**Secdogie Dialogue & Alignment App（下称 App）** 是操作员与 Agent 之间进行
**意图对齐、状态同频、逻辑追问、密码学授权**的唯一协同枢纽。它不是单向遥控端。

四条红线（贯穿全设计）：
1. **纯消费 + 签名 + 对话**：App 渲染 Agent 推来的 `Observation`，自己不截屏、不读进程内存、不做感知。感知如何产生（机主 #51 的 `agent/perception/`）不属于本设计。
2. **无中心服务器**：会话只经 secdogie 已有的底层——libsodium 加密 Tunnel / relay / WebRTC P2P（信令用 Cloudflare Workers，仅做握手，不经手明文）。
3. **密钥不出本机**：操作员 Ed25519 私钥只在 App 本地，Agent 节点永不持有，App 无法替 Agent 自签，Agent 也无法替操作员签。
4. **门只判定不执行，人工确认无开关**：App 是把 Gate 2 人工确认**加密化**的那一端；不存在"无有效签名也放行破坏性动作"的路径。撤销（R1.2）对操作员同样生效：被撤销的操作员签名不再通过验证。

复用的既有基石（不新造）：
- `secdogie_identity`：Ed25519 `did:key`、`sign_payload` / `verify_payload` / `canonical`、`TrustPolicy`（白名单减撤销）。
- `secdogie_citadel.authz`：`action_hash` / `create_authorization` / `verify_authorization`（Gate 2，PR #53 已合入 main）。
- `secdogie_citadel.action_gate` / `socratic`：双重苏格拉底门（Gate 1 计划门 + 指令门）。
- `secdogie_agent.perception.observation`：`Observation` / `SemanticNode` / `Geometry`（结构化视界，只读消费）。
- `secdogie_transport`：`DirectUDPTransport` / `RelayClient` / rendezvous / WebRTC 信令。

---

## 一、核心子系统划分与交互模型

App 在一个**经 DID 认证的长连接会话**之上，多路复用四个逻辑通道。会话本身由 P2P 会话层维护；四个子系统只管各自的 payload 语义。

```
┌─────────────────────────────────────────────────────────────┐
│                    Secdogie Dialogue App                     │
│                                                               │
│  ┌───────────────┐  ┌────────────────┐  ┌─────────────────┐  │
│  │ ① Socratic     │  │ ② Zero-Screen  │  │ ③ Cryptographic │  │
│  │   Dialogue     │  │   Inspector    │  │   Guard (Gate2) │  │
│  │  (意图对齐)     │  │  (结构化视界)   │  │  (私钥/签名)     │  │
│  └───────┬────────┘  └───────┬────────┘  └────────┬────────┘  │
│          └───────────────────┼────────────────────┘           │
│                    ┌─────────┴──────────┐                     │
│                    │ ④ P2P Session Layer │                     │
│                    │ (libsodium Tunnel /  │                    │
│                    │  relay / WebRTC 信令)│                     │
│                    └─────────┬──────────┘                     │
└──────────────────────────────┼──────────────────────────────┘
                               │  一条认证会话，双向签名帧
                    ┌──────────┴───────────┐
                    │  Agent 节点 (secdogie) │
                    │  loop + action_gate +  │
                    │  socratic + authz      │
                    └───────────────────────┘
```

### ① 意图对齐与苏格拉底对话子系统（Socratic Dialogue Channel）

- **Gate 1 追问机制**：Agent 侧的 `action_gate.gate()` 或 `socratic.review()` 判定目标歧义 / 状态未集满（`REQUEST_REOBSERVE`）/ 隐性风险（缺意图契约 → `REWRITE`）时，不静默继续，而是经本通道发起一条 `DialoguePacket(dialogue_type=SocraticQuestion)`，携带 `probe_id`、问题文本、可选项（`suggested_options`）。Agent 端阻塞在该 `probe_id` 上，超时按 fail-closed 处理（挂起，不放行）。
- **意图修补与对齐**：用户回 `DialoguePacket(dialogue_type=UserClarification, in_reply_to=probe_id)`，`content` 补充上下文或修正动作图谱。Agent 侧把它作为对当前 run 的**增量输入**（对应 `recovery.py` 的"先重观测再重试" / 追加任务上下文），无需重新初始化任务即可继续推演。对齐后 Agent 回 `SystemStatus` 告知已采纳。
- 关键：这是"追问—澄清"闭环，不是聊天。每个 `probe_id` 要么被澄清、要么超时挂起，不丢。

### ② 纯结构化视界巡检子系统（Zero-Screenshot Inspector）

- **AX 树 + DIB 增量流**：Agent 每步（或状态变化时）推 `StateSnapshotPacket`，内含该窗口的 `semantic_nodes` **增量**（新增/变更/移除，靠稳定的 `automation_id` 或 `path_index` 定位）与 `dib_references` 元数据（DIB 盲区节点的形状/hash 等**引用**，绝不含像素）。App 用 `SemanticNode.parent_index` / `path_index` 重建树，渲染为 TUI 文本节点视图。
- **焦点与视线追踪**：`focused_node_index` 指向 Agent 当前正在 Hit-Test 或准备操作的节点，App 高亮它。
- App 只渲染收到的结构，**不发起任何感知**。渲染是纯函数：`(上一快照, 增量) -> 新树视图`，可 headless 单测。

### ③ Gate 2 密码学硬拦截子系统（Cryptographic Guard）

- **本地私钥安全隔离**：操作员 Ed25519 私钥存于 App 本地的加密 keyfile（口令派生密钥加密；或系统 keychain）。仅在签名瞬间解密于内存，用后清零。私钥永不上会话、永不到 Agent 节点。
- **挑战—响应工作流**：Agent 的 `action_gate` 对 `destructive` 动作产生 `UNAUTHORIZED_ACTION` 时，经本通道抛 `Gate2ChallengePacket`（`action_hash`、风险断言、`expires_at`）。App 解析并展示风险与 `action_hash`；用户 Approve 时，App 用操作员私钥调用 `authz.create_authorization` 生成令牌，封进 `Gate2ResponsePacket` 回传；Agent 侧 `verify_authorization` 校验（类型 / hash / subject / 未过期 / 操作员未撤销 / 签名有效）通过才执行。Deny 或超时即拒。
- App 展示的 `action_hash` 必须由 App **本地对收到的 `target_action` 重算**并与挑战里的 `action_hash` 比对一致后才允许签名——防止 Agent 谎报动作骗签名。

### ④ 去中心化网络与会话管理子系统（P2P Session Layer）

- 经 Cloudflare Workers 做 WebRTC P2P 信令握手（仅交换 offer/answer/candidate，不经手明文），建立基于 C/libsodium 的双向加密 Tunnel；或在原生端直接用 `DirectUDPTransport` + `RelayClient`（打洞失败回落中继）。
- App 以一个白名单内的操作员 DID 加入网格。长连接 + 心跳 + 重连；断线期间 Agent 侧对未响应的 `probe_id` / `challenge_id` 一律 fail-closed。

---

## 二、消息流与 Protocol Data Schemas（纯 Python 类型定义）

**信封与签名**：每个出站包 = `Header` + 一个 typed payload，整体经 `sign_payload` 覆盖 `canonical(header + payload)`。收端 `verify_payload(obj, trust_policy)`：签名有效 + 发送方 DID 在白名单且未撤销 + 时间戳新鲜，才反序列化 payload。不合格即静默丢弃。

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

PROTOCOL_VERSION = "secdogie/dialogue/v1"

# ---- 信封 -------------------------------------------------------------------

@dataclass(frozen=True)
class Header:
    version: str                 # PROTOCOL_VERSION
    sender_did: str              # did:key（sender_pubkey 的规范形式）
    recipient_did: str           # 收端 DID：包只对这一个节点有效，不能被转投（修订 1）
    session_id: str              # 一次会话的随机 id
    seq: int                     # 会话内单调递增，配重放窗口
    timestamp_ns: int            # 发送方时钟，收端做 ±skew 新鲜度校验
    # 签名不在 Header 里：整包经 sign_payload 得到外层 signer/sig，
    # 覆盖 canonical({header, kind, payload})。

class PacketKind(str, Enum):
    DIALOGUE = "dialogue"
    STATE_SNAPSHOT = "state_snapshot"
    GATE2_CHALLENGE = "gate2_challenge"
    GATE2_RESPONSE = "gate2_response"
    SESSION = "session"          # hello / heartbeat / bye

@dataclass(frozen=True)
class Envelope:                  # open_envelope 认证 + 解析成功后的结果
    header: Header
    kind: PacketKind
    packet: object               # 下列某个 typed packet（线上为 to_wire 形式，经 sign_payload 签名）
    signer: str

# ---- ① 对话 -----------------------------------------------------------------

class DialogueType(str, Enum):
    SOCRATIC_QUESTION = "SocraticQuestion"     # Agent -> 用户：追问
    USER_CLARIFICATION = "UserClarification"   # 用户 -> Agent：澄清 / 修补
    SYSTEM_STATUS = "SystemStatus"             # Agent -> 用户：状态告知

@dataclass(frozen=True)
class DialoguePacket:
    probe_id: str                        # 每次追问的稳定 id；澄清用 in_reply_to 指回
    dialogue_type: DialogueType
    content: str
    in_reply_to: str = ""                # UserClarification 指向的 probe_id
    suggested_options: tuple[str, ...] = ()   # 供用户快速选择的候选
    gate_finding: str = ""               # 触发追问的 action_gate finding 种类（可空）

# ---- ② 结构化视界（增量）----------------------------------------------------

class NodeOp(str, Enum):
    ADD = "add"
    UPDATE = "update"
    REMOVE = "remove"

@dataclass(frozen=True)
class NodeDelta:
    op: NodeOp
    index: int                   # 节点在本窗口流内的稳定句柄（Agent 分配，跨快照不变）（修订 2）
    # parent_index / focused_node_index / DibRef.node_index 一律引用句柄；
    # automation_id / path_index 只描述节点来源，不作键
    automation_id: str = ""
    path_index: tuple[int, ...] = ()
    # ADD / UPDATE 时携带节点投影（复用 perception 的 SemanticNode 语义字段）
    role: str = ""
    name: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)   # x,y,w,h
    enabled: bool = True
    is_interactive: bool = False
    parent_index: int = -1

@dataclass(frozen=True)
class DibRef:
    # DIB 盲区节点的“引用”元数据，绝不含像素。字段取自 perception 的 VisualReference。
    node_index: int
    width: int
    height: int
    pixel_format: str
    content_hash: str            # 变了就知道盲区内容变了；App 只显示 hash/尺寸

@dataclass(frozen=True)
class StateSnapshotPacket:
    window_id: int
    app_pid: int
    generation: int              # 观测代际，配 TOCTOU / 陈旧检测
    nodes: tuple[NodeDelta, ...] # 相对上一快照的增量
    dib_references: tuple[DibRef, ...] = ()
    focused_node_index: int = -1 # 当前 Hit-Test / 待操作节点，App 高亮
    full: bool = False           # True = 全量重同步（重连后首帧）

# ---- ③ Gate 2 挑战 / 响应 ---------------------------------------------------

class RiskLevel(str, Enum):
    LOW = "low"
    HIGH = "high"
    IRREVERSIBLE = "irreversible"   # 删核心文件 / 资金 / 提权

@dataclass(frozen=True)
class TargetAction:
    # 与 authz.action_hash 的 _AUTHORIZED_FIELDS 一一对应，App 据此本地重算 hash
    kind: str
    target_id: str = ""
    target_role: str = ""
    target_name: str = ""
    text: str = ""
    high_risk: bool = False      # 与 PlannedAction 同默认值（修订 3）

@dataclass(frozen=True)
class Gate2ChallengePacket:
    challenge_id: str
    target_action: TargetAction
    risk_level: RiskLevel
    risk_explanation: str            # 人读的风险断言（为何不可逆 / 影响面）
    action_hash: str                 # Agent 声称的 hash；App 必须本地重算比对
    subject_did: str                 # 目标节点（令牌的 subject）
    expires_at: float                # 挑战时效；过期即作废

class Verdict(str, Enum):
    APPROVE = "Approve"
    DENY = "Deny"

@dataclass(frozen=True)
class Gate2ResponsePacket:
    challenge_id: str
    action_hash: str                 # App 本地重算的 hash（回带以便对账）
    user_verdict: Verdict
    authorization: dict = field(default_factory=dict)  # Approve 时 = authz 令牌
    # Deny 时 authorization 为空；Agent 侧 verify_authorization 失败即拒。

# ---- ④ 会话控制（修订 4）------------------------------------------------------

class SessionEvent(str, Enum):
    HELLO = "hello"
    HEARTBEAT = "heartbeat"
    BYE = "bye"
    RESYNC = "resync"            # App -> Agent：请发全量快照

@dataclass(frozen=True)
class SessionPacket:
    event: SessionEvent
    note: str = ""
```

### 端到端数据流闭环

```
用户下任务(DialoguePacket/UserClarification 或独立 submit)
      │
      ▼
Agent 规划一步动作 ──► action_gate.gate()
      │                     │
      │        ┌────────────┼─────────────┬───────────────┐
      │        ▼            ▼             ▼               ▼
      │   ALLOW(非破坏)  REWRITE/REOBSERVE  REJECT(缺授权)   (读)
      │        │            │             │
      │        │            ▼             ▼
      │        │   ① SocraticQuestion  ③ Gate2Challenge
      │        │        (probe_id)        (challenge_id, action_hash)
      │        │            │             │
      │        │            ▼             ▼
      │        │   用户 Clarification   App 本地重算 hash 比对
      │        │            │             │  一致 & Approve
      │        │            ▼             ▼
      │        │   Agent 采纳，继续     create_authorization → Gate2Response(令牌)
      │        │                          │
      │        ▼                          ▼
      │   执行动作 ◄──────────────  verify_authorization 通过
      │        │
      ▼        ▼
② StateSnapshot 增量流（全程持续推送，焦点高亮）
```

任一挂起点（probe / challenge）超时或断线 → fail-closed（不放行、不执行）。撤销一旦到达，被撤销的操作员的 Gate2Response 不再验证通过。

---

## 三、交互应用形态与 TUI 布局（Textual / Rich）

轻量可交互 TUI，分屏：

```
┌───────────────────────────┬───────────────────────────────────┐
│                           │  ② 结构化视界 Inspector             │
│  ① Socratic 对话 / 意图对齐 │  window#42  gen 7  focus=dib:3      │
│                           │  ▸ AXWindow "Drawing"               │
│  Agent: 目标有歧义——你指的  │    ▸ AXGroup "Toolbar"              │
│    是哪个"保存"?           │    ▾ canvas (DIB 盲区)              │
│    [1] 顶部工具栏           │      • polyline "外墙" ★(focus)    │
│    [2] 对话框按钮           │      • layer "结构"                 │
│  You: 1                    ├───────────────────────────────────┤
│  Agent: 已采纳，继续。      │  ③ Gate 2 密码学控制台              │
│                           │  ⚠ IRREVERSIBLE: delete file-42     │
│                           │  action_hash: 9f3a…(本地重算✓ 一致) │
│                           │  为何危险: 不可恢复，无回退路径       │
│                           │  过期: 04:59  [A]pprove  [D]eny      │
└───────────────────────────┴───────────────────────────────────┘
状态栏: session 已认证 did:key:z6Mk…  · tunnel 直连  · 心跳 1.2s
```

- 左：对话流，追问带可选项，键选即回 `UserClarification`。
- 右上：结构化树，`focused_node_index` 高亮（★）；DIB 盲区节点标注但不显示像素。
- 右下：Gate 2 弹窗——先本地重算 `action_hash` 并显示是否与挑战一致，只有一致才允许 `[A]pprove`；Approve 走本地私钥签名（可能再要一次口令解锁）。

视图层与协议层解耦：所有 packet 的解析、hash 重算、令牌生成都是纯函数，可在无 TUI、无网络下 headless 单测；Textual 只做渲染与按键。

---

## 四、模块划分与后续实现切片（确认设计后再动手）

新包 `dialogue/`（`secdogie-dialogue`，依赖 identity + citadel），分片：
1. **协议层 `protocol.py`**：上述 dataclasses + `sign_envelope` / `verify_envelope`（复用 identity）。纯函数，headless 单测（签名/新鲜度/重放/未撤销）。
2. **Gate 2 客户端 `guard.py`**：本地私钥管理 + 收 `Gate2Challenge` → 本地重算 hash 比对 → `create_authorization` → `Gate2Response`。单测：hash 不符拒签、过期拒、Deny 不出令牌、撤销后 Agent 侧拒。
3. **Inspector `inspector.py`**：`(snapshot, delta) -> tree` 纯归并 + 渲染模型。单测：增量 add/update/remove、焦点高亮、DIB 只显元数据。
4. **对话状态机 `dialogue.py`**：probe/clarify 闭环 + 超时挂起。单测：追问—澄清—采纳、超时 fail-closed。
5. **会话层 `session.py`**：绑 transport / WebRTC 信令，心跳重连。多进程回环判据。
6. **TUI `app.py`**：Textual 分屏，只接前面的纯模型。
7. **Agent 侧对接**：`action_gate` 的 finding → 发 probe / challenge；`verify_authorization` 收 `Gate2Response`。接进 T8（签名审批通路）。

每片独立 headless 可测；确认本设计后逐片写测试与实现。

---

## 附录：v1.0 实现修订

落地 `protocol.py` 时对第二节做了四处补全，均为收紧或填补悬空引用，不改变设计意图：

1. **`Header.recipient_did`**：原设计无接收方绑定，发给节点 A 的包在 skew 窗口内可被转投给节点 B（B 的防重放表里没有这个 session）。现在收端要求 `recipient_did == 本节点 DID`。
2. **`NodeDelta.index`**：原设计的 `parent_index` / `focused_node_index` / `DibRef.node_index` 引用“节点索引”，但 `NodeDelta` 自身没有索引字段。现定义为 Agent 分配、在同一窗口流内稳定的句柄，所有交叉引用都指向它。
3. **`TargetAction.high_risk` 默认 `False`**：与 `PlannedAction` 一致，否则同样的显式参数得到不同的 `action_hash`。线上所有字段都必须显式给出，默认值不会被隐式补上。
4. **`SessionPacket` / `SessionEvent`**：补上 `PacketKind.SESSION` 对应的载荷（hello / heartbeat / bye / resync）。

协议层的具体规则（`open_envelope`）：
- **先认证再解析**：签名有效且签名者在信任策略上（`TrustPolicy`，撤销即拒）之后，才解析 header 与 payload；未认证的对端永远到不了解析器。
- **严格 schema**：每个字段必填，未知字段拒收（结构化视界里无法夹带像素字段），类型精确（bool 不算 int，非有限浮点不算时间）。
- **防重放**：`timestamp_ns` 须在本地时钟 ±30 s 内；同一 (sender, session) 的 `seq` 严格递增。被拒的包不消耗 seq。会话表有上限，满时只遗忘空闲超过两倍 skew 窗口的会话（此时它被接收过的任何包都已不新鲜，遗忘不会重新打开重放）；若全是活跃会话则拒绝新会话（fail closed），绝不驱逐活跃会话。
