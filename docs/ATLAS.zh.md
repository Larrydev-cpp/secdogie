# Atlas：双层感知、只读句柄、Cloudflare 隧道

英文版：[ATLAS.md](ATLAS.md)。

Python 决策核：[`agent/secdogie_agent/atlas.py`](../agent/secdogie_agent/atlas.py)。
Windows 原生孪生模块：[`native/atlas/`](../native/atlas/)。

## 内存回退（UIA 未命中）

无障碍树为空时（自绘 CAD chrome、UIA COM 失败），下一步**不是猜像素**。`InspectPid`：

1. 对操作者和目标做 `TOKEN_QUERY`（`QueryProcessToken`）。SYSTEM / TrustedInstaller / PPL / 更高完整性返回 `denied-escalate` 或 `denied-protected`。墙不会复制更高令牌来填补差距。
2. 只用 `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION` 调用 `OpenProcess`。
3. `VirtualQueryEx` / `/proc/<pid>/maps`：跳过 `PAGE_GUARD`、`PAGE_NOACCESS`、可执行页、文件映射、lsass/csrss/PPL。
4. `ReadProcessMemory` / `process_vm_readv` 按 64 KiB 分块（MSVC `__try`）。失败的页跳过，绝不写入目标。
5. 抽出 UTF-16LE / UTF-8 字符串、`BITMAPINFOHEADER`、MZ/PE、JSON 状态块。
6. 与 last-known UIA 节点按名称融合（`HybridNode`：`uia` / `memory` / `fused`）。
7. **返回前关闭句柄并放下 SeDebug。** 没有常驻句柄，没有常驻特权，没有 `WriteProcessMemory`。

UIA 树本身是真正的 `ControlViewWalker`（GetFirstChild / GetNextSibling），有深度和节点上限——不是空的 `Walk()`。

命令行（模型控制终端）：

```
native/atlas/atlas_inspect --list
native/atlas/atlas_inspect --pid <n>
native/atlas/atlas_inspect --self --token
```

## 双层循环

1. **主定位**走操作系统无障碍树（Windows UI Automation / AT-SPI / AX）——PID、hwnd、包围盒、AutomationId。用 `--desktop-ax` 打开。macOS 原生 MCT 抓**前台应用**的全部窗口（标题 / 描述 / 值 / 包围盒）；辅助功能未授权时回退 `CGWindowList`。SIP 挡住 `task_for_pid` **不会**让 inspect 整单失败——AX/CGWindow 树已经够用。实循环优先 `click_element` / 原生 Invoke，而不是在缩小后的截图上猜像素。
2. **核验**是动作前后控件区域的 pixel-diff（agent 循环里的 `screen.changed_ratio`；原生路径是 `atlas.changed_ratio` / C++ `PixelDiff`）。没有可见突变 → 重试 → 失败。无突变的步骤永远不会被记成成功。
3. **突变按操作系统分开。** Windows：UIA Invoke，再走文档化的 `SendInput`。macOS：**只**走 `AXPress` / `AXConfirm`——`click_element` **不会**改写成 `left_click`（Darwin 上的 pyautogui 就是 Quartz HID / `CGEventPost`）。Linux：原生不做突变。无突变的步骤永远不会被记成成功。
4. **`click_element` 在可重试集合里。** 未命中按**同一投递路径**重试（再 Invoke / AXPress）。只有 Windows 才会改写成 `left_click`。不会把生的 `click_element` 交给 `backend.execute`（那不是后端动词）。
5. UIA 本帧未命中时，回退到**上一帧**已解析的控件树（last-known），而不是在同一份空快照上再 Find 一次。名称 / AutomationId / role 匹配大小写不敏感。
6. 实循环 `loop.py` 同样保留 last-known `element_targets`：空的无障碍帧不会抹掉列表，模型上一帧拿到的 `eN` 仍能解析。`click_element` 重试走 `_deliver_action`（Invoke / AXPress；Windows 可改写成 `left_click`，macOS 绝不）。
7. `axtree.find_elements` 的 name / AutomationId 是大小写不敏感的精确匹配（不是子串），与 Atlas `find_control` 对齐。

CAD 画布和自绘 chrome 仍回退到视觉。这是故意的：树上是空的，像素是回退，不是默认。

## 只读进程句柄

`OpenProcess` 只允许 `PROCESS_VM_READ | PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION`。`PROCESS_VM_WRITE`、`PROCESS_VM_OPERATION`、`PROCESS_CREATE_THREAD`、`PROCESS_ALL_ACCESS` **拒绝，不静默收窄**——幻觉出来的写请求不能被当成「我们帮你去掉危险位了」。

## 权限墙

- **SYSTEM** 是从*已经是管理员*的令牌上走文档化的 `CreateProcessAsUser`，启动时用 `--allow-elevated-command` 白名单。不是 UAC 绕过。见 [`elevate.py`](../agent/secdogie_agent/elevate.py)。
- **`NT SERVICE\TrustedInstaller` 模拟被拒绝。** 窃取服务身份令牌是安全边界绕过。`elevate.try_impersonate_trusted_installer()` 与 `PrivilegeManager::TryImpersonateTrustedInstaller()` 永远返回 `refused-identity`。
- **Anti-EDR 被拒绝。** 没有脱钩、藏句柄、PPL 绕过。操作者指定的 GUI PID 上的只读 `VirtualQueryEx` + `ReadProcessMemory` 是感知，不是规避。目标令牌只 `TOKEN_QUERY`；SYSTEM / TI / 更高完整性返回 `denied-escalate`。`ScopedPrivilege` 析构时放下 SeDebug。

## 感知回退

空的当前 UIA 树**不会覆盖** last-known。C++ `HybridControlLoop::last_` 只在本帧 `controls` 非空时更新；Python `keep_tree` / `coalesce_element_targets` 与 Web `keepTree` 同一契约。

execute 抛错或返回失败码记成 Failed，不会被当成「无突变再试一次」。Pixel-diff 宽高/长度不一致记为 100% 改变（避免用 `min` 长度低估突变）。

## Cloudflare Tunnel

生产可达性优先 **命名 Cloudflare Tunnel**，而不是未经审计的自定义 UDP `tunnel/`：

```sh
# Linux / macOS
native/atlas/scripts/setup-cloudflare-tunnel.sh secdogie-atlas atlas.example.com

# Windows
native/atlas/scripts/setup-cloudflare-tunnel.ps1 -Name secdogie-atlas -Hostname atlas.example.com
```

配置模板：[`native/atlas/config/cf_tunnel_config.json`](../native/atlas/config/cf_tunnel_config.json) 与 [`tunnel/cloudflare/`](../tunnel/cloudflare/)。`cloudflared` 只出站；主机名前面放 Cloudflare Access。不要提交凭据 JSON。

自定义 `tunnel/` 留给隔离实验网。见 [`SECURITY.md`](../SECURITY.md)。

## 编译原生模块

```sh
cmake -S native/atlas -B native/atlas/build
cmake --build native/atlas/build --target atlas_test
ctest --test-dir native/atlas/build --output-on-failure
```

非 Windows 上库仍能编过；Win32 入口返回 `Unsupported`，决策核测试仍然通过。
