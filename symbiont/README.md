# symbiont · 操作员页面

SecDogie 的操作员界面：一个**透明的单栏对话画布**。页面一打开就经 WebRTC 数据通道挂到你电脑上
常驻的 `secdogie-node`，你说一句话它就去做；需要你点头时，问题和确认以内联卡片出现在对话里。
所有机械细节——DID、哈希、指纹、房间、字节数、规格里的消息名——只进 DevTools（`[symbiont]` 开头的
`console.debug` 行），页面上只有人话。

TypeScript，零运行时依赖（Ed25519、SHA-256、HMAC 都用 WebCrypto），Node 22 可直接跑 `.ts`。

## 页面长什么样

- 居中单栏 `min(720px, 100% - 32px)`，背景 `#fafafa`，只有浅色。
- 头部只有一个 7px 状态点和一句标签：「共生体: 已连接」（英文「Symbiont: Connected」）；连接中是
  琥珀色呼吸，连不上是灰色；鼠标悬停 / 读屏给出一句解释，例如「后台一直在运行，关掉这个页面也不会打断它。」
- 底部是悬浮的毛玻璃输入条（`backdrop-filter: blur(18px) saturate(140%)`，不支持时退回纯白），
  适配 iOS 安全区；回车发送，Shift+回车换行；有任务在跑时旁边出现「停下」。
- **Gate 1**：一张温和的问句卡，例如「刚才找到了两个相似的地方，帮你确认一下是这个吗？」，答案是卡片
  上的胶囊；没有胶囊时，输入框会接管回答（只在问题出现时输入框是空的才接管）。
- **Gate 2**：一张毛玻璃确认卡，一句话说清会做什么，例如
  「这一步会直接注销旧账号（在 docs.example.com），做完就没法恢复了。确认要继续吗？」，下面是签名所覆盖
  内容的原文引用，按钮只有「取消」「批准」；「批准」淡入 0.8 秒后才可点。
- 没有模态框、不抢焦点、没有步骤日志、没有「今天做什么？」这类空白问候、没有地址输入框。

截图由 CI 的 `operator-page` 任务生成（中文 / 英文、桌面 / 手机），作为产物上传。

## 交付物在哪里

| 规格里的名字 | 实现 |
| --- | --- |
| 单栏 HTML/CSS | `index.html`（静态外壳，无内联代码或样式）、`app.css`、`src/ui/{dom,render,presence,main}.ts` |
| `ATTACH_OPERATOR` | `src/net/attach.ts`，由 `ui/main.ts` 在 `DOMContentLoaded` 时启动；传输是 `webrtc/client/web_peer.js` |
| dialogue/v1 数据通道 | 唯一一条可靠有序的 `secdogie-data` 通道上，ChannelMux 的 `dialogue/v1` 通道（`net/mux.ts`、`net/session.ts`） |
| `ADD_GOAL` | `client/operator_client.ts`：你说的话变成 `control` 包 `op=add_goal` |
| `CURRENT_STATUS` | 节点在 HELLO 后发的 `status: idle / running / waiting for operator`（`voice/status.ts` 读成状态点、环境句和「停下」） |
| `GRAPH_SNAPSHOT` | 守护进程目前没有 Merkle DAG（见「尚未做」）；`state_snapshot`（AX 窗口结构）照常验签占序号后丢弃，只记 DevTools；`graph/v1` 通道名已预留 |
| 对话式 i18n 字典 | `src/voice/{zh,en}.ts`，同一个 `Dict` 类型，编译器保证两边键一致；`voice/locale.ts` 按 `navigator.languages` 选：先遇到中文用中文，先遇到英文用英文，都没有用中文 |

## 结构

| 目录 | 做什么 |
| --- | --- |
| `net/` | 和节点逐字节一致的线路（`fixtures/vectors/link.json`）：`frame.ts` direct/v1 帧 + 1024 宽重放窗口；`mux.ts`；`session.ts` 分片 / 确认 / 重传 / 心跳（后台标签页收到消息时顺带补心跳）；`linkauth.ts` W1 与配对；`keystore.ts` 两把不可导出的密钥 + 配对记录（**唯一**允许用 IndexedDB 的文件）；`attach.ts` 挂载状态机 |
| `client/` | `CoreLink`（页面唯一面对的接口）、`OperatorClient`（真实实现）、`Conversation`（只存组织好的人话） |
| `voice/` | 所有文案；Gate 1 / Gate 2 / 状态的措辞；`visible.ts` 让控制字符和格式字符可见 |
| `demo/` | `#demo`：页面内的脚本化节点，说同样的签名包，跑的是真实客户端 |
| `ui/` | 渲染、状态点、启动 |
| `gate1/` `gate2/` `core/` | 规则、Gate 2 签名流程、规范化 JSON、Ed25519、信封、`trace.ts`（**唯一**允许用 console 的文件） |
| `graph/` `sandbox/` `attention/` | Rust-WASM 状态图门面、受限抓取 Worker、注意力调度：作为库保留，页面目前不用 |

## 挂载与配对

1. **已配对**：加入节点的常驻房间（网关地址在构建时固定，链接改不了）。节点先陈述自己（W1：用 DID 签名
   它看到的 DTLS 指纹），页面用配对记录里的节点 DID 和自己看到的指纹核对——**核对通过之前页面什么都不说，
   连自己的 DID 都不说**——再回自己的陈述，然后才走 dialogue/v1。断了就抖动退避重连；同一浏览器里最新打开
   的标签接管；网关房间持续满员才算「在别处使用中」。
2. **未配对、从配对链接打开**：链接里只有节点 DID 和一次性秘密（没有地址、没有 ICE 服务器；带了别的字段
   直接拒绝），用它派生的一次性房间见面；核对节点后发出签名、带 HMAC 的 hello，两边显示同样的 12 位
   核对码；你在电脑终端答 y、在页面点「连接」，节点才登记；收到节点签名的回执后才写配对记录。
3. **都没有**：不发起任何连接。

节点签名的「不再登记」会让页面忘掉配对；其他拒绝不会。页面关闭或刷新不发 BYE：节点把没答完的问题和
确认留到各自过期，页面回来、说 HELLO 时再原样发一次。

## 硬约束是怎么落地的

- **签名覆盖的内容不删不改**：路径、输入的文字、计划正文原样显示，控制字符 / 双向覆盖 / 零宽字符显示成
  ⏎ ⇥ ⟨U+202E⟩ 这样的记号；太长就不让批准，而不是截断（`voice/consent.ts`、`voice/visible.ts`）。
- **Gate 2 的句子只从签名字段生成**：节点给的风险说明不可信，只进 DevTools；严重程度只会往重了判
  （`high_risk`、删除键、删除类字眼一律按「做完就没法恢复」）。
- **批准只在你的点击里签**：仅当节点在配对时登记了这个浏览器的操作员密钥（终端第二个问题答 y）；
  否则「批准」置灰并如实说明。签之前再审一次，签完本地自验。这对页面里的脚本是纵深防御，**不是**对
  同源代码（扩展、被攻破的部署）的边界——见 [SECURITY.md](../SECURITY.md)。
- **「已发出」不等于「已完成」**：没收到确认的签名显示「不确定」，直到节点报告这个目标的结果。
- **页面里没有机械细节**：`tests/ui.test.ts` 在动作哈希、挑战编号、签名、各个 DID、元素编号里放哨兵值，
  断言它们不出现在 DOM 文本和属性里，同时断言签名覆盖的内容（含 64 位十六进制、`did:key`、换行、
  双向覆盖、零宽字符）原样且可见。`tests/browser/check_page.mjs` 在真实 Chromium 里再扫一遍。
- **纯净**：`tests/purity.test.ts` 禁止 `focus()`、模态、`innerHTML`、`eval`、媒体采集、`autofocus`、
  除 keystore 外的存储、除 trace 外的 console；页面外壳没有内联代码、没有地址输入框、只有两个固定按钮。
- **安全响应头**：`npm run site` 生成 `site/_headers`（Cloudflare Pages）：CSP（`script-src 'self'`，
  `connect-src` 只放行构建时的网关，`frame-ancestors 'none'`）、`X-Frame-Options: DENY`、COOP、
  `Referrer-Policy: no-referrer`、`Permissions-Policy`；`scripts/serve.mjs` 在本地发同样的头。页面在
  iframe 里时什么都不挂载。

## 运行

```sh
npm ci
npm run build:wasm     # graph/ 的 WASM（只有库和它的测试用）
npm run typecheck
npm test               # node --test：链路金向量、挂载状态机、会话、客户端、语气、渲染、纯净、journey
npm run build          # tsc -> dist/，并把 web_peer.js 复制到 dist/vendor/
SECDOGIE_SIGNAL_URL=wss://<gateway>/ws npm run site   # -> site/（可部署；不设则是本地开发版）
npm run serve          # http://127.0.0.1:8770/  （#demo 看演示）
```

本地连真实节点：`cd ../webrtc && npx wrangler dev`（网关在 127.0.0.1:8787），节点
`secdogie-node run ... --webrtc-signal ws://127.0.0.1:8787/ws`，另开终端
`secdogie-node pair ... --webrtc-signal ws://127.0.0.1:8787/ws --ui http://127.0.0.1:8770/`，
在浏览器里打开它在终端给出的链接。

浏览器测试（需要 Playwright + Chromium）：`node tests/browser/check_page.mjs`（页面本身），
`python -m pytest tests/browser`（Chromium ↔ 带 aiortc 对端的真实节点：配对、W1、提问、刷新后批准 Gate 2）。

## 尚未做（如实）

- **规格 1B**：在 Cloudflare Worker 沙箱里抓取、自动发现 Session Anchor 并签名并入 DAG；节点侧的 Merkle DAG、
  反熵与覆盖网——因此 GRAPH_SNAPSHOT 目前没有真实数据。
- **只用 AX/UIA 感知**：节点跑目标仍用视觉循环；页面和数据通道不传任何像素。
- **真实节点上的「两个相似的地方」**：节点在目标不符时直接拒绝该动作，不发起探问；这张卡目前只在 `#demo`。
  真实节点的 Gate 2 句子只能说清具体动作（「按下 Delete」「在屏幕上点一下」），因为签名动作里还没有元素名。
- **libsodium 加密数据通道**：WebRTC 链路上用 v1 帧 + DTLS + W1。
- WebAuthn 硬化 Gate 2、手机配对二维码、`unpair` 与房间轮换。
- 记忆候选只进 DevTools，不在页面上出现（留在节点隔离区，不会生效）。
