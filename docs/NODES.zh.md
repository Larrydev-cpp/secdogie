# 获取推理网格节点

英文版：[NODES.md](NODES.md)。

推理网格（[`inference/`](../inference/README.md)）只运行在机主自愿运行它的机器上。本文说明网格获得节点的三种方式、每种方式如何保持零信任，以及明确不做的事。术语见 [`GLOSSARY.md`](GLOSSARY.md)。

| 来源 | 由谁运行 | 角色 | 如何被信任 | 如何找到对端 |
| --- | --- | --- | --- | --- |
| 运营方控制的机器 | 运营方，在自有或租用的服务器上 | 任意（serve、forward、model、coordinator、broker） | DID 在 mesh 白名单上；受限角色还须在对应角色名单上 | 静态 `--peer` 条目，或 rendezvous 记录 |
| 自动发现 | —（已列名节点之间如何互连） | — | 从不扩大信任：只拨号已在 mesh 名单上的 DID | rendezvous 查询 + 泛洪的心跳 |
| 浏览器志愿者 | 打开 `symbiont/mesh.html` 并点击 **Join** 的人 | 仅转发 | 运营方为该浏览器 DID 签发的准入（`admission/v1`，最长 30 天） | 拨号加入包（join bundle）所列入口节点的 WebRTC 大厅 |

## 1. 运营方控制的机器

每个节点是一个前台进程 `secdogie-inference run`，不安装任何东西。就绪时输出一行 JSON，日志以 JSON 行写到 stderr；收到 SIGTERM / SIGINT，或自身 DID 被 Master 集合吊销时，以 0 退出。

```sh
secdogie-identity genkey node.key                 # Ed25519 身份（0600）
# trust/ 下六个白名单文件，每个 DID 一行 `authorized_did = did:key:…`：
#   mesh.allow users.allow deciders.allow coordinators.allow brokers.allow operators.allow
secdogie-inference run --identity node.key --trust-dir trust/ \
    --role serve --role forward --listen 0.0.0.0:7960 --public-host node1.example.net \
    --bootstrap rendezvous.json [--masters masters.conf --revocations revocations.jsonl]

secdogie-inference run ... --role model --lease-key lease.x25519 --backend mypkg.upstream:call
secdogie-inference run ... --role broker --keys keys.json --assignments assignments.json
secdogie-inference run ... --role coordinator --standby DID
```

- 每个信任名单都必须给出；缺少任何一个，节点拒绝启动。
- `--backend MODULE:FUNCTION` 是模型提供节点调用上游 API 的入口；不硬编码任何厂商。
- `--heartbeat-every` 同比缩放所有计时器（故障检测、腿超时、建议）。

## 2. 自动发现

自动发现让已列名节点无需手写对端地址即可找到并连接彼此：

1. **引导。** rendezvous 服务器是带 rendezvous 角色的中继，可在任一白名单机器上运行：

   ```sh
   secdogie-relay --identity relay.key --authorized trust/mesh.allow --rendezvous \
       --listen 0.0.0.0:7946 --public-host relay.example.net --record-out rendezvous.json
   ```

   以 `--bootstrap rendezvous.json` 启动的节点向它登记本地与反射端点。请求均经 DID 签名并防重放，见 `transport/secdogie_transport/rendezvous.py`。
2. **按需解析。** 节点第一次需要向某个 DID 发送时才查询其端点：
   - 最多同时 8 个查询，失败的查询 5 秒后重试；
   - 发送时找不到路径会触发重新查询，因此晚登记的对端也能连上。
3. **了解网格。** 建立第一条链路后，泛洪的心跳会把每个存活 mesh DID 及其角色填入各节点的链路状态库。
4. **维持度数**（`secdogie_inference/discovery.py`）。每个节点与中转节点（serve 或 forward）保持 `--min-degree`（3）到 `--max-degree`（6）条见证链路。
   - 与协调者或密钥代理的链路不承载路由，不计入。
   - 候选须存活、未被排除、在 mesh 名单上，且不是被准入的浏览器。
   - 优先低负载，平局按每节点哈希分散。
   - 20 秒内未成为见证链路的拨号会被放弃；已确认的链路发现模块从不拆除。
   - 协调者的修复建议仍在其上生效。

自动发现从不拨号 mesh 名单之外的 DID。可达不等于可信。

## 3. 浏览器志愿者

浏览器标签页可以作为被准入、**仅转发**的节点把连接能力借给网格（[`symbiont/src/mesh/`](../symbiont/src/mesh/)）。流程涉及双方：

1. **入口节点。** 运营方以 `--signaling wss://…/ws` 运行一个或多个节点（信令用 [`webrtc/signaling/`](../webrtc/signaling/) 中未改动的 Cloudflare Worker）。每个入口节点保持一个 WebRTC 大厅房间，`"sdl-" + sha256("lobby|DID|salt")[:40]`。
2. **身份。** 志愿者打开 `mesh.html`（由 `symbiont` 构建产物提供），点击 **Create identity**。页面生成一把不可导出的 Ed25519 密钥，只存在于该标签页中，并显示其 DID；志愿者把 DID 发给运营方。
3. **准入与加入包。** 运营方签发一份仅转发、会过期的准入，并把它包进加入包：

   ```sh
   secdogie-inference admit --operator-key op.key --subject did:key:… --ttl 86400 > admission.json
   secdogie-inference bundle --trust-dir trust/ --admission admission.json \
       --signaling wss://signal.example.net/ws --entry DID_S --entry DID_P > bundle.json
   ```

   若准入无法用所列运营者验证，或入口节点不在 mesh 名单上，`bundle` 会拒绝。
4. **加入。** 志愿者粘贴加入包并点击 **Join**。必须由用户操作触发，横幅会显示节点状态。

随后：

- 浏览器拨号每个入口节点的大厅并发起 offer，SDP 中已包含全部 ICE 候选。
- 双方交换签名的 `rtc-hello/v1`，把各自 DID 与本连接的 DTLS 指纹绑定，信令网关因此无法充当中间人。浏览器的 hello 携带其准入。
- 入口节点验证准入并发布；准入泛洪，每个全节点都接受该 DID。无论其心跳声称什么，每个全节点都只记录 `forward` 角色（`LinkStateDB.forward_only`）。
- 浏览器签发见证其邻居的 `heartbeat/v2` 心跳，因此它的链路算作见证链路，路由可以经过它。

浏览器节点做什么、从不做什么：

| 做 | 从不做 |
| --- | --- |
| 泛洪心跳、建议、故障报告、事件、重置、租约吊销与准入，每种都按必须签发它的角色核验 | 决定路由、受理请求、作为模型应答、持有租约或 API 密钥 |
| 仅在签名决策把它放在路径上时，向模型节点转发 `req`、向回转发 `resp` / `nack` | 在持有未被重置的叛变事件时转发非锁定决策，或为被锁存的决策者、向被锁存的下一跳转发 |
| 应答 `dir?` 与 `inc?`，每条新链路建立时向入口节点索取目录 | 存储任何东西：不用 IndexedDB、localStorage 或 cookie（`symbiont` 红线） |
| 只在标签页打开期间、点击之后运行 | 在后台运行，或在标签页关闭后重连 |

上限：最多 8 条链路，单帧最大 64 KB，每个邻居持续 100 帧/秒（突发 300），排队 512 帧；超出部分丢弃并计数。

### 浏览器节点的局限

- **一个标签页，一个 DID。** 密钥不落盘，新标签页即新 DID，需要新的准入。准入本就短期有效（默认 1 天，最长 30 天）。
- **加入包不签名。** 包内任何内容都只按其能证明的程度被采信：
  - 准入必须能用包内运营者名单验证，且必须指向本浏览器；
  - 入口节点必须在包内 mesh 名单上，DTLS 握手还会再核验一次。
- **吊销由全节点执行。** 浏览器不评估 Master 吊销。被吊销的浏览器 DID 会被每个全节点拒绝（准入簿外层的 `RevocationFilter`），正是这一点把它移出所有路由。
- **锁定判断偏保守。** 浏览器保存由受信 mesh 节点或用户签名的事件，并读取其证据所指的目标；它不重跑 `mutiny.py` 的证据验证器，因此可能比全节点拒绝得更多，但绝不更少。
- **转发者能看到提示词。** 与所有转发者相同：传输加密保护每一跳，但到模型提供节点的端到端封装尚未实现。

## 端口与 NAT

| 组件 | 传输 | 默认 | 说明 |
| --- | --- | --- | --- |
| `secdogie-inference run` | UDP | `0.0.0.0:7960` | `--public-host` 是对外宣告的地址；在 NAT 之后时加 `--relay-record` 中继（`FailoverTransport`） |
| `secdogie-relay`（中继 / rendezvous） | UDP | `--listen`，例如 `0.0.0.0:7946` | `--record-out` 写出供节点引导的记录 |
| 信令网关 | WSS | 443（Cloudflare Worker） | 每个房间配对两个对端；只传 SDP，从不传网格帧 |
| WebRTC 数据通道 | UDP（ICE） | 临时端口 | 除非 `--ice-server`（节点）或 `ice_servers`（加入包）给出 STUN/TURN，否则只有主机候选；两端都在对称 NAT 之后时需要 TURN |

## 明确不做的事

以下都不是获取节点的方式，本仓库也不实现其中任何一项：

- **不做隐蔽招募。** 不扫描主机，不在未经机主同意的机器上利用漏洞或安装程序，不建僵尸网络。
- **不做后台驻留。** `run` 是不安装任何东西的前台进程；浏览器节点随标签页结束。
- **不扩大信任。** 每个节点都在信任名单上或持有运营方准入；准入只授予转发角色，并会过期。
- **不做流量混淆或规避检测。** 帧是明文、签名的协议对象；WebRTC 载体是标准 DTLS。

## 测试

| 范围 | 测试 |
| --- | --- |
| 在模拟网格上发现到目标度数；解析器从不阻塞 | `inference/tests/test_discovery.py` |
| 真实 rendezvous 加六个 `run` 进程互相发现并应答 `ask`；杀掉一个转发者后绕行 | `inference/tests/test_run.py` |
| 准入规则、泛洪、仅转发角色、Master 吊销优先于准入 | `inference/tests/test_admission.py` |
| `RtcWire`：hello 绑定、不受信与已准入对端、同时拨号、经数据通道的一次请求 | `inference/tests/test_rtc.py` |
| 黄金向量：浏览器签出的心跳与 hello 与 Python 逐字节一致 | `symbiont/tests/mesh.test.ts`、`inference/tests/test_mesh_vectors.py` |
| 端到端：Playwright Chromium 加入 Python 网格；请求经 S → 浏览器 → P 得到应答；关闭标签页后浏览器从所有路由中移除 | `inference/tests/test_browser_node.py`（无 `symbiont` 工具链或 Playwright 时跳过） |
