# Glossary

Canonical English terminology for secdogie's documentation, with the Chinese term used in
the `*.zh.md` companions. English documents use the left column; the identifiers in
backticks are the names used in code.

## Project and governance

| Term | Chinese | Definition |
| --- | --- | --- |
| Supervised autonomous agent | 云端生命体 / 自主体 | The project as a whole: a decentralized, DID-authenticated agent that runs only on owned or authorized nodes and acts only under supervision. |
| North Star | 北极星 | The target end state that the roadmap converges on. |
| Four pillars | 四大支柱 | Decentralized networking; distributed acquisition and learning; the Socratic system; authenticated local-device execution. |
| Track / slice / milestone | 轨道 / 切片 / 里程碑 | A line of work; one PR-sized unit of it; a checkpoint with exit criteria. |
| Exit criteria | 完成判据 / 退出条件 | The observable conditions that close a milestone or stage. |
| Owner / operator | 机主 / 操作员 | The person who owns the nodes; the person (and key) who approves Gate 2 actions and signs resets. |
| Prohibited list | 严禁清单 | The capabilities the project never builds (memory writes, injection, kernel HID, evasion, covert persistence, privilege escalation, HITL bypass, traffic obfuscation). |

## Identity, trust and networking

| Term | Chinese | Definition |
| --- | --- | --- |
| DID | 去中心标识 | A `did:key` identifier derived from an Ed25519 public key. |
| Transport binding | 传输绑定 | A signed statement binding a DID to its X25519 transport key (`binding.py`). |
| Allowlist | 白名单 | The explicit set of DIDs a component trusts (`Allowlist`, `TrustPolicy`); there is no implicit "anyone". |
| Zero-trust by default | 零信任默认 | A component that decides whom to trust refuses to start without an allowlist; `ALLOW_ANY` must be explicit. |
| Revocation / k-of-n threshold signature | 撤销 / 门限签名 | A permanent withdrawal of a DID's authorization, signed by k of n Master keys. |
| Self-halt on revocation | 自检停机 | A process that receives a valid revocation of its own DID stops work and exits 0. |
| Rendezvous | 会合服务 | A DID-signed registration and lookup service that reports reflexive endpoints. |
| Direct-path upgrade / relay fallback | 直连升级 / 中继兜底 | Moving a relayed session to a direct UDP path, and falling back to an allowlisted relay when that fails. |
| Anti-entropy | 反熵 | Pairwise exchange of what each side lacks until replicas converge. |
| Gossip | 流言传播 | Epidemic propagation of membership or revocation records. |
| Sealed frame | 密封帧 | A frame encrypted to the recipient's bound X25519 key. |

## The Socratic system and memory

| Term | Chinese | Definition |
| --- | --- | --- |
| Socratic gate | 苏格拉底门 | A deterministic review step between intent and execution; it decides and never executes. |
| Gate 1 (instruction gate, plan gate) | 第一道门（指令门、计划门） | Intent review: instruction quality (`socratic.py`) and per-action plan review (`action_gate.py`, `GateDecision`). |
| Gate 2 | 第二道门 | Operator-signed authorization of one destructive action on one node (`authz.py`), signed through the Dialogue App. |
| Intent contract | 意图契约 | A destructive step must state its rollback or declare itself irreversible. |
| Known-failure memory | 已知失败记忆 | Actions that failed repeatedly across runs, which the plan gate rejects. |
| Staged memory: S1 episodic, S2 quarantine, S3 consolidated | 阶段式记忆：S1 情节、S2 隔离区、S3 巩固 | Journal projection → local candidate store → signed, operator-confirmed memory. Memory can only make Gate 1 stricter. |
| Human-in-the-loop (HITL) | 人在环 | Human confirmation of high-risk actions; never auto-approved by default. |
| Fail-closed | 失败即关闭 | On doubt or failure, refuse rather than proceed. |

## Execution and perception

| Term | Chinese | Definition |
| --- | --- | --- |
| Embodied execution layer | 具身执行层 | The layer that acts on a physical, display-attached device; the only place HITL applies. |
| Headless infrastructure node / display-attached node | 无头基础设施节点 / 带屏交互节点 | A node that relays and replicates unattended / a node that also runs the embodied execution layer. |
| Capability grant | 能力授权 | A signed, expiring grant of exact scopes from a trusted issuer to a node (`capability.py`). |
| Structured perception | 结构化感知 | Perception from the accessibility tree and read-only memory structures, never from screen pixels. |
| Accessibility tree (AX / UIA / AT-SPI) | 无障碍树 | The OS accessibility hierarchy on macOS / Windows / Linux. |
| DIB by reference | DIB 按引用 | A bitmap reconstructed read-only from process memory and carried as an identity and hash, never as pixels. |
| Read-only handle / token wall | 只读句柄 / 令牌墙 | Process access limited to read rights; refusal of protected, SYSTEM or higher-integrity targets. |
| TOCTOU protection | 代际校验 | Opaque targets with generation counters so an action cannot hit a changed element. |

## Inference mesh (`inference/`)

| Term | Chinese | Definition |
| --- | --- | --- |
| State 1: Nominal | 常态（new arc） | Distributed routing over confirmed links to leased model provider nodes. |
| State 2: Degraded | 当节点失效（连接中央 server） | Fault isolation and central fallback: failed and gray-failure nodes are routed around, and the coordinator relays. |
| State 3: Lockdown | 决策模型叛变时 | Adversarial containment: the decision layer and central path are suspended; clients pin two independent model provider nodes. |
| Serving node | P2P+S（决策层） | A node with the `serve` role: request admission, route selection, answer adjudication. |
| Transit forwarder | P2P | A node with the `forward` role that carries frames and decides nothing. |
| Standby forwarder | 新 P2P | A forward-only node held in reserve, recruited for an orphaned model provider node and used as the client's entry in Lockdown. |
| Model provider node | M/API | A node that answers with a leased upstream credential and signs every answer. |
| Coordinator | 中央 server / c/s | The advisory service and central fallback relay; not a trust root. |
| Key broker | API key | The credential-leasing authority: issues short-lived leases sealed to a node's X25519 key. |
| Client | user | The signed requester that re-verifies every answer itself. |
| Gray-failure node | (wrong) P2P | A node that heartbeats normally but mishandles traffic. |
| Reinstated node | S+~~wrong~~ P2P | A formerly quarantined node returned to service after a clean probation. |
| Mutiny | 叛变 | Provable adversarial behavior by a model provider node, an upstream model or the decision layer, established by self-verifying incident evidence (`mutiny.py`). |
| Latch / isolation / quarantine | 锁存 / 隔离 / 隔离期 | Evidence-based exclusion cleared only by an operator reset / a latch that excludes one node without Lockdown / a claim-based, time-limited exclusion by the coordinator. |
| Lease / canary | 租约 / 金丝雀 | A broker-signed, short-lived right to answer with a model / a honeytoken whose appearance in output proves lease material leaked. |
| Witnessed link | 见证链路 | A link counted only while each end's heartbeat names a recent heartbeat it received directly from the other. |
| Heartbeat chain fork | 心跳链分叉 | Two signed heartbeats of one node with the same sequence number or predecessor and different contents. |
| Ledger root | 账本根 | The Merkle root over a node's held latches, carried in its heartbeat for anti-entropy. |
| Decision gate | 决策门 | The history-aware pre-routing review: hard vetoes and escalation to a cross-check (`gate.py`). |
| Session | 会话 | One request's bounded, explicit lifecycle in a `SessionTable` (`session.py`). |
