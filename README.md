# secdogie · 云端生命体

一个**重型、分布式、经 DID 认证、受监督**的个人自动化体。常驻大脑跑在**你掌控的
Cloudflare Workers 核心**里；重活（公开抓取、AX 压缩、并行网页探索）分发给**全球自愿
贡献的闲置浏览器节点**并行跑；推理/规划由核心用 **API key** 发起（key 只在核心）。
**本机离线也不中断**——核心继续浏览、思考、规划。每一步意图过**两层苏格拉底门**
（fail-closed、无开关可关），任何会改外部状态的动作都要你亲自确认。

> 本机设备是**可选插件**：在场时可在门后执行被挂起的动作，不在场核心不报错、照常
> 浏览与思考。macOS 感知走**无障碍 API（AXUIElement）、系统授权、只读元数据**——
> 不是读进程内存。

---

## 架构一图（按手绘图）

**错的拓扑**（核心和用户都连所有节点，用户能打到核心、能碰 API key）——不这么做：

```
        ✗  核心 ──┬──┬──┬──┬── 节点          用户 ──┬──┬──┬── 节点
                  全连                              全连   →  用户离核心一跳，凭据暴露面全开
```

**对的拓扑**（隔离层：核心只连 edge，用户只连内层，向内切换）：

```
                   ┌──────────────────────────────────┐
                   │  调度核心  Cloudflare Workers       │   ← API key 只在这里
                   │  推理 / 规划 / 凭据保管 / 门内认证    │   ← 推理在这里，节点上没有模型
                   └───────┬───────────────────┬────────┘
            只连 edge ↘     │                   │     ↙ 只连 edge（两端）
                    ┌───────▼──────┐     ┌──────▼───────┐
                    │  edge n0     │     │  edge n4     │   edge 挂了就向内顶替
                    ├──────────────┤     ├──────────────┤
         warm 热备 → │  n1          │     │  n3          │ ← warm 热备（已连，零间隙切换）
                    ├──────────────┤     ├──────────────┤
                    │       n2  ── 内层 inner ──         │   用户只连内层
                    └───────────────▲──────────────────┘
                                    │
                                 用户 user   ← 永不连 edge、离核心至少两跳、碰不到 API key
```

* **核心只连 edge**：每端最外侧的健康节点是 edge；它为每端保留最多 `edge_depth` 个
  **热备 edge**（已经连着），所以一个 edge 掉线不会留下空档——下一个顶上时已经在线。
* **用户只连内层、永不连 edge**：用户离核心至少两跳，打不到核心、碰不到 API key。
* **向内、预测性切换**：节点健康是 EWMA（健康 → 可疑 → 死亡），**可疑**节点已被排除出
  edge 候选，所以切换发生在硬失败**之前**，而不是等超时。`edge_depth` 在短线上自动降
  到 1，保证用户永远有内层缓冲——冗余不吃掉隔离缓冲。
* **不变量**：没有任何健康节点同时是「用户可达的内层」和「核心可达的 edge」
  （`user_is_isolated_from_core`）。
* **间歇/可切断**：连接随时可断、节点可换——就是下面的 churn 容错。

## 一次公开任务的生命周期（按手绘图的流程）

```
核心（API key 推理/规划，决定要抓什么）
  │ 1. 把一个「公开、无凭据」任务分给 N 个节点（冗余 N 份）
  ▼
节点 ①②③…（浏览器 / WASM，只做公开活）
  │ 2. 有界抓取（HTTPS-only、精确允许名单、不带凭据）+ 路由扫描 → 候选状态
  │ 3. 每个节点用自己的 Ed25519 密钥签名结果
  ▼
核心
  4. 验签：必须是被指派的节点签的，协议/任务号对得上
  5. N-of-M 共识：多数一致才采信；离群结果丢弃并给该节点降权（拜占庭容忍）
  6. 把达成共识的拓扑锚进签名 DAG（append-only、内容寻址）
```

**推理用 API key（核心的决定），如实调和**：推理走 LLM API，API key 是凭据 ⇒ **只存
核心、推理由核心发起，节点上不跑模型**。节点只做公开的抓取 / AX 压缩 / 并行探索，把
压缩后的拓扑喂回核心。**API key、登录凭据、私有会话上下文——三者都永不下发节点。**

---

## 硬边界（守住，不来回）

1. 志愿节点只收**公开 / 无凭据**任务 + 公开页面；任务对象里**永不含**凭据字段或私有
   会话上下文（`assert_public` 递归拒绝任何「秘密形状」的键，一个硬测试枚举证明之）。
2. 节点不可信：结果**必须签名**，核心**冗余 N-of-M 共识 + 复验**，离群结果丢弃并降权。
3. churn 容错是**可靠性工程**（志愿算力频繁掉线）——健康探测、预测性迁移、任务检查点
   + 重分配。不是对抗封禁 / 逃避防御的东西。
4. 核心受损时 **fail-safe**：控制面不可用就让节点安全停机，绝不暴露凭据。
5. 任何改外部状态的动作：Gate 2 **fail-closed**，任何入口人工确认（无开关可关）。

完整严禁清单见下方「安全边界」与 [`SECURITY.md`](SECURITY.md)。

---

## 层与模块（分布式版）

| 层 | 位置 | 做什么 |
|---|---|---|
| 调度核心 | Cloudflare Workers（`symbiont/src/swarm/` TS 核心 + `swarm/` Python 同构实现） | 任务队列与调度、节点注册 / 健康、冗余共识 + 结果复验、DAG 锚定、**API key 推理 / 规划**、凭据保管 + 门内认证浏览 |
| 算力网 | 志愿浏览器节点（`symbiont/src/swarm/node.ts` + `swarm` 的 `worker.py`） | 公开有界抓取（复用 sandbox）、AX 压缩（复用 `graph` route）、并行探索、签名结果；**不跑模型、不碰 API key** |
| churn 协议 | 核心 ↔ 节点（`swarm/secdogie_swarm/` 的 wire / registry / coordinator / topology） | 健康探测、预测性迁移、检查点 + 重分配、结果签名 + 冗余共识、隔离拓扑与向内切换 |
| 链路 | `webrtc/` 信令 + `transport/webrtc.py` + `symbiont/`（操作员页） | 浏览器 ↔ 核心的 WebRTC v2 认证链路（W1 指纹绑定 + 签名信封），生产节点通道的本地替身是真 TCP localnet |
| 本地插件（可选） | 用户设备 | AX 感知（macOS 授权只读）+ 执行；不在场核心不报错，改状态挂 Gate 2 |
| UI | 暗夜玻璃页面 / 应用 | 连核心看群体状态、答两道门 |

两核**逐字节同构**：Python 核心（`swarm/`）和 Workers/TS 核心（`symbiont/src/swarm/`）
用同一套规范 JSON（整数毫秒、无浮点）和同一套 Ed25519 签名信封；金向量
（`swarm/fixtures/swarm_vectors.json`）+ 一个 TS 节点逐字节复现 Python 结果签名的测试
共同钉住这个契约。

---

## 现状（诚实盘点）

本容器**没有** Cloudflare 账号、也没有真实 WebGPU 浏览器节点，所以分布式这一层以
**协议 + 核心逻辑 + 仿真 + 金向量 + headless 测试**落地（与仓库现有做法一致）。已建成
并推送：

| 能力 | 在哪 | 状态 |
|---|---|---|
| 调度核心（Python）| `swarm/secdogie_swarm/`（coordinator / consensus / registry / topology / anchor / service / wire / worker / localnet） | ✅ 真跑：core + 4 节点经真 TCP 抓真实本地页、共识、锚进 DAG、掉一个节点再决一次 |
| 调度核心（Workers/TS）| `symbiont/src/swarm/`（coordinator / consensus / registry / topology / tasks / wire / node） | ✅ 与 Python 同构，同 4 个 churn 场景测试绿 |
| churn 容错 | `registry.py`（EWMA 健康）+ `coordinator.py`（预测性迁移 + 检查点）+ `topology.py`（隔离拓扑 + 热备向内切换） | ✅ 拜占庭 / 沉默 / 中途死亡 / 无法达成共识四种不稳定性都有测试 |
| 跨语言契约 | `swarm/fixtures/swarm_vectors.json` + `symbiont/tests/swarm_*.test.ts` | ✅ 逐字节一致，含签名复现 |
| 公开抓取原语 | `agent/secdogie_agent/browse/`（Python）+ `symbiont/src/sandbox/`（TS）+ `graph/`（Rust → C ABI + wasm 路由扫描） | ✅ HTTPS-only、精确允许名单、不带凭据、从不返回正文 |
| 操作员链路 | `webrtc/` + `transport/webrtc.py` + `symbiont/`（单栏毛玻璃页，WebRTC v2） | ✅ |
| 基础栈 | `identity/` DID、`citadel/` 签名日志 + 两道门 + 阶段式记忆、`node/` 常驻节点、`dialogue/` 操作员 App | ✅ |

**需要你接**（本容器做不了）：真实 Cloudflare Workers 部署（Durable Object WebSocket
胶水 + wrangler）、真实浏览器节点与 WebGPU、自建 coturn NAT 穿透、Tauri 桌面外壳。

分阶段路线见 [`ROADMAP.md`](ROADMAP.md)；swarm 子系统细节见 [`swarm/README.md`](swarm/README.md)。

---

## 四大支柱（基础层，分布式之上仍然成立）

1. **分散式网络** —— DID（Ed25519 `did:key`）身份 + 会话 / 端点抽象 + 真 P2P 传输，经认证
   的 rendezvous 发现、直连升级 / relay 兜底、成员 gossip 反熵。hub 只作 bootstrap/relay。
2. **P2P 分布式获取信息与学习** —— 签名、哈希链、append-only 事件日志 + `StateStore`；日志 /
   状态经双重认证在 mesh 上反熵收敛。
3. **苏格拉底哲学系统** —— 两层门：**指令级**（意图审视）+ **动作计划级**
   （`GateDecision`：allow / reject / rewrite / request_reobserve）。门**只判定不执行**。
4. **受认证本地设备实战** —— 结构化观测融合（AX + DIB 按引用）、不透明目标 + 代际修
   TOCTOU；动作仍过安全边界 + 能力授权 + 人在环。

一条端到端的认证链路：

```
DID 身份  →  认证会话  →  签名状态  →  苏格拉底门  →  (能力 + HITL) 现实动作
identity/    transport/    citadel/     citadel/       agent/ + 安全边界
```

完整时序图与组件对照见 [`ARCHITECTURE.zh.md`](ARCHITECTURE.zh.md)。

---

## 安全边界（严禁清单 — 逐字保留）

**严禁**引入：进程内存写、远程线程注入、内核 HID、EDR/反检测、隐蔽持久化、提权、绕过
用户授权或 macOS Accessibility / Screen Recording 权限、把 HITL 改成默认自动批准、隐蔽
嵌入第三方服务、流量混淆、打洞式反检测。

**保持**：memory = 只读、execution = 受监督、high-risk = fail-closed 且在任何模式、任何
入口都必须人工确认（没有开关可以关闭）、physical action = 显式 capability。能力模型
**永不**包含 `process.memory.write` / 内核 HID / 反检测 / 提权。

`websession.py` 只**复用你自己在别处正规登录后保存的已授权会话**去只读导航 + 读结构——
**不输入凭据、不创建 / 窃取会话、不绕过认证 / 验证码 / 反爬**。每个切片提交前都 grep
回归确认无上述原语。完整信任模型见 [`SECURITY.md`](SECURITY.md)。

---

## 快速开始（headless 可测）

分布式核心（swarm）——纯逻辑 + 仿真 + 真 TCP localnet，无需 Cloudflare、无需浏览器：

```sh
# 构建 graph 原生库（路由扫描的 C ABI），再装 Python 包
cargo build --release --manifest-path graph/Cargo.toml
pip install -e identity -e graph/python -e agent -e "swarm[worker]" cryptography pytest

python -m pytest swarm/tests -q                 # 调度 / 共识 / churn / 拓扑 / 金向量 / 端到端
python -m secdogie_swarm.demo                    # core + 4 节点抓真实本地页、共识、锚 DAG、掉节点再决

cd symbiont && npm ci && npm run typecheck && npm test   # Workers/TS 核心 + 同构测试
```

基础栈（身份 / 传输 / citadel / 感知）：

```sh
pip install ruff
ruff check .
python -m pytest identity/tests transport/tests citadel/tests -q
cd agent && python -m pytest tests/test_observation.py tests/test_target.py -q
```

CI（[`.github/workflows/test.yml`](.github/workflows/test.yml)）对每个包跑 headless
`pytest`、构建 graph cdylib 跑 swarm / 浏览 parity、C 隧道跑 `ctest`、全 Python 跑 `ruff`。

---

## 深入阅读

- [`swarm/README.md`](swarm/README.md) —— 分布式调度核心、churn 协议、隔离拓扑、共识、测试。
- [`ARCHITECTURE.zh.md`](ARCHITECTURE.zh.md) —— 四大支柱、端到端认证链路、组件表、安全边界、路线图。
- [`ROADMAP.md`](ROADMAP.md) —— 总实现目标与里程碑。
- [`SECURITY.md`](SECURITY.md) —— 信任模型、分布式节点边界、漏洞上报。
- [`docs/AUDIT-P2P-ALIGNMENT.zh.md`](docs/AUDIT-P2P-ALIGNMENT.zh.md) —— 深度源码审计与冲突记录。
