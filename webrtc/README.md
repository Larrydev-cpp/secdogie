# webrtc · 浏览器 P2P 数据通道

一个纯前端的 WebRTC 点对点数据传输模块，加上一个跑在 Cloudflare Workers 上的
WebSocket 信令网关。两个浏览器标签页（或两台机器）经网关交换 `offer` / `answer` /
`ice-candidate` 后，数据走 **DTLS 加密的 RTCDataChannel 直连**，网关看不到、也不经手
业务数据。

**只在用户显式操作时启动。** 模块导入时不开 socket、不建 `RTCPeerConnection`、不起
定时器；`startPeerConnection()` 在没有用户手势（点击）时直接拒绝，页面无法在加载时
静默联网。关掉页面或点「关闭节点」即全部拆除，没有任何后台常驻逻辑。

| 文件 | 作用 |
| --- | --- |
| [`signaling/worker.js`](signaling/worker.js) | 信令网关：Worker 入口 + `SignalingRoom` Durable Object |
| [`client/web_peer.js`](client/web_peer.js) | 浏览器端 ES 模块：协商、数据通道、断线检测 |
| [`index.html`](index.html) | 极简测试页：「启动节点」按钮 + 收发文本 + 日志 |
| [`wrangler.toml`](wrangler.toml) | Worker / Durable Object 配置 |
| [`tests/worker.test.js`](tests/worker.test.js) | 网关的 headless 单测（假 socket + 假时钟，纯 Node） |

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

浏览器侧的端到端链路（真 `wrangler dev` + 两个 Chromium 标签页）按上面「本地跑通」的
步骤手动验证。

## 已知边界

- **房间名就是唯一的门槛。** 网关不做身份认证，知道房间名的人都能占位（先到先得，满两
  人即拒）。公网部署时用难猜的随机房间名，或在 Worker 前面加 Cloudflare Access。
- **对端身份依赖信令的诚实。** DTLS 保证链路机密，但证书指纹是经网关交换的；一个恶意的
  信令服务器可以做中间人。需要端到端身份时，应在数据通道之上再做一层基于 DID 的认证
  （见 `identity/`）。
- **没有默认 TURN。** 默认只配公共 STUN，双方都在对称 NAT 后面时可能打不通；需要时通过
  `iceServers` 传入自己的 TURN。
- **一对一。** 一个房间只配对两个节点；多方需要多个房间或 mesh 扩展。
- 单条消息受 SCTP 上限约束（Chrome 通常 256 KiB），大文件需要调用方自行分片。
