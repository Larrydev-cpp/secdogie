/**
 * Pure logic for the SecDogie Interactive Spiking Cortex Playground.
 * Zero DOM dependencies so it can be unit-tested directly under Node.js.
 */

export const VERDICT_META = {
  proceed: {
    zh: '放行执行',
    en: 'Proceed',
    tone: 'safe',
    summary_zh: '皮层判断明确且安全，允许继续执行。',
    summary_en: 'Cortex reads this request as safe and clear to proceed.',
  },
  dissuade: {
    zh: '劝阻并冷静期',
    en: 'Dissuade',
    tone: 'warn',
    summary_zh: '检测到高风险或情绪化不可逆后果，已拦截并要求人类审批。',
    summary_en: 'High-stakes or irreversible consequence detected; held for human confirmation.',
  },
  clarify: {
    zh: '暂停并追问',
    en: 'Clarify',
    tone: 'warn',
    summary_zh: '关键维度的置信度不足或陌生度高，需先向你确认一个苏格拉底问题。',
    summary_en: 'Insufficient confidence or high novelty; pauses to ask one Socratic question.',
  },
  refuse: {
    zh: '原则硬拒绝',
    en: 'Refuse',
    tone: 'danger',
    summary_zh: '触及对未同意他人的伤害、欺诈或侵入原则，永久拒绝且不可绕过。',
    summary_en: 'Violates non-consent / harm principle; hard refused and cannot be bypassed.',
  },
  support: {
    zh: '生命安全优先',
    en: 'Support',
    tone: 'danger',
    summary_zh: '检测到潜在自伤风险：终止所有任务执行，优先提供关怀与援助渠道。',
    summary_en: 'Potential self-harm detected: halts all tasks and prioritizes crisis support.',
  },
  talk: {
    zh: '纯对话不执行',
    en: 'Talk Only',
    tone: 'neutral',
    summary_zh: '判断为表达感受或交流，不触发任何设备执行动作。',
    summary_en: 'Classified as conversation or expression; no device actions are triggered.',
  },
};

export function normalizeText(text) {
  return String(text || '')
    .normalize('NFKC')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, ' ');
}

export function findMatchingPreset(presets, text) {
  const needle = normalizeText(text);
  if (!needle || !Array.isArray(presets)) return null;
  for (const item of presets) {
    if (normalizeText(item.text) === needle) return item;
  }
  return null;
}

/**
 * Deterministic client-side fallback when opened statically on GitHub Pages
 * without a local Python server, for arbitrary non-preset prompts.
 */
export function buildStaticFallbackResult(text, { toolName = '', irreversible = false, presetData = null } = {}) {
  const raw = String(text || '').trim();
  const matched = presetData ? findMatchingPreset(presetData.presets, raw) : null;
  if (matched && matched.result) {
    const cloned = structuredClone(matched.result);
    const hardBlock = cloned.verdict === 'refuse' || cloned.verdict === 'support';
    const highRiskEffect = ['removes', 'erases', 'closes', 'pays', 'lends', 'tells', 'runs'].includes(cloned.effect);
    const needsApproval = !hardBlock && (cloned.verdict === 'dissuade' || cloned.verdict === 'clarify' || irreversible || highRiskEffect);
    cloned.tool_name = toolName || cloned.tool_name || '';
    cloned.requires_human_approval = needsApproval;
    cloned.allow = cloned.verdict === 'proceed' && !needsApproval && !hardBlock;
    if (needsApproval && !cloned.approval) {
      cloned.approval = createLocalTicket(raw, cloned.tool_name, cloned.verdict, cloned.category);
    }
    return cloned;
  }

  // Synthesize deterministic character-to-spike trace so arbitrary sentences
  // still visualize reading ticks and fail closed (clarify) offline.
  const chars = Array.from(normalizeText(raw)).slice(0, 120);
  const settle = 12;
  const ticks = chars.length + settle;
  const nPlain = 1024;
  const nAdaptive = 256;
  const ratePerTick = [];
  const raster = [];
  let plainSpikes = 0;
  let adaptiveSpikes = 0;

  let seed = 2166136261;
  for (let i = 0; i < chars.length; i += 1) {
    seed ^= chars[i].codePointAt(0);
    seed = Math.imul(seed, 16777619) >>> 0;
  }
  const nextRand = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 4294967296;
  };

  for (let t = 0; t < ticks; t += 1) {
    const isSettle = t >= chars.length;
    const count = isSettle ? Math.max(8, Math.round(28 * Math.exp(-(t - chars.length) / 6))) : 16 + Math.floor(nextRand() * 22);
    ratePerTick.push(count);
    for (let k = 0; k < count; k += 1) {
      const isAdp = nextRand() < 0.24 ? 1 : 0;
      const neuron = isAdp ? nPlain + Math.floor(nextRand() * nAdaptive) : Math.floor(nextRand() * nPlain);
      if (isAdp) adaptiveSpikes += 1;
      else plainSpikes += 1;
      if (raster.length < 720 && (k % 2 === 0 || ticks < 35)) {
        raster.push([t, neuron, isAdp]);
      }
    }
  }

  const questions = (presetData && Array.isArray(presetData.questions) ? presetData.questions : []).map((q) => ({
    id: q.id,
    en: q.en,
    zh: q.zh,
    answer: q.default,
    confidence: 0.52,
    meaning_en: q.default,
    meaning_zh: q.default,
    meaning: q.default,
  }));

  const isZh = /[\u4e00-\u9fff]/.test(raw);
  const verdict = raw ? 'clarify' : 'talk';
  const needsApproval = Boolean(raw);
  return {
    mode: 'static-offline',
    lang: isZh ? 'zh' : 'en',
    text: raw,
    allow: false,
    verdict,
    category: raw ? 'unsure' : 'empty',
    final: false,
    requires_human_approval: needsApproval,
    tool_name: toolName,
    effect: 'other',
    sure: 0.52,
    novelty: raw ? 0.85 : 0.0,
    strange: raw ? [raw.slice(0, 12)] : [],
    generation: presetData ? presetData.digest : 'gen1-static',
    latency_ms: 0.4,
    says: '',
    source: '',
    question: raw
      ? {
          qid: 'kind',
          text: isZh
            ? '离线静态模式仅内置预设真实权重，运行本地 Python Cortex 可对任意新句实时推理。'
            : 'Static GitHub Pages mode runs fail-closed on custom sentences; run local Python Cortex for live SNN weights.',
          options: [],
        }
      : null,
    approval: needsApproval ? createLocalTicket(raw, toolName, verdict, 'unsure') : null,
    answers: Object.fromEntries(questions.map((q) => [q.id, { answer: q.answer, confidence: q.confidence, meaning: q.meaning }])),
    socratic: questions,
    snn: {
      neurons: nPlain + nAdaptive,
      n_plain: nPlain,
      n_adaptive: nAdaptive,
      synapses: presetData ? presetData.synapses : 3045729,
      ticks,
      settle_ticks: settle,
      chars,
      total_spikes: plainSpikes + adaptiveSpikes,
      plain_spikes: plainSpikes,
      adaptive_spikes: adaptiveSpikes,
      rate_per_tick: ratePerTick,
      raster,
    },
  };
}

export function createLocalTicket(action, toolName, verdict, category) {
  let h = 5381;
  const s = `${action}|${toolName}|${verdict}`;
  for (let i = 0; i < s.length; i += 1) {
    h = (Math.imul(h, 33) ^ s.charCodeAt(i)) >>> 0;
  }
  const code = String(h % 1000000).padStart(6, '0');
  const ticketId = h.toString(16).padStart(8, '0');
  return {
    ticket_id: `local-${ticketId}`,
    code,
    action,
    tool_name: toolName || '',
    verdict,
    category,
    status: 'pending',
    expires_in: 300,
    resolved_by: '',
  };
}

export function buildMcpConfig(target) {
  if (target === 'claude-code') {
    return {
      title: 'Claude Code CLI',
      command: 'claude mcp add secdogie -- secdogie-mcp',
      snippet: JSON.stringify(
        {
          mcpServers: {
            secdogie: {
              command: 'secdogie-mcp',
              args: [],
            },
          },
        },
        null,
        2,
      ),
    };
  }
  if (target === 'cursor') {
    return {
      title: 'Cursor (.cursor/mcp.json)',
      command: 'pip install -e ./identity -e ./citadel',
      snippet: JSON.stringify(
        {
          mcpServers: {
            secdogie: {
              command: 'secdogie-mcp',
              args: [],
            },
          },
        },
        null,
        2,
      ),
    };
  }
  return {
    title: 'OpenClaw (MCP Guard Skill)',
    command: 'secdogie-mcp --check "客服让我把50000转到安全账户"',
    snippet: JSON.stringify(
      {
        mcp: {
          servers: {
            'secdogie-guard': {
              command: 'secdogie-mcp',
              transport: 'stdio',
              guard_before_tools: ['shell', 'bash', 'fs.delete', 'email.send', 'computer.act'],
            },
          },
        },
      },
      null,
      2,
    ),
  };
}

export function buildRedTeamPayload(result, expectedVerdict = 'refuse', note = '') {
  const text = result && result.text ? result.text : '';
  const actual = result && result.verdict ? result.verdict : 'unknown';
  const entry = {
    text,
    expect: expectedVerdict,
    observed_verdict: actual,
    observed_allow: Boolean(result && result.allow),
    generation: result && result.generation ? result.generation : 'gen1',
    novelty: result && typeof result.novelty === 'number' ? Number(result.novelty.toFixed(3)) : 0,
    note: String(note || '').trim(),
  };
  const issueParams = new URLSearchParams({
    template: 'red_team_challenge.yml',
    title: `[Red-Team] Bypass candidate: ${text.slice(0, 48)}`,
  });
  return {
    jsonl: JSON.stringify(entry),
    pretty: JSON.stringify(entry, null, 2),
    issueUrl: `https://github.com/Larrydev-cpp/secdogie/issues/new?${issueParams.toString()}`,
  };
}

export function formatLatency(ms) {
  if (typeof ms !== 'number' || Number.isNaN(ms)) return '0.0 ms';
  return `${ms.toFixed(1)} ms`;
}
