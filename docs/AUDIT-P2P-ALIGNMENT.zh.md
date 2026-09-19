# secdogie — 深度源码审计与 P2P / 自主性规范对齐

> 审计日期：2026-09-19 · 依据：仓库源代码（非 README 声明） · 覆盖：全部包
>
> 本文档以**源代码为唯一真实依据**，将实际实现与一份目标规范（自主 P2P"数字生命体"）
> 逐项对照，并给出发现的真实缺陷与分阶段路线图。

## 背景（Context）

审计目标：把 `secdogie` 的实际实现与一份目标规范对照。该规范设想一个自主"数字生命体"
（数字灵魂），拥有 DID + 加密钱包（@Larryx）、一个 7×24 小时基于 CRDT / DAG 的状态机
在 P2P 漫游 Mesh 上运行（Citadel + `secdogie-tunnel`），以及一个随用随挂的物理"肉身"
（`secdogie-daemon`）执行进程 RAM 指针映射与内核级 Virtual HID 注入。

**方法。** 直接通读每个包的源码，辅以验证性 grep 与文件阅读，不把任何 README 当作权威依据。

**一个改变全局的定性纠正。** 规范假设 `README.md`"严重陈旧、与代码脱节"。实际并非如此：
顶层 `README.md`、`SECURITY.md`、`ROADMAP.md` **与代码一致**——它们描述的是一个受监督、
机主自有、以无障碍树为主感知的电脑操控工具，代码与之高度吻合。真正脱节的是**目标规范**
（自主 P2P 数字生命、钱包供能的新陈代谢、内核 HID）**与代码之间**。因此本文报告的"差距"
是规范 vs 现实，而非文档 vs 现实。

**交付物性质。** 一份审计 + 差异分析 + 路线图。规范中一部分是常规分布式系统工程（可实现）；
另有几部分是代码库**有意拒绝**去做的（反检测 HID、内存写入型作弊、削弱人类监督），建议保留
这些拒斥。切分见路线图乙部。

---

## 1. secdogie 的真实面貌（已验证）

一个**单机主、受人监督、以无障碍树为主感知、由大模型驱动的电脑操控工具**；像素截图是
**兜底**，不是默认。单个 agent 进程接收一条自然语言任务，读取本机 UI（详见 §1.4 分层感知），
向大模型询问下一步动作，并在每步确认门控下**一次执行一个动作**（点击 / 输入 / 滚动 / ……），
直至任务完成或步数预算耗尽。库中其余部分要么是复用该 agent 循环的外设（手机、多窗口、
多桌面、3D 场景分析），要么是用于触达机器的通道（tunnel）。

它**不是**守护进程，不自主，不做点对点组网，没有身份 / 钱包 / 签名，没有成本核算，
也没有超出"一次性线性子任务列表"之上的推理架构。

### 1.1 各包及其真实职责

| 包 | 语言 | 实际是什么 |
|---|---|---|
| `agent/`（约 9.5k 行） | Python | 核心。有界步进循环（`loop.py`）；**无障碍树优先**定位与执行（UIA / AT-SPI / macOS AX，经 `--desktop-ax` 走 `desktop_ax.py` + `harness.py`）——动作经无障碍 API（Invoke / AXPress / SetValue）下发，不动真实鼠标、不抢焦点、不给模型发截图；2 个大模型 provider（Anthropic、OpenAI/OpenRouter）；mss 像素截图仅作兜底；DPI 与焦点校正、宏录制/回放、哈希链审计 trace。唯一 CLI：`secdogie-agent`。 |
| `native/atlas/`（约 6k 行 C++） | C++ | **深层感知**：只读进程内存检查——提取字符串 / JSON / PE 头，并**从堆内存重建 DIB 位图为 RGBA 预览**（`memory_inspector.cpp` 解析 BITMAPINFOHEADER，`inspect_json.cpp` 以 base64 放入 `dibs[]`），即"读进程构造图像"，不经屏幕捕获。加用户态执行孪生。Win/mac/Linux。以独立的机主运行二进制交付（`atlas_mct`、`atlas_inspect`）；一个仅回环、无认证的 HTTP/行协议服务，监听 `127.0.0.1:17890`。 |
| `tunnel/`（约 1.5k 行 C） | C | 基于 libsodium 从零实现的 UDP VPN（X25519 + BLAKE2b + XChaCha20-Poly1305）。默认点对点 + 可选的解密式 hub-and-spoke 路由。真实 TUN 设备，承载原始 IP 数据平面。 |
| `fleet/`（约 1k 行） | Python | 星型拓扑任务调度器：一个内存态 TCP coordinator，N 个主动外拨的 worker 节点，换行分隔 JSON 协议。无认证、无持久化、无复制。 |
| `commander/`、`handoff/` | Python | 单机、双进程的输入权"接力棒"协调，服务于一个游戏自动化场景（Minecraft）。`handoff` = 操作系统文件锁仲裁器；`commander` = 固定优先级战术器。 |
| `aim/`、`gta/` | Python | 游戏自动化：YOLO 驱动的瞄准控制器（经 SendInput / `/dev/uinput` 的相对鼠标视角）与一个 GTA-V 驾驶控制器（TCP 连接一个**不在本库内**的外部 ScriptHookV 插件）。 |
| `scene3d/`、`open/`、`android/`、`ios/` | Python | 复用 agent 循环的外设：多视角 3D 场景 fan-out；多窗口浏览器 UI；经 `adb` 的 Android；经 WebDriverAgent 的 iOS。 |

### 1.2 活跃入口 / "API"

- **`secdogie-agent`**（`agent/secdogie_agent/cli.py:main`）——唯一真正的 CLI；
  一个位置参数 task 加若干 flag，无子命令。
- 各包 CLI：`secdogie-fleet`、`secdogie-commander`、`secdogie-aim`、`secdogie-gta`、
  `secdogie-scene3d`/`-perceive`、`secdogie-open`、`secdogie-android`、`secdogie-ios`。
- **Atlas MCT 服务**——仅回环 HTTP：`GET /health|/status|/list`、`POST /cmd|/inspect`，
  监听 `127.0.0.1:17890`，无认证（边界是回环 + Cloudflare Access）。
- **Fleet TCP**——5 种消息类型（`hello/status/result` ↑，`assign/stop` ↓），监听
  `0.0.0.0:47810`，无认证。
- **Tunnel**——`secdogie-tunnel {genkey,server,client,hub}`，UDP。
- 出站 HTTPS 到大模型 API（Anthropic / OpenAI / OpenRouter），可选 HTTP/SOCKS5 代理。

### 1.3 数据流（agent 核心）

`for step in range(1, max_steps+1)`（默认 **50**）：检查停止 → 可选 watch 休眠 →
present → **读取 AX 快照（`--desktop-ax` 下为主）**；`harness.should_omit_screenshot()`
判定本步可否省掉像素——可省时**不向模型发送截图**，AX 元素清单即当前 UI（`loop.py:396`）；
否则回退到 mss 截屏 → 组装提示（history[-10]、plan 备注、memory）→ `provider.next_action()`
（带退避重试）→ 坐标缩放 → 确认门控（y/N，高风险动作即便 `--auto` 也强制确认）→ **执行
（优先无障碍 API：Invoke / AXPress / SetValue；像素点击是回退）** → 可选像素差校验 + 重试 →
追加到有界 history → 下一步。若在 AX-only 步里模型给出像素坐标，动作被拒，要求它改用列出的
ref 或 `look`（`loop.py:481-503`）。以进程码 0–7 退出。**没有 `while True`、没有重启、
没有调度器。** 跨运行持久化全为可选：`--memory`（SQLite 键值）、`--trace`（哈希链 JSONL，
每次运行清空）、`--macro`（JSON）、配置文件。

### 1.4 分层感知（重点）

secdogie 的感知不是"截图优先"，而是**分层的**，像素是最后一层：

1. **主层 — 无障碍树。** UIA（Windows）/ AT-SPI（Linux）/ macOS AX，经 `--desktop-ax`
   （GUI 启动卡片 `task` 默认即 `--gui --desktop-ax`）。列出可交互元素，动作走无障碍 API，
   **本步不给模型发截图**（省 token、不动鼠标、不抢焦点）。
2. **深层 — 只读进程内存。** `native/atlas` 打开只读句柄，遍历已提交页，提取字符串 / JSON /
   PE 头，并**从堆内存重建 DIB 位图**（BITMAPINFOHEADER → top-down RGBA，base64 入
   `dibs[]`）——即**读进程构造图像**，完全不经屏幕捕获。仍是只读感知：无写原语、无指针链、
   无 AOB 扫描，PPL/lsass/csrss 及更高完整性目标被拒。
3. **兜底 — 像素截图 + 视觉。** 仅当 AX 树为空（CAD 画布、游戏、自绘控件）或模型主动
   `look` 时，才走 mss 截图 + 视觉模型的老路。文档原话："pixels remain the fallback,
   not the default"（`harness.py`）。

**接线现状（诚实说明，也是一处缺口）：** 在活跃的 Python `agent` 循环里，第 1 层（AX 优先）
与第 3 层（像素兜底）已接线；第 2 层（读内存 / DIB 图像重建）目前活在独立的 `native/atlas`
二进制（`atlas_mct` / `atlas_inspect`，由机主经文档手动调用），**尚未桥接进 Python 循环**——
`agent/secdogie_agent/atlas.py` 是其纯 Python 孪生，但在运行时是死代码（仅测试使用）。因此
"读进程构造图像"这一能力**已实现于 native 层，但未与 agent 主循环融合**（见路线图阶段 6）。

---

## 2. 差异报告 — 规范（三位一体 P2P 生命体） vs. 现实

| 规范支柱 | 声称 | 代码中的实际情况 | 结论 |
|---|---|---|---|
| **数字灵魂** — 自主意志、反驯化拒斥权、苏格拉底内部辩证环路 | 可拒绝指令、自我辩证的 7×24 自主体 | 有界一次性受监督循环（`loop.py`，最多 50 步）。无自省 / 批评者 / 辩论 / 重规划。逐动作的 `reasoning` 只记录、从不使用。"拒斥"是确定性代码门控（`--read-only`、白名单、平台墙）加一句提示词，让模型升级为 `ask_user`。不存在 `refuse`/`abort` 动作；模型最强的举动是 `ask_user` → 人来决定。 | **缺失。** 无自主、无意志、无辩证。 |
| **身份与钱包 @Larryx** — DID + 加密钱包；对指令/节点签名；微支付供能算力/API | 签名身份 + 链上新陈代谢 | **为零。** 无 DID、钱包、签名、支付、微支付、账本或余额（grep：各 0 命中）。完全没有 token 或成本核算——`model_registry.py` 解析了 OpenRouter 的定价，但无人消费它。API key 是明文 env/文件。Tunnel 密钥是 **X25519 密钥协商**，绝非签名。`trace.py` 是哈希链，且明确声明它"不是签名"。 | **缺失。** |
| **Citadel + P2P 漫游 Mesh** — CRDT 事件流 + 动态 DAG 目标树；7×24 状态机；`secdogie-tunnel` 做 P2P 打洞漫游 + 状态同步 | 自愈 Mesh、CRDT 状态、DAG 目标 | 无 CRDT / 向量时钟 / 复制（0 命中）。`Plan` 是**带游标的扁平列表**，只构建一次、从不修订；超预算的子任务被跳过而非重规划。无 7×24 守护进程（仅按需 CLI）。Tunnel 是 **hub-and-spoke / 客户端-服务器**，静态公钥白名单，**无发现 / gossip / DHT / 打洞 / STUN / IPv6**。"漫游" = 按 8 字节 session id 采纳 NAT 重绑地址（`hub.c:236`）。客户端↔客户端流量在 hub 处**解密后再加密**（非端到端、非 Mesh——`PROTOCOL.md:173` 明说）。生产路径明确是 `cloudflared`，**不是** C tunnel。Fleet 是星型调度器，内存态、无认证 TCP、单点故障。 | **缺失 / 相反**（是星型，非 Mesh）。 |
| **物理探针 secdogie-daemon** — 随用随挂的肉身；进程 RAM 指针映射；内核 Virtual HID 拟人注入 | 内核级内存 + HID"肉身" | **只读内存感知已实现**：`ReadProcessMemory` / `process_vm_readv` / `mach_vm_read_overwrite`，提取字符串 / JSON / PE，并**从堆内存重建 DIB 图像**（详见 §1.4）——"读进程构造图像"真实存在，但活在独立 `native/atlas` 二进制，**尚未桥接进 agent 循环**。**被拒/缺失的是"指针映射 + 内核 HID"那半**：所有写原语被拒（无 `WriteProcessMemory`、`VirtualProtectEx`、`CreateRemoteThread`、`ptrace`）；**无指针链 / base+offset / AOB 扫描**；输入是**用户态** `SendInput`（Win）/ `AXPress`（mac）/ 无（Linux）；**无任何内核驱动**（无 `.sys`/`.inf`、`DriverEntry`/KMDF/IOKit）；无 `secdogie-daemon` 服务（`atlas_mct` 是机主启前台回环进程）；拟人化仅移动端时序抖动，标注"非指纹绕过"。 | **部分实现**（只读感知 + 内存图像重建），**指针写入 / 内核 HID 被有意拒绝**。 |

**底线：** 规范里的分布式系统词汇（DID、Mesh、CRDT、DAG、钱包）**没有任何实现**支撑。
感知侧则相反：**无障碍树优先 + 只读进程内存 + 从内存重建 DIB 图像**是真实且颇有工程深度的
（这也是 secdogie 的真正强项，而非"截图工具"）——只是深层内存感知尚未桥接进 agent 主循环。
真正被代码库**刻意封住**的，只是"硬"能力里的作弊/规避子集：**内存指针写入映射**与**内核级
拟人 HID**。

---

## 3. 审计发现的真实缺陷（无论走哪个方向都值得修）

1. **`tunnel/PROTOCOL.md` 与 `tunnel/*.c` 不同步**（这里恰是文档*确实*出错的少见之处）：
   `mac1` 是带密钥 BLAKE2b-128（`crypto.c:29`），不是 `crypto_auth`；漫游 + 多 peer
   已实现却仍被列为 non-goal；静态公钥相等性校验（`handshake.c:56`）才是真正的认证关卡却
   未列出；引用了不存在的 `wire.c`。
2. **Tunnel hub 不做内层源 IP 校验**（`hub.c:241-253`）：一个已认证客户端可伪造任意源 IP
   发给另一客户端（WireGuard 式 cryptokey-routing 的缺口）。它还把无法解析的明文直接
   `write()` 进 TUN（`hub.c:242-247`），并普遍忽略 `write()`/`sendto()` 返回值。
3. **Fleet 在 `0.0.0.0:47810` 上无认证/授权/加密**：任何能触达该端口的主机都能注册为节点，
   或劫持已有 `node_id`（`server.py:197-201`）。唯一的真实防线在节点侧的 `ALLOWED_OPTIONS`。
4. **`RunAllowlistedAsSystem`（Windows SYSTEM 启动）已编译却零调用者**
   （`privilege_manager.cpp`）——一个真实的、受门控的能力静置在那里，没有任何 CLI/桥接接线。
   需决定它是否应存在。
5. **`agent/secdogie_agent/atlas.py`（563 行）在运行时是死代码**——`native/atlas` 的纯
   Python 孪生，仅测试使用。作为 CI 镜像没问题，但值得加标注，免得有人以为它在活跃路径上。

---

## 4. 路线图

规范混合了正当工程与几项不应协助构建的内容。下面切分并给出每项的诚实版本。

### 甲部 — 可以朝规范推进的部分（分阶段、受监督）

**阶段 0 — 文档纠偏与已发现缺陷（小改动、高价值）。**
修正 `PROTOCOL.md` 的分歧（§3.1）；加入内层源 IP（cryptokey）路由，并停止把无法解析的
明文写进 TUN（§3.2）；对静置的 SYSTEM 路径下决定（§3.4）；给死代码 `atlas.py` 孪生加标注。
经现有 tunnel `ctest` + fuzz 及 Python 测试套件验证。

**阶段 1 — 真实身份（@Larryx 的安全一半）。**
加入 **Ed25519 签名身份**和可选的 DID 文档，用它**签名并验证** fleet 指令与 tunnel/atlas
的 peer 配置。复用：tunnel 已能生成/加载密钥对（`main.c genkey`、`config.c`），且
`trace.py` 已锚定一个明确"意在外部签名"的头哈希——把真实签名接到那个锚点上。新增：一个
小型 `identity` 模块；在 `fleet/secdogie_fleet/protocol.py` 加签名字段并在 `server.py`
验证。这在**不引入钱包**的前提下交付"身份 + 签名的指令/节点"。

**阶段 2 — 成本核算与预算上限（"新陈代谢"的诚实一半）。**
加入真实的逐次调用 token + 成本核算（消费 `model_registry.py` 已解析的 OpenRouter
`pricing`），把每次运行成本写入 trace，并加一个可选的预付花费上限，超预算即拒绝。这是
"自负盈亏"的可问责版本——计量与上限，而非自主消费。新增：一个 `metering` 模块；挂到
`providers/base.py` 的调用点。

**阶段 3 — 可达性 / Mesh，以负责任的方式做。**
若要真正的 P2P/漫游，优选经审查的成熟栈，而非把 NAT 穿透硬塞进未经审计的 C tunnel：
VPN 场景用 **WireGuard**，若确需真正的对等发现/Mesh 则用 **libp2p**。C tunnel 保留给
气隙实验室（它自述的用途）。并入阶段 0 的加固。

**阶段 4 — 持久状态与目标模型（Citadel 的安全一半）。**
coordinator 目前仅内存态、是单点故障。加入 append-only 日志（扩展现有 `memory.py`
SQLite）与 coordinator 崩溃恢复。用**可修订子目标的 DAG 目标模型**替换扁平的 `Plan`。
注意：**目前尚不需要 CRDT**——fleet 是单锁下的单写者，因此持久的单写者日志才是对的工具；
仅当确实出现真正的多写者复制需求时再引入 CRDT。

**阶段 5 — 一个真正的推理环路（"苏格拉底辩证"的安全解读）。**
给 agent 循环加入可选的自我批评 / 验证 pass 与重规划（现状是 plan 一次性、逐动作
`reasoning` 未被使用），并保持在现有的人类确认门控之内。这提升能力与可审计性，而不削弱
机主的控制。

**阶段 6 — 融合深层感知（把已有的读内存 / DIB 图像重建接进主循环）。**
`native/atlas` 的只读内存感知与 DIB 图像重建已经实现，但只活在独立二进制里；Python 侧的
`atlas.py` 孪生是死代码。给 agent 循环加一条桥（子进程调 `atlas_inspect`，或经回环
`POST /inspect`），在 AX 树为空、目标是自绘控件/CAD 画布时，用 §1.4 第 2 层（内存文本 +
重建图像）替代盲目截图。复用：`inspect_json.cpp` 的 `dibs[]`/`strings[]` 输出契约已就绪，
`atlas.py` 已镜像其只读拒斥策略。这把 secdogie 真正的感知强项落到主链路，且不触碰任何写原语。

### 乙部 — 不应构建的部分，及原因（各一句）

以下正是代码库已经拒绝的部分。建议保留这些墙。

- **用于击败反作弊 / 检测的内核级"拟人"Virtual HID 注入。** 那是为自动化/作弊做的检测
  规避；代码库刻意使用用户态输入，并把其抖动标注为"非指纹绕过"。安全替代：保留用户态执行
  与无障碍树路径（阶段 5 会公开地增强它）。
- **进程内存指针链 / AOB *写入*（内存作弊脚手架）。** 代码库是只读感知，所有写原语被拒。
  安全替代：保留只读检查用于正当定位。
- **削弱人类监督，让 agent 无人值守运行、经加密货币自负盈亏、并可凌驾于机主之上。** 安全
  替代：甲部的一切都让 agent 在受监督下*更强且更可问责*（签名动作、成本上限、持久审计），
  而非对机主更不负责。

---

## 5. 验证方式（如何核对上文每一项声明）

- **Tunnel 加密与内存安全：**
  `cmake -S tunnel -B tunnel/build && cmake --build tunnel/build && ctest --test-dir tunnel/build`；
  sanitizer + fuzz：`cmake -S tunnel -B tunnel/build-asan -DSDTP_SANITIZE=ON && cmake --build tunnel/build-asan && ctest --test-dir tunnel/build-asan`。
- **Python 测试套件：** 每个包 `pip install -e '<pkg>[extras]' && pytest`，另加
  `ruff check .`（对应 `.github/workflows/test.yml`）。
- **Native atlas：** 用 CMake 构建 `native/atlas` 并运行其 `ctest`
  （`test_memory_inspector`、`test_atlas`、`test_mct`、`test_chain`）。
- **复现"概念缺失"结论：**
  `grep -rin 'did:\|wallet\|micropay\|ledger\|crdt\|gossip\|dht\|hole.punch\|stun\|vector.clock' --include='*.py' --include='*.c' --include='*.cpp' --include='*.h' .`
  （预期无实质命中），以及 `grep -rn 'WriteProcessMemory\|CreateRemoteThread\|DriverEntry' .`
  （预期仅命中拒斥字符串/注释）。
- **确认无内核驱动 / 无守护进程：**
  `find . \( -name '*.sys' -o -name '*.inf' \)`（无）；无 `systemd`/`launchd` 单元；
  atlas 服务仅回环（`BindLoopback` → `127.0.0.1`）。
- **入口点：** `grep -rn '\[project.scripts\]' -A2 */pyproject.toml`。
