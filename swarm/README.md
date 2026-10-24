# secdogie swarm · 分布式调度核心 + churn 容错

本机离线时，**常驻调度核心**把公开的重活（有界抓取、路由 / AX 压缩、并行网页探索）
分发给**自愿贡献的闲置浏览器节点**并行跑；节点签名回结果，核心**冗余共识 + 复验**，
把达成一致的拓扑锚进签名 DAG。推理 / 规划由核心用 **API key** 发起——**key 只在核心、
节点上不跑模型**。

这个目录是调度核心的 **Python 实现**。它在 Cloudflare Workers 上有一个**逐字节同构**的
TS 实现（`symbiont/src/swarm/`），用同一套规范 JSON 和 Ed25519 签名信封，由金向量钉住。

```
核心（API key 推理/规划）
  │ 1. 公开任务分给 N 个节点（冗余）        tasks.py · build_crawl / assert_public
  ▼
节点（浏览器 / WASM，只做公开活）           worker.py · 有界抓取 + 路由扫描 → 候选状态
  │ 2. 每个节点签名结果                      identity · Ed25519 信封
  ▼
核心
  3. 验签 + N-of-M 共识 + 离群降权           consensus.py · verify_result / Tally
  4. 锚进签名 DAG                            anchor.py  · add_state / add_observation
  churn：健康探测 / 预测迁移 / 检查点重分配   registry.py + coordinator.py
  隔离拓扑：核心↔edge、用户↔内层、向内切换    topology.py
```

## 模块

| 文件 | 做什么 |
|---|---|
| `tasks.py` | 任务 / 结果 schema，整数毫秒时间戳。`build_crawl`、`validate_task`、`sign_task`、`build_result`、`output_digest`（共识对齐的摘要），以及 `assert_public`——递归拒绝任何「秘密形状」的键（key/token/secret/cookie/auth/…），保证下发节点的任务只含公开活。 |
| `wire.py` | 核心 ↔ 节点线上协议 `secdogie/swarm-wire/v1`：DISPATCH / RESULT / HEARTBEAT / PROBE / PONG / CHECKPOINT / CANCEL。`encode` = 规范 JSON；`decode` 严格（拒重复键、拒 NaN）。 |
| `registry.py` | 节点健康：EWMA 延迟 + 错误率 + 心跳，分类 **healthy → suspect → dead**。`suspect` 是**预测性**信号：节点在掉线之前就被标记，好让调度器提前迁移它的在途任务。 |
| `consensus.py` | 不可信节点的结果：`verify_result`（必须是被指派的节点签的）+ `Tally`（N-of-M 共识，离群结果是少数派）+ `quorum_for`（默认严格多数）。 |
| `coordinator.py` | 调度状态机：分配 → 超时 / 健康探测 → **预测性迁移** → 重分配 → 收集 → 共识 → 核心签名的共识记录。携带 `resume_from` 检查点把在途进度交给替补节点。 |
| `topology.py` | 连接角色层：核心只连 **edge**、用户只连**内层**、**向内切换**；`edge_depth` 热备 edge（零间隙切换）+ 隔离不变量。见下。 |
| `anchor.py` | 把达成共识的爬取结果写成签名 delta（`add_state` / `add_observation` / `add_reference`），ingest 进真正的 `graph` DAG。 |
| `worker.py` | 节点侧公开任务执行：验核心签名的任务 → 有界抓取 + 路由扫描 → 签名结果。**从不返回页面正文**，只返回候选状态。 |
| `service.py` | 把上面全部接成可跑的 `SwarmCore`（真时钟、churn tick 循环、验签 + 共识 + 锚定）与 `SwarmNode`（心跳、收 DISPATCH 跑 crawl、签结果）。 |
| `localnet.py` | `CoreServer` / `NodeClient`：asyncio TCP、4 字节大端长度前缀帧——生产 WebRTC 数据通道的**本地真替身**（不是 mock）。 |
| `demo.py` | `python -m secdogie_swarm.demo`：core + 4 节点抓**真实**本地页、共识、锚 DAG、掉一个节点再决一次。 |

## 隔离拓扑与「切换灵活性」（`topology.py`）

三层：核心 ↔ **edge 节点** ↔ **内层节点** ↔ 用户。

* **核心只连 edge**：每端最外侧的健康节点是 edge。核心为每端保留最多 `edge_depth` 个
  **热备 edge**（已经连着的），所以一个 edge 掉线不会留下空档——下一个顶上时已在线。
* **用户只连内层、永不连 edge**：用户离核心至少两跳，碰不到 API key。
* **向内、预测性切换**：`suspect`（正在退化）的节点已被排除出 edge 候选，所以切换发生在
  硬失败**之前**；`edge_depth` 在短线上自动降到 1，保证用户始终有内层缓冲——冗余不吃掉
  隔离缓冲（`min_inner`）。
* **不变量** `user_is_isolated_from_core`：没有任何健康节点同时是「用户可达的内层」和
  「核心可达的 edge」。不足缓冲时用户干脆没有入口（宁可不连，也不把用户放到 edge 上）。

> **这版改了什么**：之前每端只有**一个** edge，edge 一掉就有「找下一个 → 重连」的空档，
> 切换不灵活。现在 `SwarmCore` 默认 `edge_depth=2`：热备 edge 一直连着，掉线即顶替、零间隙，
> 且随健康**连续**向内滑动，而不是等超时才硬切。短线上自动降级，不牺牲用户隔离。

```python
topo = LineTopology(registry, edge_depth=2)   # 每端 2 个热备 edge
topo.edges()        # 核心连的 edge（含热备），按线序
topo.standbys()     # 热备 edge（已连，随时顶替外层）
topo.inner()        # 用户可连的内层
topo.user_entry()   # 给用户的最居中内层节点
topo.roles()        # 一次性快照：edges / standbys / inner / user_entry / isolated
```

## 跨语言契约

两核用同一套规范 JSON（`sort_keys`、`(",",":")`、`ensure_ascii=False`、**整数毫秒、无浮点**）
和同一套 Ed25519 签名信封（payload + 签名者 `did:key` + 对规范字节的签名）。Ed25519 是
确定性的，所以同一 seed + 同一 message 两语言得到**逐字节相同**的签名。

* 金向量：`fixtures/swarm_vectors.json`（核心签名的任务、节点签名的结果 + 摘要、每条 wire
  消息的规范字节）。`python fixtures/generate.py --check` 校验新鲜度。
* TS 对账：`symbiont/tests/swarm_vectors.test.ts` 对齐字节，并有一个 **TS 节点逐字节复现
  Python 结果签名** 的测试。

## churn / 不稳定性

志愿算力会频繁掉线，所以可靠性是一等公民。`tests/test_chaos.py` 用真实调度器 + 可注入
故障的仿真节点证明：

* **拜占庭**：撒谎节点的离群结果被共识丢弃并降权，核心永不采信错误结果；
* **沉默**：不回话的节点被预测性迁移，任务仍然决出；
* **中途死亡**：在途节点死掉，检查点交给替补；
* **无法达成共识**：诚实地 FAILED，绝不挂起、绝不采信。

## 运行

```sh
cargo build --release --manifest-path ../graph/Cargo.toml       # 路由扫描 C ABI
pip install -e ../identity -e ../graph/python -e ../agent -e ".[worker]" cryptography pytest

python -m pytest tests -q            # 调度 / 共识 / churn / 拓扑 / 金向量 / 端到端
python -m secdogie_swarm.demo        # 端到端演示
python fixtures/generate.py --check  # 金向量新鲜度
```

## 本容器做不到的（需要你的账号）

真实 Cloudflare Workers 部署（Durable Object WebSocket 胶水 + `wrangler.toml`）、真实浏览器
节点与 WebGPU、自建 coturn NAT 穿透。核心逻辑、协议、共识、churn、金向量都已在本容器里
可建可测；端到端上线由你接。
