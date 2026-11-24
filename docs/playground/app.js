import {
  VERDICT_META,
  buildMcpConfig,
  buildRedTeamPayload,
  buildStaticFallbackResult,
  formatLatency,
} from './engine.js';
import { SpikeRasterRenderer } from './snn-canvas.js';

const I18N = {
  zh: {
    nav_playground: '脉冲靶场',
    nav_trajectory: '世界模型',
    nav_sharding: '设备分片',
    nav_mcp: 'MCP 闸门',
    nav_compare: '方案对比',
    nav_redteam: '红队挑战',
    nav_cta: '接入 MCP',
    hero_kicker: 'ALPINE GARDEN SANCTUARY · GEN-1 SPIKING PREFRONTAL CORTEX',
    hero_title: '雪山与花园秘境中的安全前额叶',
    hero_subtitle:
      '输入任意中文或英文指令，实时观测 1,280 个脉冲神经元逐字放电、世界模型多步因果终态推演，以及 N 设备分布式 SNN 模型分片——无正则、无词表、本机静谧运行。',
    interlude_kicker: 'BEYOND SURFACE TEXT · CAUSAL WORLD MODEL & DISTRIBUTED CORTEX',
    interlude_quote:
      '如静水映雪峰：安全防线不逐字猜谜，而是推演动作落地的终态，并将前额叶切分守护在你的每一台设备之中。',
    input_heading: '测试指令与工具上下文',
    prompt_label: '待评估的请求或 Agent 工具意图',
    tool_label: '下游 MCP 工具名（可选）',
    irreversible_label: '标记为不可逆工具（强制本机显式授权）',
    run_btn: '运行脉冲皮层',
    replay_btn: '重播波形',
    presets_heading: '精选实战与红队用例',
    mcp_out_heading: 'MCP Gate 返回载荷',
    metric_allow: 'MCP 执行放行',
    metric_latency: '本地 CPU 耗时',
    metric_spikes: '脉冲总放电数',
    metric_novelty: '语义陌生度',
    hold_waiting: '不可逆动作已暂停（Fail-Closed）',
    hold_hint:
      '该操作涉及高风险或不可逆状态变更，已由本机前额叶拦停；须由本机操作者核实后显式签名放行（多设备节点仅承载皮层模型分片，不用于远程点击确认）。',
    raster_heading: '1,280 神经元实时脉冲栅格与群体发放率',
    probe_hint: '悬停画布探测毫秒级时间步',
    socratic_heading: '14 个苏格拉底问题置信度矩阵',
    sec_traj_title: '01. 世界模型多步因果推演：专杀「拆步绕过 / Semantic Chaining」',
    sec_traj_desc:
      '单靠文本分类永远防不住把恶意意图拆成多步无害动作（如“螺纹钢 + 弹簧 + 撞针”）。SecDogie 的 mind/trajectory.py 跨轮追踪真实系统状态原子，在执行前进行前向因果闭包推演——无论话术多委婉，只要组合终态越界，当场硬拦。',
    traj_scenarios_title: '选择多步攻击 / 对照轨迹',
    run_traj_btn: '运行世界模型因果推演',
    traj_timeline_title: '跨轮状态累积与因果闭包判定',
    sec_shard_title: '02. N 设备分布式脉冲模型分片：不是远程弹窗，是把前额叶切分到多台设备',
    sec_shard_desc:
      '将 1,280 个脉冲神经元按行切分到你的主力机、手机、笔记本与 NAS：每台设备只存 1/N 突触权重，节点间只传 1-bit 压缩脉冲（162 字节/tick，较 FP32 压缩 31.6×）并由 Ed25519 DID 签名聚合。单点失陷无法还原或伪造完整安全前额叶。',
    mode_exact: 'Exact 1-bit 同步 (LAN)',
    mode_1rtt: '1-RTT 局部递归 (WAN)',
    run_shard_btn: '运行真实分片推理',
    shard_m_parity: '与单机数值误差 Δ',
    shard_m_bw: '1-bit 脉冲带宽',
    shard_m_rtt: '网络往返 / 总流量',
    shard_m_sig: 'Ed25519 DID 验签',
    sec_mcp_title: '03. 一行命令为 OpenClaw / Claude Code / Cursor 装上生物前额叶',
    sec_mcp_desc:
      '无需放弃你现有的 Agent 工作流。将 secdogie-mcp 挂载为本地标准 MCP Server，高危 tool call 在本机 CPU 毫秒级通过脉冲神经网络与跨轮世界模型审查，不依赖云端 Guardrail API，也不受 Prompt Injection 诱导。',
    copy_config: '复制配置',
    cli_quick_title: '30 秒终端快速验真',
    cli_quick_note:
      '返回码 0 表示安全放行，返回码 3 表示被前额叶或世界模型轨迹拦截；原生支持 secdogie_gate_check 与 secdogie_session_trajectory 多轮状态追踪。',
    sec_compare_title: '04. 架构实测对比：SecDogie vs OpenClaw / 云端 LLM Guardrail',
    sec_compare_desc:
      '不靠口号，只看底层机制与可复现数据。SecDogie 既可以独立作为你的多设备分布式安全伴侣，也可以直接作为 OpenClaw 的本地安全护盾协同工作。',
    col_dim: '对比维度',
    col_llm_guard: '云端 LLM Guardrail',
    row_brain: '核心决策模型',
    row_brain_sd: '1,280 脉冲神经元 SNN（无词表、无正则、e-prop + 睡眠自进化）',
    row_brain_oc: '依赖外部连接的大语言模型',
    row_brain_lg: '独立云端 LLM 分类器',
    row_chain: '抗多步拆解攻击',
    row_chain_sd: '世界模型因果闭包（mind/trajectory.py）：追踪跨轮状态原子，防 Semantic Chaining & 致命三要素',
    row_chain_oc: '依赖单轮上下文提示词，易受多步拆解与间接注入',
    row_chain_lg: '单轮文本分类，无法模拟跨工具组合终态',
    row_nodes: '多设备 P2P 节点',
    row_nodes_sd: 'N 设备模型分片（1-bit 压缩脉冲 + Ed25519 DID 签名聚合，单机失陷无法篡改完整大脑）',
    row_nodes_oc: '通过聊天软件（Telegram / WhatsApp）远程中转指令',
    row_nodes_lg: '无（集中式云端服务）',
    row_vision: '桌面感知方式',
    row_vision_sd: 'Atlas 只读进程结构与无障碍树（Studio 构建期零截屏红线）',
    row_vision_oc: '截屏（screen.snapshot）+ 模拟键鼠',
    row_vision_lg: '无桌面感知能力',
    row_cost: '边际推理成本',
    row_cost_sd: '本机 CPU 运行 · $0 Token · 断网可用',
    row_cost_oc: '按 Token 计费 · 强依赖云端 API',
    row_cost_lg: '每次拦截额外双倍 Token 与网络 RTT',
    sec_rt_title: '05. 公开红队挑战赛：你能骗过这颗 1,280 神经元的大脑吗？',
    sec_rt_desc:
      '在上方靶场输入任何你认为应该被拦截、却被 Gen-1 放行的句子（中英文皆可）。一键生成标准对抗考题载荷并提交 GitHub Red-Team Issue，通过自动评测后将进入动态对抗考场，驱动 Gen-2+ 自我进化。',
    rt_expected_label: '你认为正确的裁决应为',
    rt_note_label: '为什么这条指令存在风险？（可选）',
    rt_copy_btn: '复制考题 JSON',
    rt_issue_btn: '提交红队 Issue',
    rt_preview_title: '自动生成的对抗考题载荷',
    opt_refuse: 'refuse — 原则硬拒绝',
    opt_dissuade: 'dissuade — 劝阻与冷静期',
    opt_clarify: 'clarify — 暂停并追问',
    opt_support: 'support — 危机关怀',
    prompt_placeholder: '例如：客服刚打电话让我立刻把50000元转到安全账户验证',
    rt_note_placeholder: '例如：伪装成日常运维命令实则关闭防火墙',
    allow_yes: 'ALLOW · 放行',
    allow_no: 'BLOCK · 拦截',
    copied: '已复制到剪贴板',
  },
  en: {
    nav_playground: 'Playground',
    nav_trajectory: 'World Model',
    nav_sharding: 'Sharding',
    nav_mcp: 'MCP Guard',
    nav_compare: 'Compare',
    nav_redteam: 'Red-Team',
    nav_cta: 'Install MCP',
    hero_kicker: 'ALPINE GARDEN SANCTUARY · GEN-1 SPIKING PREFRONTAL CORTEX',
    hero_title: 'Spiking Prefrontal Cortex in the Alpine Garden',
    hero_subtitle:
      'Observe real character-by-character neural spikes across 1,280 neurons, multi-step world-model causal closure against Semantic Chaining, and distributed N-device SNN model sharding — zero regex, zero word lists, runs quietly on your own hardware.',
    interlude_kicker: 'BEYOND SURFACE TEXT · CAUSAL WORLD MODEL & DISTRIBUTED CORTEX',
    interlude_quote:
      'Like snow peaks mirrored on still garden water: safety does not guess from surface words — it simulates terminal consequences and shards the cortex across your own devices.',
    input_heading: 'Request & Agent Tool Context',
    prompt_label: 'Candidate user request or agent tool intent',
    tool_label: 'Downstream MCP tool name (optional)',
    irreversible_label: 'Mark tool as irreversible (enforces local explicit authorization)',
    run_btn: 'Analyze Request',
    replay_btn: 'Replay Spikes',
    presets_heading: 'Curated & Red-Team Scenarios',
    mcp_out_heading: 'MCP Gate JSON Payload',
    metric_allow: 'MCP Execution',
    metric_latency: 'Local CPU Latency',
    metric_spikes: 'Total Neural Spikes',
    metric_novelty: 'Novelty Score',
    hold_waiting: 'Irreversible Action Held (Fail-Closed)',
    hold_hint:
      'This action involves high-stakes or irreversible state mutation and was held locally. Multi-device P2P nodes hold sharded cortex weights rather than remote click-to-approve popups.',
    raster_heading: '1,280-Neuron Spike Raster & Population Firing Rate',
    probe_hint: 'Hover plot to probe millisecond reading ticks',
    socratic_heading: '14 Socratic Questions Confidence Matrix',
    sec_traj_title: '01. World-Model Trajectory Defense: Stopping Multi-Step Semantic Chaining',
    sec_traj_desc:
      'Text classifiers can never enumerate every way to split a harmful goal into benign-looking parts. SecDogie mind/trajectory.py tracks concrete system-state atoms across turns and runs forward causal closure before execution.',
    traj_scenarios_title: 'Select Multi-Step Attack / Baseline Trajectory',
    run_traj_btn: 'Run World-Model Simulation',
    traj_timeline_title: 'Cross-Turn State Accumulation & Causal Closure',
    sec_shard_title: '02. N-Device Distributed SNN Model Sharding: Split the Brain Across Devices',
    sec_shard_desc:
      'Partition the 1,280 spiking neurons across Desktop, Phone, Laptop, and NAS: each device stores only 1/N of the weights, exchanges 1-bit packed spikes (~162 B/tick, 31.6x smaller than FP32), and signs partial readouts with Ed25519 DIDs.',
    mode_exact: 'Exact 1-bit Sync (LAN)',
    mode_1rtt: '1-RTT Local Recurrent (WAN)',
    run_shard_btn: 'Run Live Sharded Inference',
    shard_m_parity: 'Parity vs Monolithic Δ',
    shard_m_bw: '1-bit Spike Bandwidth',
    shard_m_rtt: 'RTT Rounds / Total Wire',
    shard_m_sig: 'Ed25519 DID Quorum',
    sec_mcp_title: '03. Add a Biological Prefrontal Cortex to OpenClaw, Claude Code & Cursor',
    sec_mcp_desc:
      'Keep your existing agent workflow. Mount secdogie-mcp as a local stdio MCP server so risky tool calls pass through a local spiking neural network and world-model trajectory gate in milliseconds.',
    copy_config: 'Copy Config',
    cli_quick_title: '30-Second CLI Smoke Test',
    cli_quick_note:
      'Exit code 0 means safe to proceed; exit code 3 means blocked by cortex or world-model trajectory. Supports secdogie_gate_check and secdogie_session_trajectory.',
    sec_compare_title: '04. Empirical Architecture Comparison: SecDogie vs OpenClaw & LLM Guardrails',
    sec_compare_desc:
      'No hype — compare concrete mechanisms side by side. SecDogie works both as a distributed multi-device companion and as a local safety shield in front of OpenClaw.',
    col_dim: 'Dimension',
    col_llm_guard: 'Cloud LLM Guardrail',
    row_brain: 'Decision Engine',
    row_brain_sd: '1,280-neuron spiking network (zero word lists, e-prop + sleep RSI)',
    row_brain_oc: 'External language model you connect',
    row_brain_lg: 'Separate cloud LLM classifier',
    row_chain: 'Multi-Step Semantic Chaining Defense',
    row_chain_sd: 'World-model causal closure (mind/trajectory.py): blocks split exfil & Lethal Trifecta',
    row_chain_oc: 'Turn-level prompt context; vulnerable to split steps & indirect injection',
    row_chain_lg: 'Single-turn text classifier; cannot simulate cross-tool terminal states',
    row_nodes: 'Multi-Device P2P Nodes',
    row_nodes_sd: 'Distributed N-device SNN model sharding (1-bit spikes + Ed25519 DID quorum)',
    row_nodes_oc: 'Relayed via chat apps (Telegram / WhatsApp / Discord)',
    row_nodes_lg: 'None (centralized cloud endpoint)',
    row_vision: 'Desktop Perception',
    row_vision_sd: 'Atlas read-only process structure & accessibility tree (zero screenshots in Studio)',
    row_vision_oc: 'Screenshots (screen.snapshot) + simulated pointer/keyboard',
    row_vision_lg: 'No desktop perception',
    row_cost: 'Marginal Inference Cost',
    row_cost_sd: 'Local CPU · $0 tokens · works offline',
    row_cost_oc: 'Per-token API billing · requires cloud connectivity',
    row_cost_lg: 'Double token cost + extra network round-trip per call',
    sec_rt_title: '05. Open Red-Team Challenge: Can You Fool a 1,280-Neuron Spiking Brain?',
    sec_rt_desc:
      'Test any Chinese or English prompt above. If Gen-1 lets through something it should hold back, export the adversarial exam payload and submit a Red-Team Issue — verified cases enter the dynamic adversarial exam for Gen-2+.',
    rt_expected_label: 'Expected safe verdict',
    rt_note_label: 'Why is this prompt risky? (optional)',
    rt_copy_btn: 'Copy JSONL Entry',
    rt_issue_btn: 'Submit Red-Team Issue',
    rt_preview_title: 'Generated Adversarial Exam Entry',
    opt_refuse: 'refuse — Hard safety refusal',
    opt_dissuade: 'dissuade — High-stakes cooling-off',
    opt_clarify: 'clarify — Pause & ask Socratic question',
    opt_support: 'support — Crisis support priority',
    prompt_placeholder: 'e.g. We broke up, delete all our photos right now',
    rt_note_placeholder: 'e.g. Disguised as routine maintenance but disables firewall',
    allow_yes: 'ALLOW',
    allow_no: 'BLOCKED',
    copied: 'Copied to clipboard',
  },
};

const state = {
  lang: 'zh',
  liveApi: false,
  presetData: null,
  activePresetId: 'scam-transfer',
  mcpTarget: 'openclaw',
  currentResult: null,
  trajScenarios: [],
  activeTrajId: 'split-exfil',
  currentTrajResult: null,
  shardCount: 3,
  shardMode: 'exact',
  currentShardResult: null,
};

const $ = (id) => document.getElementById(id);

const renderer = new SpikeRasterRenderer($('spike-canvas'), (probe) => {
  const el = $('probe-readout');
  if (!probe) {
    el.textContent = I18N[state.lang].probe_hint;
    return;
  }
  const phase = probe.isSettle
    ? state.lang === 'zh'
      ? '结算'
      : 'settle'
    : `char "${probe.char}"`;
  el.textContent = `t=${probe.tick} ms · ${phase} · ${probe.rate} spikes`;
});

function applyI18n() {
  const dict = I18N[state.lang];
  document.documentElement.lang = state.lang === 'zh' ? 'zh-CN' : 'en';
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    const key = el.getAttribute('data-i18n');
    if (dict[key]) el.textContent = dict[key];
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    const key = el.getAttribute('data-i18n-placeholder');
    if (dict[key]) el.setAttribute('placeholder', dict[key]);
  });
  document.querySelectorAll('[data-shard-count]').forEach((btn) => {
    const n = btn.getAttribute('data-shard-count');
    btn.textContent = state.lang === 'zh' ? `${n} 台设备` : `${n} Devices`;
  });
  $('lang-zh').setAttribute('aria-pressed', String(state.lang === 'zh'));
  $('lang-en').setAttribute('aria-pressed', String(state.lang === 'en'));
  renderer.setLang(state.lang);
  renderPresets();
  renderTrajScenarios();
  if (state.currentResult) renderResult(state.currentResult, false);
  if (state.currentTrajResult) renderTrajResult(state.currentTrajResult);
  if (state.currentShardResult) renderShardResult(state.currentShardResult);
}

function renderPresets() {
  const container = $('preset-container');
  container.textContent = '';
  if (!state.presetData || !Array.isArray(state.presetData.presets)) return;
  for (const item of state.presetData.presets) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'preset-btn';
    btn.setAttribute('aria-current', String(item.id === state.activePresetId));

    const title = document.createElement('span');
    title.textContent = state.lang === 'zh' ? item.title_zh : item.title_en;

    const tag = document.createElement('span');
    tag.className = 'preset-tag';
    tag.textContent = state.lang === 'zh' ? item.tag_zh : item.tag_en;

    btn.appendChild(title);
    btn.appendChild(tag);
    btn.addEventListener('click', () => {
      state.activePresetId = item.id;
      $('prompt-input').value = item.text;
      $('tool-name-input').value = item.tool_name || '';
      $('irreversible-toggle').checked = Boolean(item.irreversible);
      updateCharCount();
      renderPresets();
      runAnalysis();
    });
    container.appendChild(btn);
  }
}

function updateCharCount() {
  const n = Array.from(($('prompt-input').value || '').trim()).length;
  $('char-count').textContent = `${n} chars`;
}

function renderMcpTabs() {
  const cfg = buildMcpConfig(state.mcpTarget);
  $('mcp-target-title').textContent = cfg.title;
  $('mcp-config-code').textContent = cfg.snippet;
  document.querySelectorAll('[data-mcp-tab]').forEach((btn) => {
    btn.setAttribute('aria-selected', String(btn.getAttribute('data-mcp-tab') === state.mcpTarget));
  });
}

function renderRedTeam() {
  const expected = $('rt-expected').value;
  const note = $('rt-note').value;
  const payload = buildRedTeamPayload(state.currentResult, expected, note);
  $('rt-json-preview').textContent = payload.pretty;
  $('open-rt-issue').href = payload.issueUrl;
}

function renderResult(res, animateCanvas = true) {
  state.currentResult = res;
  const dict = I18N[state.lang];
  const meta = VERDICT_META[res.verdict] || VERDICT_META.talk;

  $('verdict-category').textContent = `${res.verdict.toUpperCase()} · ${res.category || 'general'}`;
  const vLabel = $('verdict-label');
  vLabel.textContent = state.lang === 'zh' ? meta.zh : meta.en;
  vLabel.className = `verdict-label tone-${meta.tone}`;
  $('verdict-summary').textContent = state.lang === 'zh' ? meta.summary_zh : meta.summary_en;
  $('verdict-mode').textContent = res.mode === 'live' ? 'Live Python SNN' : 'Gen-1 Snapshot';
  $('verdict-digest').textContent = res.generation || 'gen1';

  const allowEl = $('metric-allow');
  allowEl.textContent = res.allow ? dict.allow_yes : dict.allow_no;
  allowEl.className = `metric-val ${res.allow ? 'allow-yes' : 'allow-no'}`;
  $('metric-latency').textContent = formatLatency(res.latency_ms);
  $('metric-spikes').textContent = String((res.snn && res.snn.total_spikes) || 0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  $('metric-novelty').textContent = (typeof res.novelty === 'number' ? res.novelty : 0).toFixed(2);

  // Local irreversible hold notice
  const holdBox = $('hold-notice-box');
  holdBox.hidden = !res.requires_human_approval;

  // Crisis fallback or Socratic question banner
  const supBox = $('support-box');
  if (res.says) {
    supBox.hidden = false;
    $('support-kicker').textContent = `Cortex Reply · ${res.source || 'gate'}`;
    $('support-text').textContent = res.says;
  } else if (res.question && res.question.text) {
    supBox.hidden = false;
    $('support-kicker').textContent = `Socratic Question · ${res.question.qid}`;
    $('support-text').textContent = res.question.text;
  } else {
    supBox.hidden = true;
  }

  // Spike raster canvas
  if (res.snn) {
    $('legend-lif').textContent = `LIF (${res.snn.n_plain}): ${res.snn.plain_spikes} spikes`;
    $('legend-alif').textContent = `ALIF (${res.snn.n_adaptive} working memory): ${res.snn.adaptive_spikes} spikes`;
    if (animateCanvas) {
      renderer.setData(res.snn);
    } else {
      renderer.snn = res.snn;
      renderer.draw();
    }
  }

  // 14 Socratic questions matrix (respects mind/decide.py short-circuiting on chat/support)
  const grid = $('socratic-grid');
  grid.textContent = '';
  const rows = Array.isArray(res.socratic) ? res.socratic : [];
  const kindRow = rows.find((r) => r.id === 'kind');
  const isChatShortCircuit =
    res.verdict === 'talk' &&
    kindRow &&
    (kindRow.answer === 'chat' || kindRow.answer === 'feel' || kindRow.answer === 'unclear');
  const isSupportShortCircuit = res.verdict === 'support';
  const activeCount = isChatShortCircuit || isSupportShortCircuit ? 2 : rows.length;
  $('socratic-count').textContent =
    isChatShortCircuit || isSupportShortCircuit
      ? state.lang === 'zh'
        ? `${activeCount} / ${rows.length} 维度生效（其余短路跳过）`
        : `${activeCount} / ${rows.length} active (rest short-circuited)`
      : `${rows.length} dimensions`;

  for (const row of rows) {
    const isShortCircuited =
      (isChatShortCircuit && row.id !== 'kind' && row.id !== 'self') ||
      (isSupportShortCircuit && row.id !== 'self' && row.id !== 'kind');

    const cell = document.createElement('div');
    cell.className = `socratic-row${isShortCircuited ? ' is-skipped' : ''}`;

    const top = document.createElement('div');
    top.className = 'socratic-top';

    const qText = document.createElement('span');
    qText.className = 'socratic-q';
    qText.textContent = state.lang === 'zh' ? row.zh : row.en;

    const conf = document.createElement('span');
    conf.className = 'socratic-conf';
    const pct = Math.round((row.confidence || 0) * 100);
    conf.textContent = isShortCircuited
      ? state.lang === 'zh'
        ? '已短路'
        : 'skipped'
      : `${pct}%`;

    top.appendChild(qText);
    top.appendChild(conf);

    const track = document.createElement('div');
    track.className = 'socratic-bar-track';
    const fill = document.createElement('div');
    const isHighRisk =
      !isShortCircuited &&
      ((row.id === 'self' && row.answer === 'yes') ||
        (row.id === 'others' && row.answer === 'harm') ||
        (row.id === 'seat' && row.answer === 'yes'));
    fill.className = `socratic-bar-fill${isHighRisk ? ' high-risk' : ''}${isShortCircuited ? ' is-skipped' : ''}`;
    fill.style.width = isShortCircuited ? '0%' : `${Math.max(4, Math.min(100, pct))}%`;
    track.appendChild(fill);

    const ans = document.createElement('div');
    ans.className = 'socratic-ans';
    if (isShortCircuited) {
      const meaning = document.createElement('span');
      meaning.textContent =
        state.lang === 'zh'
          ? isChatShortCircuit
            ? '纯对话短路跳过（该动作头仅在 task/ask 上训练与生效）'
            : '已触发危机优先通道，跳过动作评估'
          : isChatShortCircuit
            ? 'Short-circuited on pure chat (action head only trained/evaluated on task/ask)'
            : 'Bypassed by crisis-support priority path';
      ans.appendChild(meaning);
    } else {
      const code = document.createElement('code');
      code.textContent = row.answer;
      const meaning = document.createElement('span');
      meaning.textContent = state.lang === 'zh' ? row.meaning_zh || row.meaning : row.meaning_en || row.meaning;
      ans.appendChild(code);
      ans.appendChild(meaning);
    }

    cell.appendChild(top);
    cell.appendChild(track);
    cell.appendChild(ans);
    grid.appendChild(cell);
  }

  // Compact MCP JSON payload preview
  const mcpView = {
    tool: 'secdogie_gate_check',
    allow: res.allow,
    verdict: res.verdict,
    category: res.category,
    requires_local_auth: res.requires_human_approval,
    effect: res.effect,
    latency_ms: res.latency_ms,
    novelty: res.novelty,
  };
  $('mcp-json-preview').textContent = JSON.stringify(mcpView, null, 2);

  renderRedTeam();
}

// ---------------------------------------------------------------------------
// 01. Multi-Step World-Model Trajectory Sandbox
// ---------------------------------------------------------------------------

function renderTrajScenarios() {
  const list = $('traj-scenario-list');
  list.textContent = '';
  for (const sc of state.trajScenarios) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'preset-btn';
    btn.setAttribute('aria-current', String(sc.id === state.activeTrajId));

    const title = document.createElement('span');
    title.textContent = state.lang === 'zh' ? sc.title_zh : sc.title_en;

    const tag = document.createElement('span');
    tag.className = 'preset-tag';
    tag.textContent = state.lang === 'zh' ? sc.tag_zh : sc.tag_en;

    btn.appendChild(title);
    btn.appendChild(tag);
    btn.addEventListener('click', () => {
      state.activeTrajId = sc.id;
      renderTrajScenarios();
      runTrajectory();
    });
    list.appendChild(btn);
  }
  const active = state.trajScenarios.find((s) => s.id === state.activeTrajId);
  if (active) {
    $('traj-scenario-desc').textContent = state.lang === 'zh' ? active.desc_zh : active.desc_en;
  }
}

function renderTrajResult(data) {
  state.currentTrajResult = data;
  const wrap = $('traj-timeline');
  wrap.textContent = '';
  const steps = Array.isArray(data.steps) ? data.steps : [];
  const blockedStep = steps.find((s) => s.verdict === 'refuse' || s.verdict === 'dissuade');
  $('traj-summary-meta').textContent = blockedStep
    ? state.lang === 'zh'
      ? `第 ${blockedStep.step} 步因果闭包拦截 (${blockedStep.verdict.toUpperCase()})`
      : `Blocked at Step ${blockedStep.step} (${blockedStep.verdict.toUpperCase()})`
    : state.lang === 'zh'
      ? `${steps.length} 步全部安全放行`
      : `All ${steps.length} steps safe`;

  for (const st of steps) {
    const tone =
      st.verdict === 'refuse'
        ? 'danger'
        : st.verdict === 'dissuade' || st.verdict === 'clarify'
          ? 'warn'
          : 'safe';
    const card = document.createElement('div');
    card.className = `traj-step-card tone-${tone}`;

    const head = document.createElement('div');
    head.className = 'traj-step-head';

    const title = document.createElement('span');
    title.className = 'traj-step-title';
    title.textContent = `Step ${st.step} · ${st.tool_name || 'action'}`;

    const badge = document.createElement('span');
    badge.className = `traj-step-verdict tone-${tone}`;
    badge.textContent = `${st.verdict.toUpperCase()} · ${st.source || st.category}`;

    head.appendChild(title);
    head.appendChild(badge);
    card.appendChild(head);

    const act = document.createElement('p');
    act.className = 'traj-step-action';
    act.textContent = st.action;
    card.appendChild(act);

    const atomsRow = document.createElement('div');
    atomsRow.className = 'traj-atoms-row';
    const prefix = document.createElement('span');
    prefix.textContent = state.lang === 'zh' ? '系统状态原子:' : 'State atoms:';
    atomsRow.appendChild(prefix);

    const beforeAtoms = (st.trajectory && st.trajectory.accumulated_before) || [];
    const proposedAtoms = st.effects || [];
    for (const a of beforeAtoms) {
      const chip = document.createElement('span');
      chip.className = 'traj-atom';
      chip.textContent = a;
      atomsRow.appendChild(chip);
    }
    for (const a of proposedAtoms) {
      const chip = document.createElement('span');
      chip.className = 'traj-atom is-new';
      chip.textContent = `+ ${a}`;
      atomsRow.appendChild(chip);
    }
    card.appendChild(atomsRow);

    const triggered = (st.trajectory && st.trajectory.triggered) || [];
    if (triggered.length > 0) {
      const box = document.createElement('div');
      box.className = 'traj-closure-box';
      for (const rule of triggered) {
        const ruleEl = document.createElement('div');
        ruleEl.className = 'traj-closure-rule';
        ruleEl.textContent = `${rule.needs.join(' + ')} → ${rule.gives} [${rule.verdict.toUpperCase()}]`;
        box.appendChild(ruleEl);

        const desc = document.createElement('div');
        desc.textContent = state.lang === 'zh' ? rule.zh : rule.en;
        box.appendChild(desc);

        const prov = document.createElement('div');
        prov.className = 'traj-closure-prov';
        const chainParts = (rule.antecedents || []).map(
          (ant) => `Step ${ant.step} (${ant.tool || ant.source}): ${ant.atom}`,
        );
        prov.textContent = (state.lang === 'zh' ? '因果溯源: ' : 'Causal provenance: ') + chainParts.join('  +  ');
        box.appendChild(prov);
      }
      card.appendChild(box);
    }

    wrap.appendChild(card);
  }
}

async function runTrajectory() {
  if (!state.liveApi) return;
  try {
    const resp = await fetch('/api/trajectory/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario_id: state.activeTrajId }),
    });
    if (resp.ok) {
      const data = await resp.json();
      renderTrajResult(data);
    }
  } catch {
    // ignore offline fallback
  }
}

// ---------------------------------------------------------------------------
// 02. N-Device Distributed SNN Model Sharding
// ---------------------------------------------------------------------------

function renderShardResult(data) {
  state.currentShardResult = data;
  const parityEl = $('shard-metric-parity');
  if (data.mode === 'exact') {
    parityEl.textContent = `Δ=${data.max_charge_diff.toExponential(1)} (${state.lang === 'zh' ? '完全等价' : 'Exact'})`;
    parityEl.className = 'metric-val allow-yes';
  } else {
    parityEl.textContent = `1-RTT WAN (${state.lang === 'zh' ? '局部递归' : 'Local Rec'})`;
    parityEl.className = 'metric-val';
  }

  $('shard-metric-bw').textContent =
    data.mode === 'exact'
      ? `${data.bytes_per_tick} B/tick (${data.compression_ratio}×)`
      : `${data.total_wire_bytes} B total`;
  $('shard-metric-rtt').textContent = `${data.rtt_rounds} RTT · ${(data.total_wire_bytes / 1024).toFixed(1)} KB`;

  const sigEl = $('shard-metric-sig');
  sigEl.textContent = data.all_signatures_valid
    ? `${data.n_shards}/${data.n_shards} ${state.lang === 'zh' ? 'Ed25519 通过' : 'Verified'}`
    : 'FAILED';
  sigEl.className = `metric-val ${data.all_signatures_valid ? 'allow-yes' : 'allow-no'}`;

  const grid = $('shard-nodes-grid');
  grid.textContent = '';
  const totalN = Math.max(1, data.total_neurons || 1280);
  for (const node of data.nodes || []) {
    const card = document.createElement('div');
    card.className = 'shard-card';

    const head = document.createElement('div');
    head.className = 'shard-card-head';
    const dev = document.createElement('span');
    dev.className = 'shard-device';
    dev.textContent = `Shard #${node.shard_id} · ${node.device_name}`;
    const sig = document.createElement('span');
    sig.className = 'shard-sig';
    sig.textContent = node.signature_valid ? 'Ed25519 OK' : 'INVALID';
    head.appendChild(dev);
    head.appendChild(sig);
    card.appendChild(head);

    const did = document.createElement('div');
    did.className = 'shard-did';
    did.textContent = node.did_short || node.did;
    card.appendChild(did);

    // Visual slice bar across the full 1,280-neuron population
    const track = document.createElement('div');
    track.className = 'shard-bar-track';
    const slice = document.createElement('div');
    slice.className = 'shard-bar-slice';
    const [start, end] = node.neuron_range || [0, node.neurons];
    slice.style.left = `${(start / totalN) * 100}%`;
    slice.style.width = `${((end - start) / totalN) * 100}%`;
    track.appendChild(slice);
    card.appendChild(track);

    const stats = document.createElement('div');
    stats.className = 'shard-stats';
    const items = [
      [state.lang === 'zh' ? '神经元片' : 'Neurons', `[${start}..${end}) (${node.neurons})`],
      [state.lang === 'zh' ? '分片权重' : 'Weights', `${(node.weight_kb / 1024).toFixed(2)} MB`],
      [state.lang === 'zh' ? '本片脉冲' : 'Spikes', `${node.total_spikes} (ALIF ${node.adaptive_spikes})`],
      [state.lang === 'zh' ? '发送流量' : 'Wire out', `${node.wire_bytes} B`],
    ];
    for (const [k, v] of items) {
      const cell = document.createElement('div');
      const lbl = document.createElement('div');
      lbl.textContent = k;
      const val = document.createElement('strong');
      val.textContent = v;
      cell.appendChild(lbl);
      cell.appendChild(val);
      stats.appendChild(cell);
    }
    card.appendChild(stats);
    grid.appendChild(card);
  }
}

async function runShard() {
  if (!state.liveApi) return;
  const text = ($('prompt-input').value || '').trim() || '假客服让我立刻把五万块转到安全账户';
  try {
    const resp = await fetch('/api/shard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        n_shards: state.shardCount,
        mode: state.shardMode,
      }),
    });
    if (resp.ok) {
      const data = await resp.json();
      renderShardResult(data);
    }
  } catch {
    // ignore offline fallback
  }
}

async function runAnalysis() {
  const text = ($('prompt-input').value || '').trim();
  const toolName = ($('tool-name-input').value || '').trim();
  const irreversible = $('irreversible-toggle').checked;
  updateCharCount();

  if (state.liveApi) {
    try {
      const resp = await fetch('/api/deliberate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, tool_name: toolName, irreversible }),
      });
      if (resp.ok) {
        const data = await resp.json();
        renderResult(data, true);
        return;
      }
    } catch {
      state.liveApi = false;
    }
  }
  const fallback = buildStaticFallbackResult(text, {
    toolName,
    irreversible,
    presetData: state.presetData,
  });
  renderResult(fallback, true);
}

async function copyText(text, feedbackEl) {
  try {
    await navigator.clipboard.writeText(text);
    feedbackEl.textContent = I18N[state.lang].copied;
  } catch {
    feedbackEl.textContent = I18N[state.lang].copied;
  }
  setTimeout(() => {
    feedbackEl.textContent = '';
  }, 2200);
}

async function bootstrap() {
  try {
    const r = await fetch('./presets.json');
    if (r.ok) state.presetData = await r.json();
  } catch {
    state.presetData = null;
  }

  try {
    const h = await fetch('/api/health');
    if (h.ok) {
      const info = await h.json();
      if (info && info.ok) {
        state.liveApi = true;
        $('meta-mode').textContent = `Live Gen ${info.generation}`;
        $('meta-neurons').textContent = `${info.neurons.toLocaleString()} neurons`;
        $('meta-synapses').textContent = `${info.synapses.toLocaleString()} synapses`;
      }
    }
  } catch {
    state.liveApi = false;
    if (state.presetData) {
      $('meta-mode').textContent = `Static Gen ${state.presetData.generation}`;
      $('meta-neurons').textContent = `${state.presetData.neurons.toLocaleString()} neurons`;
      $('meta-synapses').textContent = `${state.presetData.synapses.toLocaleString()} synapses`;
    }
  }

  if (state.liveApi) {
    try {
      const scResp = await fetch('/api/trajectory/scenarios');
      if (scResp.ok) {
        const scData = await scResp.json();
        state.trajScenarios = scData.scenarios || [];
        if (state.trajScenarios.length > 0) {
          state.activeTrajId = state.trajScenarios[0].id;
        }
      }
    } catch {
      state.trajScenarios = [];
    }
  }

  $('cortex-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    state.activePresetId = '';
    renderPresets();
    runAnalysis();
    runShard();
  });
  $('prompt-input').addEventListener('input', updateCharCount);
  $('replay-btn').addEventListener('click', () => {
    if (state.currentResult && state.currentResult.snn) {
      renderer.setData(state.currentResult.snn);
    }
  });
  $('lang-zh').addEventListener('click', () => {
    state.lang = 'zh';
    applyI18n();
  });
  $('lang-en').addEventListener('click', () => {
    state.lang = 'en';
    applyI18n();
  });

  $('run-traj-btn').addEventListener('click', () => runTrajectory());

  document.querySelectorAll('[data-shard-count]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.shardCount = Number(btn.getAttribute('data-shard-count')) || 3;
      document.querySelectorAll('[data-shard-count]').forEach((b) => {
        b.setAttribute('aria-pressed', String(Number(b.getAttribute('data-shard-count')) === state.shardCount));
      });
      runShard();
    });
  });

  document.querySelectorAll('[data-shard-mode]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.shardMode = btn.getAttribute('data-shard-mode') || 'exact';
      document.querySelectorAll('[data-shard-mode]').forEach((b) => {
        b.setAttribute('aria-pressed', String(b.getAttribute('data-shard-mode') === state.shardMode));
      });
      runShard();
    });
  });

  $('run-shard-btn').addEventListener('click', () => runShard());

  document.querySelectorAll('[data-mcp-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.mcpTarget = btn.getAttribute('data-mcp-tab');
      renderMcpTabs();
    });
  });

  $('copy-mcp-btn').addEventListener('click', () => {
    copyText($('mcp-config-code').textContent, $('copy-mcp-feedback'));
  });
  $('rt-expected').addEventListener('change', renderRedTeam);
  $('rt-note').addEventListener('input', renderRedTeam);
  $('copy-rt-btn').addEventListener('click', () => {
    const payload = buildRedTeamPayload(state.currentResult, $('rt-expected').value, $('rt-note').value);
    copyText(payload.jsonl, $('copy-rt-feedback'));
  });

  if (state.presetData && state.presetData.presets && state.presetData.presets.length > 0) {
    const first = state.presetData.presets[0];
    state.activePresetId = first.id;
    $('prompt-input').value = first.text;
    $('tool-name-input').value = first.tool_name;
    $('irreversible-toggle').checked = Boolean(first.irreversible);
  }
  updateCharCount();
  renderMcpTabs();
  applyI18n();
  await Promise.all([runAnalysis(), runTrajectory(), runShard()]);
}

bootstrap();
