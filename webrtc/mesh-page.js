// 「我的设备」: this device and your other devices, as one mesh.
//
// Nothing goes online until the person clicks 上线 / Go online; 下线 or
// closing the page ends every link. The page only reads what pairing left in
// this browser (keystore.js): the owner key and the devices it admitted, or
// this device's key and its admission.

import { existingIdentity, listDevices, listMemberships, rosterOf } from './client/keystore.js';
import { LinkState, createMesh } from './client/mesh.js';

const WORDS = {
  zh: {
    title: '我的设备',
    lede: '这些设备组成网格「{mesh}」。点「上线」后，这台设备会和其他在线的设备经 WebRTC 直连；关掉页面就下线。',
    'empty.title': '这台设备还不在任何网格里',
    'empty.text': '先在已经在网格里的设备上写一封请帖，用这台设备打开并同意。',
    'empty.pair': '去配对',
    'here.mesh': '网格',
    'here.signal': '信令地址',
    'here.invite': '请另一台设备进来',
    owner: '主人', member: '成员', unnamed: '未命名的设备', 'this.device': '这台设备',
    'go.online': '上线', 'go.offline': '下线',
    'others.title': '网格里的其他设备',
    'others.empty': '还没有别的设备。',
    'state.online': '在线 · 直连', 'state.online.rtt': '在线 · 直连 · {rtt} 毫秒',
    'state.connecting': '连接中…', 'state.offline': '不在线', 'state.refused': '被拒绝',
    'talk.title': '说一句', 'talk.ph': '发给所有在线的设备', 'talk.send': '发送', 'talk.me': '这台设备',
    'st.online': '已上线：正在连接你的其他设备。',
    'st.linked': '已上线，和 {n} 台设备直连。',
    'st.offline': '已下线，所有链路都已关闭。',
    'st.need.signal': '信令地址要以 ws:// 或 wss:// 开头。',
    'st.sent': '已发给 {n} 台设备。',
    'st.nobody': '现在没有在线的设备。',
    'st.failed': '没能上线：{detail}',
    'r.the fingerprints do not match: someone is between the two devices': '两台设备之间有人在转手，已断开',
    'r.not a device of this mesh': '不是这个网格的设备',
    'r.this is not the device this link is for': '对面不是它该是的那台设备',
    'r.the hello is too old or from the future': '两台设备的时钟相差超过两分钟',
    'r.the hello is for another mesh': '它属于另一个网格',
    foot: '链路上的每一条消息都由发送的设备签名，只有主人签过接纳书的设备才连得上。信令只帮两台设备找到彼此，看不到内容。没有后台：关掉页面，这台设备就下线。',
  },
  en: {
    title: 'My devices',
    lede: 'These devices make up the mesh “{mesh}”. Go online and this device links directly to your other online devices over WebRTC; close the page and it goes offline.',
    'empty.title': 'This device is not in a mesh yet',
    'empty.text': 'Write an invitation on a device that is already in your mesh, then open it here and agree.',
    'empty.pair': 'Go to pairing',
    'here.mesh': 'Mesh',
    'here.signal': 'Signaling address',
    'here.invite': 'Bring in another device',
    owner: 'Owner', member: 'Member', unnamed: 'Unnamed device', 'this.device': 'This device',
    'go.online': 'Go online', 'go.offline': 'Go offline',
    'others.title': 'Your other devices',
    'others.empty': 'No other devices yet.',
    'state.online': 'Online · direct', 'state.online.rtt': 'Online · direct · {rtt} ms',
    'state.connecting': 'Connecting…', 'state.offline': 'Offline', 'state.refused': 'Refused',
    'talk.title': 'Say something', 'talk.ph': 'To every device online now', 'talk.send': 'Send', 'talk.me': 'This device',
    'st.online': 'Online: linking to your other devices.',
    'st.linked': 'Online, linked directly to {n} device(s).',
    'st.offline': 'Offline. Every link is closed.',
    'st.need.signal': 'The signaling address starts with ws:// or wss://.',
    'st.sent': 'Sent to {n} device(s).',
    'st.nobody': 'No device is online right now.',
    'st.failed': 'Could not go online: {detail}',
    'r.the fingerprints do not match: someone is between the two devices': 'someone is relaying between the two devices; disconnected',
    'r.not a device of this mesh': 'not a device of this mesh',
    'r.this is not the device this link is for': 'not the device it should be',
    'r.the hello is too old or from the future': 'the clocks differ by more than two minutes',
    'r.the hello is for another mesh': 'it belongs to another mesh',
    foot: 'Every message on a link is signed by the device that sent it, and only devices with an admission the owner signed can link at all. Signaling only helps two devices find each other; it cannot read what they say. Nothing runs in the background: close the page and this device goes offline.',
  },
};

const $ = (id) => document.getElementById(id);
const store = {
  get(key) { try { return globalThis.localStorage?.getItem(key) ?? null; } catch { return null; } },
  set(key, value) { try { globalThis.localStorage?.setItem(key, value); } catch { /* not kept */ } },
};
let lang = store.get('secdogie.lang') || ((navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en');
const t = (key, params = {}) => (WORDS[lang][key] ?? WORDS.zh[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => String(params[k] ?? ''));
const shortDid = (did) => (did && did.length > 24 ? `${did.slice(0, 14)}…${did.slice(-6)}` : did ?? '');

const page = { choices: [], choice: null, mesh: null, notes: [], status: null };

function setStatus(key, params = {}, tone = '') {
  page.status = key ? { key, params, tone } : null;
  paint();
}

// ---- painting ------------------------------------------------------------------------

function paint() {
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  document.title = lang === 'zh' ? 'secdogie · 我的设备' : 'secdogie · My devices';
  for (const b of document.querySelectorAll('.lang button')) b.setAttribute('aria-pressed', String(b.dataset.lang === lang));
  for (const el of document.querySelectorAll('[data-t]')) el.textContent = t(el.dataset.t);
  for (const el of document.querySelectorAll('[data-ph]')) el.placeholder = t(el.dataset.ph);
  $('title').textContent = t('title');

  const snap = page.mesh?.snapshot();
  const empty = !page.choice;
  document.body.dataset.phase = empty ? 'empty' : snap?.online ? 'online' : 'offline';
  $('empty').hidden = !empty;
  for (const id of ['here', 'others', 'talk']) $(id).hidden = empty;
  $('lede').textContent = empty ? '' : t('lede', { mesh: page.choice.mesh });
  if (['st.online', 'st.linked'].includes(page.status?.key) && snap?.online) {
    const n = snap.devices.filter((d) => d.state === LinkState.ONLINE).length;
    page.status = n ? { key: 'st.linked', params: { n }, tone: 'ok' } : { key: 'st.online', params: {}, tone: 'ok' };
  }
  const st = $('status');
  st.textContent = page.status ? t(page.status.key, page.status.params) : '';
  st.dataset.tone = page.status?.tone ?? '';
  if (empty || !snap) return;

  $('me-name').textContent = snap.me.name || t('this.device');
  $('me-role').textContent = t(snap.me.owner ? 'owner' : 'member');
  $('me-role').classList.toggle('owner', snap.me.owner);
  $('me-did').textContent = shortDid(snap.me.did);
  $('me-did').title = snap.me.did;
  $('invite').hidden = !snap.me.owner;
  $('mesh-switch').hidden = page.choices.length < 2;
  $('online').textContent = t(snap.online ? 'go.offline' : 'go.online');
  $('online').classList.toggle('quiet', snap.online);
  $('signal').disabled = snap.online;

  $('devices').replaceChildren(...snap.devices.map((d) => {
    const li = document.createElement('li');
    li.dataset.did = d.did;
    li.dataset.state = d.state;
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.setAttribute('aria-hidden', 'true');
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = d.name || t('unnamed');
    if (d.owner) {
      const badge = document.createElement('span');
      badge.className = 'badge owner';
      badge.textContent = t('owner');
      name.append(badge);
    }
    const state = document.createElement('span');
    state.className = 'state';
    state.textContent = d.state === LinkState.ONLINE
      ? (d.rtt == null ? t('state.online') : t('state.online.rtt', { rtt: d.rtt }))
      : t(`state.${d.state}`);
    const did = document.createElement('span');
    did.className = 'did';
    did.textContent = shortDid(d.did);
    li.append(dot, name, state, did);
    if (d.state === LinkState.REFUSED && d.reason) {
      const why = document.createElement('span');
      why.className = 'why';
      why.textContent = WORDS[lang][`r.${d.reason}`] ?? d.reason;
      li.append(why);
    }
    return li;
  }));
  $('devices-empty').hidden = snap.devices.length > 0;
  const anyone = snap.devices.some((d) => d.state === LinkState.ONLINE);
  $('send').disabled = !anyone;

  $('notes').replaceChildren(...page.notes.map((n) => {
    const li = document.createElement('li');
    li.classList.toggle('mine', n.mine);
    const from = document.createElement('div');
    from.className = 'from';
    const time = new Date(n.at).toLocaleTimeString(document.documentElement.lang, { hour: '2-digit', minute: '2-digit' });
    from.textContent = `${n.mine ? t('talk.me') : n.name || shortDid(n.from)} · ${time}`;
    const text = document.createElement('div');
    text.textContent = n.text;
    li.append(from, text);
    return li;
  }));
}

// ---- which mesh -----------------------------------------------------------------------

async function choices() {
  const out = [];
  const owner = await existingIdentity('owner');
  if (owner) {
    const devices = await listDevices();
    const mesh = store.get('secdogie.mesh') || devices[0]?.mesh || 'home';
    out.push({
      key: `own:${mesh}`, mesh, slot: 'owner', me: owner, owners: [owner.did], admission: null,
      admissions: devices.filter((a) => a.mesh === mesh), name: store.get('secdogie.owner-name') || '',
    });
  }
  const device = await existingIdentity('device');
  if (device) {
    for (const a of await listMemberships()) {
      if (a.device !== device.did) continue;
      out.push({
        key: `member:${a.mesh}:${a.signer}`, mesh: a.mesh, slot: 'device', me: device, owners: [a.signer],
        admission: a, admissions: (await rosterOf(a.mesh))?.admissions ?? [], name: a.name,
      });
    }
  }
  return out;
}

async function choose(key) {
  page.mesh?.goOffline();
  page.choice = page.choices.find((c) => c.key === key) ?? page.choices[0] ?? null;
  page.notes = [];
  page.mesh = null;
  if (page.choice) {
    store.set('secdogie.mesh-choice', page.choice.key);
    const c = page.choice;
    page.mesh = await createMesh({
      me: c.me, mesh: c.mesh, owners: c.owners, admission: c.admission, admissions: c.admissions, name: c.name,
      signalingUrl: $('signal').value.trim(),
      log: new URLSearchParams(location.search).has('debug') ? (line) => console.debug(`[mesh] ${line}`) : undefined,
    });
    page.mesh.onChange(() => paint());
    page.mesh.onNote((n) => {
      page.notes.unshift({ ...n, mine: false });
      page.notes = page.notes.slice(0, 50);
      paint();
    });
  }
  paint();
}

// ---- actions -----------------------------------------------------------------------

$('online').addEventListener('click', async () => {
  if (!page.mesh) return;
  if (page.mesh.snapshot().online) {
    page.mesh.goOffline();
    setStatus('st.offline');
    return;
  }
  const signal = $('signal').value.trim();
  if (!/^wss?:\/\//.test(signal)) return setStatus('st.need.signal', {}, 'warn');
  store.set('secdogie.signal', signal);
  try {
    await page.mesh.goOnline({ signalingUrl: signal }); // inside this click: the person's consent for the session
    setStatus('st.online', {}, 'ok');
  } catch (err) {
    setStatus('st.failed', { detail: err.message }, 'warn');
  }
});

$('say-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = $('say').value.trim();
  if (!text || !page.mesh) return;
  const n = await page.mesh.say(text);
  if (n === 0) return setStatus('st.nobody', {}, 'warn');
  page.notes.unshift({ from: page.choice.me.did, text, at: Date.now(), mine: true });
  $('say').value = '';
  setStatus('st.sent', { n });
});

$('mesh-select').addEventListener('change', (e) => choose(e.target.value));

for (const b of document.querySelectorAll('.lang button')) {
  b.addEventListener('click', () => {
    lang = b.dataset.lang;
    store.set('secdogie.lang', lang);
    paint();
  });
}

window.addEventListener('pagehide', () => page.mesh?.goOffline());

(async function start() {
  if (!globalThis.isSecureContext || !globalThis.crypto?.subtle) {
    document.body.dataset.phase = 'insecure';
    $('lede').textContent = lang === 'zh'
      ? '这个页面要在 https:// 或 localhost 下打开：浏览器只在那里提供签名用的密钥功能。'
      : 'Open this page over https:// or on localhost: browsers only provide the signing keys there.';
    $('title').textContent = t('title');
    return;
  }
  $('signal').value = new URLSearchParams(location.search).get('signal') || store.get('secdogie.signal') || 'ws://localhost:8787/ws';
  page.choices = await choices();
  $('mesh-select').replaceChildren(...page.choices.map((c) => {
    const o = document.createElement('option');
    o.value = c.key;
    o.textContent = `${c.mesh} · ${t(c.slot === 'owner' ? 'owner' : 'member')}`;
    return o;
  }));
  const saved = store.get('secdogie.mesh-choice');
  if (saved && page.choices.some((c) => c.key === saved)) $('mesh-select').value = saved;
  await choose($('mesh-select').value || page.choices[0]?.key);
}());
