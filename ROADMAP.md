# secdogie · 云端生命体 路线图

> 总实现目标与到达它的里程碑。完整架构、组件表与端到端认证链路见
> [`ARCHITECTURE.zh.md`](ARCHITECTURE.zh.md)。

## 北极星

一个**去中心、经 DID 认证、受监督**的自主体,运行在**你自有 / 已授权**的节点上:无中心服务器
而靠 P2P 存续,从**公开或已授权**的资源学习(**结构化感知,不截屏**),用**两层苏格拉底门**
审视每一步意图,最终**只在经认证的本地设备、在能力授权 + 人在环下**采取现实动作。

**不可协商的前提(严禁)**:进程内存写、远程线程注入、内核 HID、EDR/反检测、隐蔽持久化、
提权、绕过用户授权 / macOS 权限、把 HITL 改成默认自动批准、流量混淆、打洞式反检测。
**保持**:memory=只读、execution=受监督、high-risk=fail-closed、physical action=显式 capability。

## 五条并行 track

- **A 分散式网络** —— DID 身份 → 会话/端点 → 真 P2P UDP → rendezvous → 直连升级/relay 兜底 →
  成员 gossip →(后)Kademlia 路由、bootstrap 加固、数据面机密性接上 tunnel/WireGuard。
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
- **M2 P2P mesh + 状态收敛 — ✅ 已达成**:P2P.2 直连升级 + P2P.3 成员 gossip + Replication.1。
  判据:任意两个授权节点能直连或经 relay 通信,一个节点写入的签名状态收敛到其余节点(loopback 多节点测)。
- **M3 运行闭环 + 能力治理 — 🔨 进行中(2.7 / 2.8 / 2.9 构件已建成,待接入实时 agent 回路)**:C 的 2.7/2.8 + D 的 2.9。判据:交给节点一个目标,它能
  规划→观测→过门→(能力 + HITL)执行→验证→写回,崩溃后先重观测再安全恢复,每个动作经签名能力校验。
- **M4 端到端纵切 — 🔜**:一个授权节点「学习 + 行动」,结果全网收敛,可 headless 演示整条链。
- **M5 加固 / 落地 — 🔜**:OS 原生接线在实机验证、tunnel 机密性接上数据面、打包/发布/文档、安全复审。

## 切片计划（2026-09）

每项一个 PR：审计 → 实现 → 测试 → 跑全量 → lint → 提交。✅ 已完成，🔨 进行中，🔜 未开始。

**已定决策**：
- Tunnel T2 走双后端 + 统一 DID 控制面（生产 WireGuard，实验/气隙网 C SDTP）；
- 中转只转发密文（端到端）；NAT 穿透经 mesh 协调，只在白名单节点之间；
- 整机路由 = 网格子网 + 出口节点；
- 撤销由 Master **k-of-n 门限签名**、**永久**生效；
- 感知结构化优先，截图须显式开启；
- HITL 默认只确认高风险，但高风险在任何入口都必须确认。

| 轨道 | 切片 | 状态 |
| --- | --- | --- |
| S 安全修复/纠偏 | S1 SDTP 确认后再切换（= T2.0a）· S2 fleet 安全模式全有或全无 · S3 文档与代码对齐 | ✅ ✅ ✅ |
| A 网格运行时 | 2C 任一白名单节点兼任 relay · A0 rendezvous 上 UDP · A1 wire gossip · A2 `secdogie-node` 运行时 | ✅ 🔜 🔜 🔜 |
| R 3.0 撤销 | R1 MasterSet/门限撤销声明/TrustPolicy · R2 全面执行 + 缓存失效 · R3 零信任默认关闭 · R4 传播（journal + 快速帧）· R5 自检停机 | 🔜 |
| T2 隧道 | C 轨：T2.0b 卫生/fuzz/netns 冒烟 → T2.0c Noise IK v2 握手 → T2.1 定时器/rekey/DoS 限速 → T2.2 mesh 模式 → T2.3 本地控制 socket | 🔜 |
| | Py 轨（`netd/`）：T2.4a 记录字段/地址派生/期望状态 → T2.4b WireGuard 后端 + 调和器 + 子网 → T2.4c SDTP 后端 → T2.5 密文中转 → T2.6 NAT 穿透 → T2.8 出口节点 → T2.9 撤销联动 + netns e2e | 🔜 |
| C M3 实时回路 | C1 CI 跑真实回路测试 · C2 HITL 修正（高风险清单、全入口必确认、签名审批）· C3 fleet/console/desktop 审批通路 · C4 门控加强 · C5 运行记录前移 · C6 统一入口 · C7 设备类别与无头隔离 | 🔜 |
| D 感知 | D1 observation/target 入环 · D2 结构化优先（Windows UIA 结构图，截图显式开启）· D3 macOS AX 命中测试 · D4 Atlas 只读桥 | 🔜 |
| W 浏览器 | W1 浏览器 DID + 信令 DID 认证 · W2 跨语言签名向量 · W3 aiortc 桥接技术验证 · W4 浏览器作为观察/审批端 | 🔜 |
| M 收尾 | M4 纵切演示 · M5 安全复审 / 实机验证 / 发布 | 🔜 |

**顺序**：
1. S；
2. R1–R3、A0/A1、C1/C2；
3. A2（含 R4/R5）、T2 两条轨并行、C3–C7；
4. T2.5–T2.9、D；
5. W、M。

网络轨道与 agent 轨道改动的包不同，可以并行推进。

## 贯穿纪律

每片 `inspect→implement→test→跑全量→lint→提交`,不做一次性重写;全部 headless/loopback 可测、
DID 签名 + allowlist 门控;提交前 grep 回归确认无严禁原语;冲突/现状如实记录进文档,不为架构图伪造完成状态。
