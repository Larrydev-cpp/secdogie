# secdogie 主计划（Master Plan）

> 依据：仓库源代码（2026-09 通读），不是 README 声明。本文件把外部给出的
> Master Architecture Plan（Phase 0–4）对齐到代码现状，并拆成可独立合入、
> headless 可测的切片。总路线图见 [`ROADMAP.md`](../ROADMAP.md)，架构见
> [`ARCHITECTURE.zh.md`](../ARCHITECTURE.zh.md)。

## 设计前提

- **P2P 网络不依赖 VPS。** 没有专用服务器：rendezvous、relay、bootstrap、状态恢复都由
  对端承担。云主机、云存储只是可选的宿主或备份位置，不是前提。
- **Master 可随时叫停。** “抗毁”指抗基础设施故障（节点掉线、容器销毁、网络分区），
  不是对抗操作者本人：Master 能撤销任一节点的授权，节点收到后立即停止。
- **红线不变**（见 ARCHITECTURE §0/§4）：HITL 不改成默认自动批准；高风险 fail-closed；
  内存只读；无流量混淆 / 反检测 / 隐蔽持久化 / 提权；Master 私钥与浏览器会话文件
  不出本地设备（需要登录的访问由本地节点代办）。

## 现状盘点（已核实）

| 计划条目 | 仓库实际 |
|---|---|
| P2P.1 rendezvous | `transport/rendezvous.py`，在 #46/#47 合入 |
| P2P.2 直连升级 / relay 兜底 | `transport/upgrade.py`；已加固：所有 direct 帧带签名的 `ts` + `ctr`（`freshness.py` + `ReplayWindow`），`Session.path/epoch/fall_back` 先建后拆迁移，探测走 `route_to` 不动现有路由，PROBE-ACK 绑定被探测 DID 的随机一次性 nonce |
| P2P.3 gossip / Kademlia | `membership.py`、`dht.py` 已有，但**只是纯逻辑**：`gossip_round(a, b)` 在进程内比对两个 view，`find_node` 依赖注入的 `QueryFn`，**没有线协议** |
| 节点组装 | `RendezvousClient` / `DirectUpgrader` / `MembershipView` / `RoutingTable` 在 transport 包外零调用；`DirectUDPTransport` 只有一个 deliver 回调，没有多路复用 |
| Relay | `HubTransport` 只存在于进程内；没有网络 relay；CONNECT 经 relay 时不签名 |
| 复制 | `citadel/replication.py` 能跑在 UDP 上，但没有分片，大批事件会超 UDP 64KB |
| 派生凭证 | `identity/capability.py`：issuer→subject、TTL（默认 1 天）、白名单 scope；grant 经日志传播（`capability_grant`）。**没有撤销**；`Journal` allowlist 是静态的 |
| Master 密钥 | `Identity.save` 写 0600 文件，不是系统 Keychain |
| 步骤级账本 | **已接线**：`supervisor.agent_run_task` 用 `trace_on_entry` → `RunRecorder.record_step(frame_sha256, action, result)`；`loop_gate.py` 逐动作校验能力 |
| 死循环检测 | `agent/loop.py` 有 frame-hash + 同动作 `stall_limit`（exit 6），只停不 Revise |
| `memory_state_hash` | atlas 里不存在；atlas 尚未桥接进 agent 循环 |

## 切片（依赖顺序：0 → 2B → 2C → 2D → 3.0 → 3.1 → 3.2/3.3 → 3.4 → 4.x；3.2 可跳过）

### 0 · 文档对齐
本文件 + ROADMAP / ARCHITECTURE 状态修正。

### Phase 2 收尾：让 mesh 真正跑起来

**2B · 多路复用 + `MeshNode`**
- `DirectUDPTransport` 把非 direct 帧交给可选 `on_other(raw, addr)`，rendezvous 帧与数据面共用一个 socket。
- `transport/mux.py`：direct 帧载荷里的信封 `{"ch": "upgrade|repl|member", ...}`。
- `transport/node.py`：`MeshNode` 组装 rendezvous 注册/查询、`DirectUpgrader`、复制通道（按 ~48KB 分片）、
  membership gossip 线协议；`tick(now)` 驱动 keepalive / sweep / gossip / 复制，时钟可注入。
- 验收：3 节点 loopback（其中 1 个只能走 relay）日志收敛；掐断直连后经 relay 仍收敛。

**2C · rendezvous / relay 是节点角色**
- 任何自身可达的 `MeshNode`（公网 IP、端口映射或同一局域网）都可开启 `serve_rendezvous` / `serve_relay`，
  为白名单对端服务（同 libp2p circuit relay v2 的思路）。可以多个节点同时担任，没有单点。
- relay 只转发**原样的已签名帧**：接收方验原发送者签名，relay 无法伪造；CONNECT 因此自带签名和时间戳。
- 经 relay 到达的帧**不得**触发地址漫游采纳，否则会把 relay 地址记成对端的直连地址。
- 验收：relay 篡改被丢；relay 转发不改变对端 direct endpoint；担任 relay 的节点掉线后换一个仍收敛。

**2D · 无服务器的引导与发现**
- bootstrap 列表 = 任意几个已知对端（DID + 地址）；入网后靠 gossip 与 DHT 自维护，本地缓存见过的对端。
- 局域网发现：在本地子网广播自签名 membership 记录（验签 + allowlist），同网段节点不需要任何外部节点。
- DHT：`QueryFn` 走 mux 的请求/响应通道（带超时）。
- 验收：任一 bootstrap 对端挂掉，新节点仍能入网并 `find_peer`；纯局域网两节点自动互联。

**连通性的诚实边界**：所有节点都在对称 NAT 之后、且没有任何节点可达时，打洞和 relay 都无法成立。
此时至少需要一个可达的对端——任意一台自有设备开端口映射即可，不需要租 VPS。不承诺“100% 连通”。

### Phase 3：持久化与凭证治理（无头节点）

**3.0 · 撤销 + 无头节点 scope（必须早于任何无头节点上线）**
- `capability.py`：`create_revocation` / `verify_revocation`；`effective_scopes(..., revocations=)`；
  新增非物理 scope `journal.append`、`mesh.sync`。
- 撤销经日志传播（`capability_revoke`）；`Journal` 拒收被撤销 subject 在截止 seq 之后的事件；
  transport / 复制对其断开。
- 节点自检：自身 grant 过期或被撤销 → 停止写入与复制（fail-closed）。无头节点 grant 默认 TTL 6h，
  只能由本地 Master 续签；无头节点使用自己生成的子 DID，Master 私钥不出本地。

**3.1 · 恢复以对端为主，Block 归档是可选备份**
- 主路径：节点重启 / 新节点加入时，经复制从任意在线对端拉回完整日志（`Journal.merge` 自验证）。
- 备份路径（全部节点同时离线时兜底）：`citadel/archive.py` 导出签名、加密的 Block 到任意 `BlockStore`
  （本地目录、NAS、U 盘，或可选 S3/R2）。加密用部署级 archive key，使任意授权节点都能恢复。
- 验收：删库后恢复的 heads 与 `StateStore.materialize()` 完全一致；篡改、缺块、乱序被检出。

**3.2 · `S3Store`（可选）**：可选依赖，兼容 R2；测试不连网。

**3.3 · 状态投影**：`StateStore.materialize()` + goal tree 写入 SQLite 投影，只是缓存，可随时从日志重建。

**3.4 · 无头节点程序**：`secdogie-citadel node` 可跑在家用电脑、NAS、树莓派等任意自有设备。
无头节点**不跑 agent 循环**（没有桌面、没有物理 scope）；执行留在有人的本地设备、走 HITL。
Docker compose 只是可选打包。

### Phase 4：闭环与多节点（按现状缩小范围）
- **4.1** 步骤账本已接线 → 补端到端测试（假 provider），修正状态描述。
- **4.2** 停滞时先注入一次 Revise 提示，再到上限才停止（exit 6 语义不变）。`memory_state_hash`
  依赖 atlas 桥接，后置。
- **4.3** goal 声明所需 scope，指派给 grant 覆盖它的节点；物理 scope 只授给本地节点，
  所以物理步骤天然只落本地 + HITL；结果经复制汇总。

## 验证纪律

每片：`inspect → implement → test → 跑全量 → ruff check . → 提交`；transport / citadel / identity
测试分开跑（测试模块有重名）；多节点行为一律用 127.0.0.1 loopback + 注入时钟测试；提交前 grep
严禁原语回归（ARCHITECTURE §4）。
