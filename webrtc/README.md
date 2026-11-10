# webrtc · 浏览器 P2P 数据通道

一个纯前端的 WebRTC 点对点数据传输模块，加上一个跑在 Cloudflare Workers 上的
WebSocket 信令网关。两个浏览器标签页（或两台机器）经网关交换 `offer` / `answer` /
`ice-candidate` 后，数据走 **DTLS 加密的 RTCDataChannel 直连**，网关看不到、也不经手
业务数据。

**网格只跑在你自己的设备上。** 加一台设备就在浏览器里完成：在已有的设备上写一封请帖，
到新设备上打开、点「同意」，两边经 WebRTC 直连，两块屏幕对一下六位数，在邀请的那边点
「接纳」。见下面的[配对你自己的设备](#配对你自己的设备pairhtml)。

**只在用户显式操作时启动。** 模块导入时不开 socket、不建 `RTCPeerConnection`、不起
定时器；`startPeerConnection()` 在没有用户手势（点击）时直接拒绝，页面无法在加载时
静默联网。关掉页面或点「关闭节点」即全部拆除，没有任何后台常驻逻辑。

| 文件 | 作用 |
| --- | --- |
| [`signaling/worker.js`](signaling/worker.js) | 信令网关：Worker 入口 + `SignalingRoom` Durable Object |
| [`client/web_peer.js`](client/web_peer.js) | 浏览器端 ES 模块：协商、数据通道、断线检测 |
| [`index.html`](index.html) | 极简测试页：「启动节点」按钮 + 收发文本 + 日志 |
| [`pair.html`](pair.html) · [`pair.js`](pair.js) | 配对页：写请帖 / 同意 / 对码 / 接纳，中英双语 |
| [`client/pairing.js`](client/pairing.js) | 配对协议的浏览器实现，与 `transport/secdogie_transport/pairing.py` 逐字节一致 |
| [`client/keystore.js`](client/keystore.js) | 设备钥匙（不可导出的 `CryptoKey`）与接纳书存在 IndexedDB |
| [`mesh.html`](mesh.html) · [`mesh-page.js`](mesh-page.js) | 「我的设备」：上线 / 下线，其他设备的在线状态与时延，说一句 |
| [`client/mesh.js`](client/mesh.js) | 浏览器设备的网格运行时：每对设备一条链路、`link-hello`、名册互传、重连 |
| [`client/meshlink.js`](client/meshlink.js) | 会合房间、名册、`link-hello/v1`，与 `transport/secdogie_transport/meshlink.py` 逐字节一致 |
| [`client/frames.js`](client/frames.js) | 与 Python 相同的签名帧 `secdogie/direct/v1`、通道复用和防重放窗口 |
| [`ink.css`](ink.css) | 两个页面共用的样式（夜色墨蓝、宣纸色字、泥金） |
| [`wrangler.toml`](wrangler.toml) | Worker / Durable Object 配置 |
| [`tests/worker.test.js`](tests/worker.test.js) | 网关的 headless 单测（假 socket + 假时钟，纯 Node） |
| [`tests/pairing.test.js`](tests/pairing.test.js) | 配对协议对照 Python 金向量（`fixtures/vectors/pairing.json`） |
| [`tests/meshlink.test.js`](tests/meshlink.test.js) | 网格协议与签名帧对照 Python 金向量（`fixtures/vectors/meshlink.json`） |
| [`tests/e2e/pairing.e2e.cjs`](tests/e2e/pairing.e2e.cjs) | 两个真实 Chromium 经真实数据通道配对 |
| [`tests/e2e/mesh.e2e.cjs`](tests/e2e/mesh.e2e.cjs) | 三个真实 Chromium 上线组成网格（`npm run e2e` 两个都跑） |

## 本地跑通（两个标签页互发）

需要 Node 22+（当前 wrangler 的要求）和 Python 3（只用来起静态文件服务）。

```sh
cd webrtc
npx wrangler dev                 # 终端 1：信令网关 -> ws://localhost:8787/ws
python3 -m http.server 8080      # 终端 2：测试页   -> http://localhost:8080/
```

1. 在两个标签页打开 <http://localhost:8080/>，房间名保持一致（默认 `demo`）。
2. 标签页 A 点「启动节点」→ 状态 `waiting`；标签页 B 点「启动节点」→ 两边进入
   `connected`。
3. 在任一边输入文本发送，另一边日志出现 `← …`。

测试页的信令地址和房间也可以用查询参数预填：
`index.html?signal=wss://<worker>.workers.dev/ws&room=my-room`。ES 模块不能从
`file://` 加载，所以测试页必须经 HTTP 打开（`localhost` 算安全上下文，WebRTC 可用）。

## 配对你自己的设备（`pair.html`）

```sh
cd webrtc
npx wrangler dev                 # 终端 1：信令网关 -> ws://localhost:8787/ws
python3 -m http.server 8080      # 终端 2：配对页   -> http://localhost:8080/pair.html
```

1. **写请帖**（已在网格里的设备）：填网格名、这台设备的名字和信令地址，点「写请帖」。这台
   浏览器生成网格的主人钥匙（第一次时），签一封十分钟有效的请帖，并在一个一次性的信令房间
   里等着。把链接 `pair.html#invite=…` 发到新设备上。
2. **同意**（新设备）：打开链接，页面**离线**核验请帖，显示谁邀请、哪个网格、何时过期。
   点「同意加入」之前不联网；点了才生成这台设备自己的钥匙，并连上邀请方。
3. **对码**：数据通道打开后，两边各签一份 `carrier-bind/v1`，写明自己看到的两张 DTLS 证书指纹；
   两块屏幕显示同样的六位数。
4. **接纳**：在邀请的那边确认数字一样，点「一样，接纳」。它签一份 `device-admission/v1` 交给
   新设备；新设备核验后存下，也可以「保存接纳书」成文件。

**页面要在 `https://` 或 `localhost` 下打开。** 浏览器只在安全环境里提供签名用的 WebCrypto。
两台真设备之间用时，把 `pair.html`、`pair.js`、`client/` 放到任意 HTTPS 静态托管上（页面是
纯静态的，请帖在 URL 的 `#` 之后，托管方收不到），信令网关部署成 `wss://`（见下一节）。
在纯 http 的局域网地址上打开时，页面会直接说明原因。

**信令看得到什么。** 一次性房间名、两边的 SDP 与 ICE 候选（含 IP 地址）。看不到请帖、
钥匙、设备名和通道里的任何数据。

**为什么要对六位数。** 新设备的钥匙是刚生成的，邀请方事先不认识它：
- 新设备这一侧，请帖里写着邀请方的 DID，对方的 bind 必须由它签名，且指纹必须与自己看到的
  连接对上。信令中间人伪造不了，冒名的一方会被直接断开；
- 邀请方这一侧，一个在信令中间的人可以用自己的新钥匙冒充「新设备」。它和邀请方之间的
  六位数与真设备屏幕上的六位数不同，只有人眼对一下才能发现。所以接纳只在邀请方点，并且只在
  两边数字一致时点。

**节点怎么认这台设备。** 邀请方页面显示「这台设备的主人身份」（一个 DID）。把它加进各节点的
主人名单后，`own_devices(接纳书, owners=…, mesh=…)` 得出这个网格的设备，交给
`PeerBook(own=…)`。地址簿默认只收你自己的设备，公开层要 `public=True` 才开。

## 上线：我的设备（`mesh.html`）

配对之后，每台设备打开 `mesh.html`，点「上线」：

- 这台设备为名册里的每台设备各开一条链路，各在自己的会合房间里（由网格名和两个 DID 算出，网关不用改）。
- 数据通道打开后，两边先互发 `link-hello/v1`：签名覆盖自己的 DID、接纳书和这条连接的两张 DTLS 证书
  指纹。对方必须是这条链路对应的那台设备、是主人或持有主人签的接纳书，指纹必须交叉对上。在此之前
  什么都不读。
- 之后链路上只有 DID 签名的帧（与 UDP 上的 `secdogie/direct/v1` 相同），通道 `mesh/v1` 里传名册、
  一句话和测时延的 ping。新配对的手机只认识主人；连上主人后拿到名册，就自己去连其他在线的设备。
- 点「下线」或关掉页面，所有链路立刻关闭；没有后台。

架构和后续切片见 [`docs/PLAN-OWN-DEVICES.zh.md`](../docs/PLAN-OWN-DEVICES.zh.md)。目前的限制：
- 知道两个 DID 的人可以抢先占住它们的会合房间，只能让它们暂时连不上，冒充不了（S10 改成 DID 认证信箱后解决）；
- 还没有撤销（S6）；
- Python 节点还不走 WebRTC（S8）。

## 部署网关

```sh
cd webrtc
npx wrangler deploy              # -> wss://secdogie-signaling.<account>.workers.dev/ws
```

部署前在 `wrangler.toml` 的 `ALLOWED_ORIGINS` 里填上测试页所在的 origin（逗号分隔，
如 `https://peer.example.com`）；留空表示允许任意 origin，只适合本地开发。Durable
Object 用的是 SQLite 后端类，免费计划可用。

## 架构

```
标签页 A ──ws──┐                         ┌──ws── 标签页 B
               ├─ Worker ─ SignalingRoom ┤        （只转发 offer / answer / ice-candidate）
               │   （按 room 选 DO）       │
标签页 A ◀═════════ RTCDataChannel（DTLS/SCTP，可靠 + 有序）═════════▶ 标签页 B
```

**为什么用 Durable Object。** 普通 Worker 可能把同一房间的两个 WebSocket 放进不同的
isolate，模块级 `Map` 不共享，生产环境里路由会静默失败。`idFromName(room)` 把一个房间
的所有连接钉到同一个对象上，「Peer ID → 活跃 WebSocket」的内存映射就放在那里。除
闲置清扫用的 alarm 外不写任何存储。

**角色与协商。** 一个房间最多两个节点。后加入者收到 `welcome.peers` 里的对端 ID，
作为 offerer：`createDataChannel` → `createOffer` → `setLocalDescription` → 发
`offer`；先到者收到 `offer` 后 `setRemoteDescription` → `createAnswer` →
`setLocalDescription` → 发 `answer`。ICE 候选 trickle 发送；本地描述发出前收集到的
候选先排队，保证对端先拿到描述。只有 offerer 会重新发起协商，所以不会出现双方同时
offer（glare）；每个收到的 `offer` 都在一个全新的 `RTCPeerConnection` 上应答。

**数据通道。** `createDataChannel('secdogie-data', { ordered: true })`，不设
`maxRetransmits` / `maxPacketLifeTime`，即可靠有序模式。

**断线检测（`oniceconnectionstatechange`）。**

| ICE 状态 | 处理 |
| --- | --- |
| `disconnected` | 进入 `disconnected`，给 8 s 自愈宽限；若网关已通知对端离开，则立即判定断开 |
| `connected` / `completed` | 宽限期内恢复则回到 `connected` |
| `failed`，或宽限期超时 | 拆掉当前链路 → `waiting`；offerer 若对端仍在房间，按 1 s / 2 s / 4 s 退避最多重新 offer 3 次 |

另外：协商 30 s 内未打开数据通道视为失败（同样走重试）；数据通道 `close` 同样视为链路
丢失；信令断开时若直连已建立则保持直连，否则整体进入 `failed`。

## 客户端 API（`client/web_peer.js`）

```js
import {
  PeerState, startPeerConnection, closePeerConnection, sendData, onMessage, onStateChange,
} from './client/web_peer.js';

onStateChange((state, detail) => console.log(state, detail));
onMessage((data) => console.log('收到', data));        // string 或 ArrayBuffer

button.onclick = async () => {                          // 必须在用户手势里调用
  const { peerId } = await startPeerConnection({
    signalingUrl: 'ws://localhost:8787/ws',
    room: 'demo',
    // iceServers: [...],   默认 stun:stun.l.google.com:19302；需要跨对称 NAT 时加 TURN
    // log: (line) => ...,  诊断日志，默认 console.debug
  });
};

await sendData('hello');            // string / ArrayBuffer / TypedArray / Blob 原样发送，其他值按 JSON 文本发送
closePeerConnection();              // 幂等，随时可调
```

| 函数 | 说明 |
| --- | --- |
| `startPeerConnection(opts)` | 连接网关并加入房间；网关接纳后 resolve `{peerId, room}`。无用户手势、已在运行、URL/房间名非法时 reject |
| `closePeerConnection()` | 关闭数据通道、`RTCPeerConnection` 和信令 socket，状态回到 `idle` |
| `sendData(payload)` | 经数据通道发送；通道未打开时 reject；超过 SCTP 单消息上限时抛 `RangeError`；发送缓冲超过 1 MiB 时等待排空 |
| `onMessage(cb)` / `onStateChange(cb)` | 注册监听，返回取消函数 |
| `currentDescriptions()` | 当前连接的本地与对端 SDP（只读），配对页从中取 DTLS 指纹；两者都没有时为 `null` |
| `createPeerLink()` | 另一个独立的对端（自己的信令 socket、房间、链路和监听），方法同上：`start` / `close` / `send` / `onMessage` / `onStateChange` / `currentDescriptions`。网格每条链路一个 |

状态：`idle` → `signaling` → `waiting` → `negotiating` → `connected`，以及
`disconnected`（ICE 暂失）和 `failed`（放弃；可重新 `startPeerConnection()`）。

## 信令协议与网关限额

所有帧都是 JSON 文本。网关只转发三种类型，`from` 由网关按发送方会话盖章（客户端自带的
`from` 被忽略），`payload` 按 `RTCSessionDescriptionInit` / `RTCIceCandidateInit` 的字段
白名单重建，多余字段一律丢弃。

| 方向 | 消息 |
| --- | --- |
| 客户端 → 网关 | `{type: 'offer' \| 'answer' \| 'ice-candidate', to, payload}`；`{type: 'ping'}`（心跳，不转发） |
| 网关 → 客户端 | `welcome {peerId, peers}`、`peer-joined {peerId}`、`peer-left {peerId}`、转发的 `{type, from, payload}`、`error {code, message}` |

| 限额 | 值 | 超限 |
| --- | --- | --- |
| 单帧 JSON 大小（UTF-8） | 16 KiB | 关闭，1009 |
| 速率（令牌桶） | 突发 40 帧，持续 10 帧/s | 关闭，1008 |
| 二进制帧 | 不接受 | 关闭，1003 |
| 每房间节点数 | 2 | 第三个连接收到 `room-full` 后关闭，4001 |
| 闲置（无任何帧，含心跳） | 60 s（客户端每 20 s ping） | alarm 每 30 s 清扫，关闭，4002 |
| 非法 JSON / 类型 / payload / 目标 | — | 回 `error`，不转发 |
| 房间名 | `[A-Za-z0-9_-]{1,64}` | HTTP 400 |
| Origin | `ALLOWED_ORIGINS` 非空时校验 | HTTP 403 |

## 测试

```sh
cd webrtc && npm test            # = node --test，无需安装依赖
```

单测用假 socket 和假时钟直接驱动 `SignalingRoom`：欢迎/加入通知、三种类型的定向转发与
`from` 盖章、字段白名单、非法类型/payload/JSON/目标、超大帧（含多字节字符）、二进制帧、
速率限制、满房、断开通知、闲置清扫、以及入口的 health / upgrade / origin / 房间名校验。
CI（`.github/workflows/test.yml` 的 `webrtc-tests`）每次推送都跑。

配对协议的单测对照 Python 签出的金向量：Python 签的每份声明在浏览器里都能核验，同样的输入
签出同样的字节，中间人、过期、篡改、改投各有各的失败原因。

```sh
cd webrtc && npm run e2e         # 需要 playwright 包和 Chromium
```

端到端测试用两个独立的浏览器上下文（各自的 IndexedDB 和钥匙），测试自己扮演信令网关，数据
通道走本机回环。它断言：点「同意」之前新设备没有任何连接；两块屏幕的六位数一致；接纳后新设备
持有的接纳书能核验；任何一边点「不一样」都不接纳；被改过的请帖在离线时就被拒绝；在纯 http 的
局域网地址上页面说明原因。

网格的端到端测试用三个浏览器上下文（主人、手机、平板），先经配对页配好，再各自打开「我的设备」。它断言：
点「上线」之前没有任何连接；手机经主人的名册找到平板并直连，三台组成全互连；一台设备说的话另两台都
收到；下线或关掉页面的设备在其他设备上显示为不在线。CI 的 `webrtc-e2e` 每次推送都跑两个测试。

浏览器侧接真网关（真 `wrangler dev` + 两个标签页）的链路按上面「本地跑通」的步骤手动验证。

## 已知边界

- **房间名就是唯一的门槛。** 网关不做身份认证，知道房间名的人都能占位（先到先得，满两
  人即拒）。公网部署时用难猜的随机房间名，或在 Worker 前面加 Cloudflare Access。
- **对端身份依赖信令的诚实（`web_peer.js` 本身）。** DTLS 保证链路机密，但证书指纹是经网关
  交换的；一个恶意的信令服务器可以做中间人。配对页在数据通道之上补了这一层：双方签名的
  bind 覆盖两张证书指纹，再加上人眼对六位数（见上文）。其他用法要端到端身份时，照此在通道
  之上做基于 DID 的认证。
- **没有默认 TURN。** 默认只配公共 STUN，双方都在对称 NAT 后面时可能打不通；需要时通过
  `iceServers` 传入自己的 TURN。
- **一对一。** 一个房间只配对两个节点；多方需要多个房间或 mesh 扩展。
- 单条消息受 SCTP 上限约束（Chrome 通常 256 KiB），大文件需要调用方自行分片。
