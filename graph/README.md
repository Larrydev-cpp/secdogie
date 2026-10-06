# graph · 状态图内核（Rust，原生 + wasm32）

`secdogie-graph`：内容寻址、只追加的状态图；have/want 反熵；保守的词法路由适配器；
`secdogie/action-authorization/v1` 字段集上的确定性动作预览。编译成 wasm32 后**没有任何导入**——
碰不到网络、DOM 和时钟，只评判交给它的字节。TypeScript 侧见 [`../symbiont/`](../symbiont/)。

依赖只有 `sha2`、`ed25519-dalek`（`verify_strict`）、`base64`。

## 图增量 `secdogie/state-graph-delta/v1`

```text
payload  = {"type": "secdogie/state-graph-delta/v1", "author": did,
            "parents": [cid, ...],   升序、去重、≤16
            "lamport": int,          = 1 + max(父 lamport)，根为 1
            "ops": [op, ...]}        1..=256
envelope = payload + {"signer": did, "sig": b64}     signer 必须等于 author
cid      = sha256(canonical(payload)).hex()           与 citadel/journal 的 entry_hash 同法
```

只有三种 op，全部是「加」：

| op | 字段 | 含义 |
| --- | --- | --- |
| `add_state` | `origin`（规范 https origin）、`route`（规范化路径）、`query_keys`（排序的键名，不含值） | 一个状态；键 = sha256(canonical({type:"secdogie/state-key/v1", origin, route, query_keys})) |
| `add_reference` | `from`、`to`、`via`（a/area/form/link/redirect）、`method`、`fields`（字段**名**） | 页面标记**提到了**另一个状态——不是迁移，也不声称走过 |
| `add_observation` | `state`、`content_hash`、`byte_len` | 读到的字节按哈希引用，内容本身不进图 |

**v1 拒绝任何删除 / 墓碑语义**：其他 op 名一律拒收。视图是所有 op 的增长集合并集，与到达顺序无关、
天然收敛。增量里**没有浮点**。

`DagStore::insert` 的顺序：验签（Ed25519 `verify_strict`）→ signer == author → 作者在受信集合内
（集合不能为空）→ 严格 schema → 父节点齐了再核对 lamport。父节点未到的增量先验签后作为孤儿暂存
（有上限），父节点到达时级联提升。存储满了就拒绝写入，**不驱逐**。

## 反熵

```text
graph_have   {heads}
graph_want   {cids, have_heads}     带上请求方的前沿
graph_deltas {envelopes}            所请求的节点及其不在对方前沿祖先中的全部祖先，父在前，单批 ≤256
```

带前沿的 want 让常见情况一轮收敛；还缺的父节点在入库后再发 want。传输与对端认证由传输层负责
（WebRTC DTLS / DID 绑定的 UDP 会话），每条增量仍由 `insert` 独立验证。

## 词法路由适配器（`route.rs`）

只从 `a/area[href]`、`link[rel=canonical|alternate|next|prev]`、`form[action]`（只记 method 与字段名；
含密码框的表单整体跳过）、第一个同源的 `base[href]` 提取；跳过注释与 `script/style/template/textarea/noscript`
等原始文本元素；不读 `on*`、`data-*`、`srcset`、内联脚本。只接受 https；userinfo、IDN、IPv6 字面量、
反斜杠等一律拒绝并按原因计数，从不「修复」成别的东西。站外链接只计数。

## 规范化 JSON（`canon.rs`）

与 Python `canonical()` 逐字节一致，包括 `repr(float)` 的排版与 int/float 区分；`NaN`、重复键、
孤立代理项一律拒绝。见 [`../fixtures/vectors/canonical.json`](../fixtures/vectors/canonical.json)。

## FFI

JSON 进、JSON 出，手写 C ABI（`sd_alloc`、`sd_free`、`sd_graph_new`、`sd_graph_call`、`sd_graph_drop`、
`sd_route_scan`、`sd_canonical`），返回 `(ptr << 32) | len`。错误一律作为 `{"ok": false, "error": ...}`
返回，不陷入。

## 构建与测试

```sh
cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
rustup target add wasm32-unknown-unknown
cargo build --release --target wasm32-unknown-unknown
```

`tests/vectors.rs` 读取 `fixtures/vectors/`（Python 参考实现生成）：规范化字节、Python 签名的令牌与
对话信封、图增量的 CID 与拒绝理由必须一致。
