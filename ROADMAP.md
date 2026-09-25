# secdogie · 云端生命体 路线图

> 总实现目标与到达它的里程碑。完整架构、组件表与端到端认证链路见
> [`ARCHITECTURE.zh.md`](ARCHITECTURE.zh.md)；按代码现状拆好的切片计划见
> [`docs/MASTER-PLAN.zh.md`](docs/MASTER-PLAN.zh.md)。

## 北极星

一个**去中心、经 DID 认证、受监督**的自主体,运行在**你自有 / 已授权**的节点上:无中心服务器
而靠 P2P 存续,从**公开或已授权**的资源学习(**结构化感知,不截屏**),用**两层苏格拉底门**
审视每一步意图,最终**只在经认证的本地设备、在能力授权 + 人在环下**采取现实动作。

**不可协商的前提(严禁)**:进程内存写、远程线程注入、内核 HID、EDR/反检测、隐蔽持久化、
提权、绕过用户授权 / macOS 权限、把 HITL 改成默认自动批准、流量混淆、打洞式反检测。
**保持**:memory=只读、execution=受监督、high-risk=fail-closed、physical action=显式 capability。

## 五条并行 track

- **A 分散式网络** —— DID 身份 → 会话/端点 → 真 P2P UDP → rendezvous → 直连升级/relay 兜底 →
  成员 gossip →(后)节点组装(`MeshNode`)、rendezvous/relay 作为对端角色、无服务器引导与局域网发现、
  Kademlia 线协议、数据面机密性接上 tunnel/WireGuard。**不依赖 VPS**:没有专用服务器。
- **B 分布式状态与学习** —— 签名日志 + StateStore → 反熵复制在 mesh 上收敛 → 内容寻址的观测/
  知识存储(大数据按 content_hash 引用)→(后)评估真正的 CRDT。
- **C 心智(苏格拉底 + 监督)** —— 指令门 + 计划门 → **2.7 Agent↔Citadel run 闭环** →
  2.8 崩溃恢复(executing 崩溃先重观测再重试)。
- **D 受认证设备实战** —— 结构化观测融合(AX + DIB 按引用)+ 目标 TOCTOU → 2.9 能力签名授权
  模型 → AX 原生身份/代际的 OS 侧接线(macOS/Windows 验证)→ DIB 完整接入运行时。
- **E 控制与运维** —— desktop 原生 GUI / console / fleet 协调 / CI 矩阵 / 发布 / 文档,贯穿维护。

## 里程碑(每个给「完成判据」)

- **M1 基础认证链 — ✅ 已达成**:2.1→2.6 + P2P.1。判据:身份→会话→状态→门→(受监督)动作
  每一步可验证,全 headless 绿。
- **M2 P2P mesh + 状态收敛 — ✅ 构件已达成,🔨 组装中**:P2P.2 直连升级 + P2P.3 成员 gossip + Replication.1。
  判据:任意两个授权节点能直连或经 relay 通信,一个节点写入的签名状态收敛到其余节点(loopback 多节点测)。
  P2P.2 已加固:direct 帧签名 `ts`+`ctr`(时钟偏差 + 重放窗口)、`Session` 先建后拆迁移、ACK 绑定被探测 DID。
  缺口:gossip/DHT 还只是纯逻辑、没有线协议,各构件尚未组装成一个节点(MASTER-PLAN 2B–2D)。
- **M3 运行闭环 + 能力治理 — 🔨 进行中(2.7 / 2.8 / 2.9 已建成;`agent_run_task` 已把逐步 trace 写进 run 状态、
  逐动作做能力校验;待补撤销与端到端测试)**:C 的 2.7/2.8 + D 的 2.9。判据:交给节点一个目标,它能
  规划→观测→过门→(能力 + HITL)执行→验证→写回,崩溃后先重观测再安全恢复,每个动作经签名能力校验。
- **M4 端到端纵切 — 🔜**:一个授权节点「学习 + 行动」,结果全网收敛,可 headless 演示整条链。
- **M5 加固 / 落地 — 🔜**:OS 原生接线在实机验证、tunnel 机密性接上数据面、打包/发布/文档、安全复审。

## 贯穿纪律

每片 `inspect→implement→test→跑全量→lint→提交`,不做一次性重写;全部 headless/loopback 可测、
DID 签名 + allowlist 门控;提交前 grep 回归确认无严禁原语;冲突/现状如实记录进文档,不为架构图伪造完成状态。
