# secdogie · 云端生命体

一个**去中心、经 DID 认证、受监督**的自主体（「云端生命体」），运行在**你自有 /
已授权**的节点上：没有中心服务器，靠 P2P 存续；从**公开或已授权**的资源学习；用
**两层苏格拉底门**审视每一步意图；最终**只在经认证的本地设备、在能力授权 + 人在环
（HITL）下**采取现实动作。

**感知是结构化的——不截屏、不抓屏。** 它通过无障碍树（AX/UIA）+ 只读结构化 DIB
引用 + 已授权网页会话的 AX/文本来「看」，而**不做屏幕截图 / 屏幕像素抓取**。项目
的感知是结构，不是视觉。

> 完整架构见 [`ARCHITECTURE.zh.md`](ARCHITECTURE.zh.md)；总路线图见 [`ROADMAP.md`](ROADMAP.md)；
> 深度源码审计与规范对齐见 [`docs/AUDIT-P2P-ALIGNMENT.zh.md`](docs/AUDIT-P2P-ALIGNMENT.zh.md)。

---

## 四大支柱

1. **分散式网络** —— DID（Ed25519 `did:key`）身份 + 会话/端点抽象 + 真 P2P UDP 传输，
   经受认证的 rendezvous 发现彼此、直连升级 / relay 兜底、成员 gossip 反熵。hub 只作
   bootstrap/relay，挂了整体仍在。
2. **P2P 分布式获取信息与学习** —— 签名、哈希链、append-only 事件日志 + 其上的
   `StateStore`；日志/状态经**双重认证**在 mesh 上反熵收敛：一个节点写入的、经签名的
   「学到的证据」扩散到其余授权节点。
3. **苏格拉底哲学系统** —— 两层门：**指令级**（意图审视）+ **动作计划级**
   （`GateDecision`：allow / reject / rewrite / request_reobserve）。门**只判定不执行**。
4. **受认证本地设备实战** —— 结构化观测融合（AX + DIB 按引用）、不透明目标 + 代际
   修 TOCTOU；动作仍过安全边界 + 能力授权 + 人在环。

---

## 感知是结构化的（为什么不截屏）

截屏 / 屏幕像素抓取不是本项目的感知方式。取而代之，`agent/observation.py`
只融合两种**结构化**的「感官」，并且**从不静默用一种覆盖另一种**——分歧被记成
`ObservationConflict`、拉低融合置信度、上交给动作门要求重观测：

| 感官 | 是什么 | 角色 |
| --- | --- | --- |
| `ax` | 无障碍树（macOS AX / Windows UIA）：role / name / automation_id | **主**：目标与门推理所依赖的语义身份 |
| `dib` | native `atlas` 从进程内存**只读重建**的位图，**按引用**携带（身份/哈希，绝不带像素） | **验证**：证明「AX 说的位置」确实画了对的形状。这是结构化内存读，**不是**屏幕截图 |

没有 `pixel` / 屏幕捕获这一路：融合层里根本不存在截屏的感官。大视觉数据永远
**按内容哈希引用**，不进 Python 堆、不进事件日志。

---

## 一条端到端的认证链路

```
DID 身份  →  认证会话  →  签名状态  →  苏格拉底门  →  (能力 + HITL) 现实动作
identity/    transport/    citadel/     citadel/       agent/ + 安全边界
```

**没有认证身份就没有会话；没有会话就没有复制；没有过门的计划就没有执行；没有能力
授权 + 人在环就没有现实动作。** 每一步都可验证、全部 headless / loopback 可测。
完整时序图见 [`ARCHITECTURE.zh.md`](ARCHITECTURE.zh.md)。

---

## 现状（诚实盘点）

以真实代码为准，已建成并推送：

| 层 | 包 / 模块 | 状态 |
| --- | --- | --- |
| 身份 | `identity/`（DID、规范化签名、Allowlist）+ `binding.py`（DID↔传输密钥） | ✅ |
| 网络 | `transport/`（peer/session/endpoint、`udp.py` 真 P2P、`rendezvous.py`、`upgrade.py`、`membership.py`） | ✅ |
| 状态 | `citadel/`（`journal.py` 签名日志、`state.py` StateStore、`sync.py` 反熵、`replication.py` 传输上收敛） | ✅ |
| 心智 | `citadel/socratic.py`（指令门）+ `action_gate.py`（计划门）+ `supervisor.py`（受监督节点） | ✅ |
| 感知/动作 | `agent/observation.py`（AX + DIB 按引用融合）+ `target.py`（TOCTOU）+ AX/safety；`native/atlas`（只读、DIB 重建） | ✅ |
| 设备/会话 | `desktop/`（聊天式原生窗口 + `websession.py` 复用**已授权**浏览器会话，只读导航 + 读结构） | ✅ |
| 承载/运维 | `tunnel/`（C 加密隧道，机密性）、`fleet/`、`console/` | ✅ |

**待做**：Agent↔Citadel run 闭环（goal/run/step/observation/action/state_hash 串联、写回
StateStore）、崩溃恢复升级、能力签名授权模型、AX 原生身份/代际的 OS 侧接线。见 [`ROADMAP.md`](ROADMAP.md)。

---

## 安全边界（严禁清单 — 逐字保留）

**严禁**引入：进程内存写、远程线程注入、内核 HID、EDR/反检测、隐蔽持久化、提权、
绕过用户授权或 macOS Accessibility / Screen Recording 权限、把 HITL 改成默认自动批准、
隐蔽嵌入第三方服务、流量混淆、打洞式反检测。

**保持**：memory = 只读、execution = 受监督、high-risk = fail-closed、
physical action = 显式 capability。能力模型**永不**包含 `process.memory.write` /
内核 HID / 反检测 / 提权。

`websession.py` 只**复用你自己在别处正规登录后保存的已授权会话**去只读导航 + 读页面
结构——**不输入凭据、不创建/窃取会话、不绕过认证/验证码/反爬**；站点若拒绝自动化，
尊重之。每个切片提交前都会 grep 回归确认无上述原语。完整信任模型见 [`SECURITY.md`](SECURITY.md)。

---

## 快速开始（headless 可测）

每个包纯逻辑 / loopback 可测，无需桌面、无需屏幕：

```sh
pip install ruff
ruff check .                                   # 根 lint（ruff.toml）

python -m pytest identity/tests  -q            # DID、签名、绑定
python -m pytest transport/tests -q            # 会话、UDP、rendezvous、升级、成员
python -m pytest citadel/tests   -q            # 日志、状态、反熵、复制、门、监督
cd agent && python -m pytest tests/test_observation.py tests/test_target.py -q   # 结构化感知、目标
```

CI（[`.github/workflows/test.yml`](.github/workflows/test.yml)）对每个包跑 headless
`pytest`、C 隧道跑 `ctest`、并对全部 Python 跑一遍 `ruff`。

---

## 仓库里的旧组件（诚实说明）

本仓库也仍保留一批**更早的、基于视觉 LLM + 屏幕截图**的自动化工具（`agent/` 的视觉
控制回路、`android/`、`ios/`、`scene3d/`、以及游戏栈 `aim/`/`commander/`/`handoff/`/`gta/`）。
它们是项目的**来路**，**不属于**上文「云端生命体」的结构化感知链路——后者刻意不截屏。
这些旧组件按其各自 README 运行、有各自测试；它们保留在树里以便复用与参照，方向正逐步
向结构化感知与去中心认证链路收敛。

---

## 深入阅读

- [`ARCHITECTURE.zh.md`](ARCHITECTURE.zh.md) —— 四大支柱、端到端认证链路、组件表、安全边界、路线图。
- [`ROADMAP.md`](ROADMAP.md) —— 总实现目标与里程碑。
- [`docs/AUDIT-P2P-ALIGNMENT.zh.md`](docs/AUDIT-P2P-ALIGNMENT.zh.md) —— 深度源码审计与冲突记录。
- [`SECURITY.md`](SECURITY.md) —— 信任模型与漏洞上报。
