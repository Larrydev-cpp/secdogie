# symbiont · 浏览器侧共生运行时

SecDogie 在浏览器里的运行时：**双重苏格拉底门**（Gate 1 语义对齐 + Gate 2 密码学放行）、
**注意力感知的打断调度**、以及建在 Rust-WASM 状态图（[`../graph/`](../graph/)）之上的
**拓扑映射**。TypeScript，零运行时依赖（Ed25519 与 SHA-256 用 WebCrypto），Node 22 可直接跑
`.ts`，浏览器用 `tsc` 产物。

与现有 Python 部分**逐字节兼容**：规范化 JSON、`secdogie/action-authorization/v1`
令牌、`secdogie/dialogue/v1` 信封、`socratic.py` 的审视规则——由
[`fixtures/vectors/`](../fixtures/vectors/) 的金向量锁住（Python 生成，Rust/TS 必须一致）。

## 三个子系统

| 子系统 | 模块 | 做什么 |
| --- | --- | --- |
| A 状态图与拓扑 | `graph/wasm.ts` · `graph/topology.ts` · `sandbox/*` | Worker 里受限抓取公开页面 → WASM 词法路由适配器产出**候选状态** → 由 agent 密钥签名的只追加增量 → WASM 引擎自己验签入库；have/want 反熵 |
| B 注意力调度 | `attention/*` | 由焦点信号算 `InterruptionBudget`（flow / engaged / gap / away / suppressed）；提案队列在心流时压住、空隙时浮现、敏感场景遮住 |
| C 双重苏格拉底门 | `gate1/*` · `gate2/*` · `gates/pipeline.ts` | Gate 1 一次问一个问题、没对齐不出计划；对齐后 WASM 确定性预计算动作；高风险动作经内联气泡由操作员 Ed25519 签名放行 |

视图层：`stream/*`——常驻的「意识流」（群体与拓扑状态在最上面，从不是空白页）、按意图折叠的
叙述（从不逐步列机械操作）、Gate 1 / Gate 2 的**内联气泡**（从不是模态框）。

## 硬约束是怎么落地的

- **没有模态框、不抢焦点、不注入标记**：`tests/purity.test.ts` 扫描 `src/`，出现
  `.focus(`、`showModal`、`role="dialog"`、`alert/confirm/prompt`、`innerHTML`、`eval`、
  `credentials: 'include'`、`redirect: 'follow'`、`localStorage` 等即失败。
- **Gate 2 内联但不草率**：气泡完整列出签名所承诺的六个字段；出现后 0.8 秒内「批准」无效
  （防止刚冒出的气泡被误点）；敏感场景下内容被遮住且不可批准；签名前再审一次；
  浏览器里操作员密钥只在**真实的用户点击**中解锁（`navigator.userActivation`），页面上的
  脚本（包括 agent 自己）拿不到；签出的令牌先本地自验再发出。
- **没有隐式的「是」**：Gate 1 问题过期 = 否；Gate 2 挑战过期 = 节点拒绝；队列没有紧急插队。
- **两把钥匙**：agent 的 DID 签图增量、发挑战；操作员的 DID 签放行。issuer 用
  `verifyAuthorization` 核对动作哈希、subject、时效与受信操作员后才放行。
- **只用候选状态，不编造迁移**：Gate 1 只能在图里已有的状态中选择；图里没有就问，不猜。
  `plan_action` 对图里不存在的状态直接拒绝。
- **抓取边界**（`sandbox/policy.ts`）：只 https、精确 origin 白名单（空 = 什么都不抓）、
  `credentials: 'omit'`、`referrerPolicy: 'no-referrer'`、正文与时间有上限、
  `redirect: 'manual'`（只报告，目标在白名单内才作为候选，不跟随）。对方不允许跨域读取
  （CORS）时如实报告失败，**不走代理绕过**。策略在 Worker 初始化时固定，之后不能放宽。

### 焦点信号的边界（刻意的）

需求里的「全系统打字 CPM」「OS 窗口上下文」浏览器拿不到；要拿到就得装全局键盘钩子——
那和键盘记录器只有一线之隔，属于严禁清单。所以：

- `BrowserFocusProvider` 只看**本应用自己的文档**：可见性、是否有焦点、按键 / 点击**次数**
  （监听器不接收事件对象，读不到按了什么）；
- 系统侧信号只能由**已授权**来源（例如节点的无障碍层给出的粗粒度应用类别、系统空闲时长 API）
  以聚合样本经 `ManualFocusProvider` 送入。这一来源尚未实现。

## 跨语言规范化（Python 浮点一致性）

签名覆盖的是 Python `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
的字节。JS 自带的 JSON 在四处不同，`core/canon.ts` 逐一处理：

| 情况 | Python | JS 默认 | 这里 |
| --- | --- | --- | --- |
| 整值浮点 | `1759740120.0` | `1759740120` | 浮点必须显式 `pyFloat()`；裸 `number` 只表示整数 |
| 指数 | `1e+16`、`1e-05` | `10000000000000000`、`0.00001` | 照搬 `repr`：`decpt <= -4 或 > 16` 用指数，至少两位带符号 |
| 键序 | 按码点 | 按 UTF-16 | `compareCodePoints` |
| 大整数 | 精确 | 超过 2^53 丢精度 | `bigint`（对话头的 `timestamp_ns` 约 1.76e18） |
| `NaN`、重复键、孤立代理项 | 输出 / 后者覆盖 / 编码时才报错 | — | 一律拒绝 |

`parseLossless` 按 `json.loads` 的词法规则区分 int 与 float，所以 Python 签名的对象重新编码后
得到的正是被签名的字节。图增量里**不含浮点**，内容地址不受浮点规则影响。

## 运行

```sh
npm ci
npm run build:wasm     # cargo build ../graph 为 wasm32，复制到 wasm/
npm run typecheck
npm test               # node --test，含与 Python 金向量的逐字节比对与四段式 journey
npm run build          # tsc -> dist/
python3 -m http.server 8080   # 打开 http://localhost:8080/ 看离线 demo
```

`tests/journey.test.ts` 用真实的 WASM 引擎走完 **FlowState（排队）→ AttentionGap →
Gate 1 苏格拉底澄清 → Gate 2 内联签名**，并覆盖探问过期、反驳删除拓扑、敏感场景遮蔽、签名过期。

## 尚未做（如实）

- 与 Python 节点之间的真实传输桥接（W3）：包形状已兼容，但目前两侧在同一页面内经回环连接；
- 操作员密钥只在内存中（IndexedDB 持久化与浏览器 DID 属于 W1），浏览器操作员 DID 需先登记进节点的 operators；
- 记忆候选（`MemoryCandidatePacket`）的内联确认气泡；
- 真正的系统窗口（需要桌面外壳）；目前沙箱 = 专用 Worker + 页面内非模态侧栏；
- 系统侧焦点信号来源（见上）。
