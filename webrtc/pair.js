// The pairing page: bring another of your own devices into your mesh.
//
// Without an invite in the URL this browser invites: it holds the mesh owner
// key, writes a short-lived invitation and waits in a one-time signaling room.
// With `#invite=…` this browser is the device being invited: it checks the
// invitation offline and does nothing on the network until the person clicks
// agree. Then:
//
//   WebRTC data channel  ->  each side signs the DTLS fingerprints it sees
//   (the bind; a mismatch means someone is between the two devices)
//   ->  both screens show the same six digits  ->  the person confirms on
//   the inviting device  ->  it signs an admission and hands it over.
//
// The invite travels in the URL fragment, which browsers never send to the
// server that serves this page.

import {
  DEFAULT_TTL, DEVICE, INVITER, checkCode, cleanName, createAdmission, createBind, createInvite, decodeInvite,
  encodeInvite, fingerprintFromSdp, randomHex32, verifyAdmission, verifyBind, verifyInvite,
} from './client/pairing.js';
import {
  existingIdentity, forgetIdentity, listDevices, listMemberships, loadOrCreateIdentity, saveDevice, saveMembership,
} from './client/keystore.js';
import {
  PeerState, closePeerConnection, currentDescriptions, onMessage, onStateChange, sendData, startPeerConnection,
} from './client/web_peer.js';

// ---- words ---------------------------------------------------------------------------

const WORDS = {
  zh: {
    'owner.title': '请你的另一台设备进来',
    'owner.lede': '网格只由你自己的设备组成。在这里写一封请帖，到另一台设备上打开、点同意；两块屏幕显示同样的六位数后，在这边接纳它。',
    'join.title': '一封请帖',
    'join.lede': '你的另一台设备邀请这台设备加入网格「{mesh}」。点同意之前，这个页面不联网。',
    'step.write': '写请帖', 'step.agree': '对方同意', 'step.compare': '对码', 'step.admit': '接纳',
    'form.mesh': '网格名',
    'form.name': '这台设备叫',
    'form.name.ph': '书房的电脑',
    'form.signal': '信令地址',
    'form.signal.hint': '只帮两台设备找到彼此，看不到你们之间传的内容。',
    'form.write': '写请帖',
    'out.title': '请帖',
    'out.sub': '在另一台设备上打开这个链接。一封请帖只请一台设备。',
    'out.copy': '复制链接', 'out.copied': '已复制',
    'out.withdraw': '收回请帖',
    'out.clock': '还剩 {t}',
    'out.waiting': '等对方打开并点同意…',
    'out.connecting': '对方同意了，正在接通…',
    'card.from': '邀请者', 'card.mesh': '网格', 'card.until': '有效至', 'card.signal': '信令',
    'card.name.ph': '我的手机',
    'card.agree': '同意加入',
    'card.offline': '同意后，这台设备会生成自己的钥匙（钥匙不离开这个浏览器），再经信令和邀请者直连。',
    'invalid.title': '这封请帖用不了',
    'invalid.next': '请在邀请你的设备上重新写一封，再打开新的链接。',
    'insecure.title': '要在安全的地址下打开',
    'insecure.reason': '浏览器只在 https:// 或 localhost 下提供签名用的密钥功能，这里是 {origin}。',
    'insecure.next': '把这个页面放到任何 https 静态托管上，或在本机用 localhost 打开，再打开请帖链接。',
    'compare.title': '两块屏幕上的数字一样吗？',
    'compare.sub.owner': '一样就接纳；不一样说明有人在两台设备之间，取消即可。',
    'compare.sub.device': '对照另一块屏幕上的六位数，在那边确认。',
    'compare.who.owner': '请求加入的是「{name}」 · {did}',
    'compare.who.device': '邀请你的是 {did}',
    'compare.admit': '一样，接纳',
    'compare.differ': '不一样，取消',
    'done.owner.title': '已接纳「{name}」',
    'done.owner.sub': '它现在是网格「{mesh}」的一员。接纳书由这台设备签名，你的任何一个节点都能核验。',
    'done.device.title': '已加入「{mesh}」',
    'done.device.sub': '邀请你的设备签了接纳书，已经存在这台设备上。',
    'done.save': '保存接纳书',
    'done.again': '再请一台',
    'mine.title': '你的网格',
    'mine.owner': '这台设备的主人身份：',
    'mine.copy': '复制',
    'mine.owner.why': '把它加进你各个节点的主人名单，节点就认得这里接纳的设备。',
    'mine.devices': '已接纳的设备',
    'mine.memberships': '这台设备加入的网格',
    'mine.empty': '还没有。',
    'mine.volatile': '这个浏览器不让保存：关掉页面后，这里的钥匙和记录都会消失。',
    'mine.forget': '忘掉这台设备的钥匙',
    'mine.forget.sure': '再点一次确认：钥匙忘掉后，用它接纳的设备要重新配对',
    'unnamed': '未命名的设备',
    'st.cancelled.me': '已取消，没有接纳任何设备。',
    'st.cancelled.them': '对方取消了，没有接纳任何设备。',
    'st.expired': '请帖过期了。需要的话再写一封。',
    'st.withdrawn': '请帖已收回。',
    'st.failed': '连不上另一台设备：{detail}',
    'st.lost': '连接中断了，正在等它恢复…',
    'st.connecting': '正在连接邀请你的设备…',
    'st.rebuilt': '连接重建了，请重新对码。',
    'st.need.mesh': '给网格起个名字。',
    'st.need.signal': '信令地址要以 ws:// 或 wss:// 开头。',
    'st.admitting': '正在签接纳书…',
    'st.waiting.admit': '等对方在那边确认…',
    'st.bad.admission': '收到的接纳书核验不过：{reason}',
    'st.copy.failed': '没能复制，请手动选中链接。',
    'foot': '关于信令：它只转交两台设备的连接信息（SDP 与候选地址）。之后的数据走两台设备之间 DTLS 加密的直连。若有人在信令中间做手脚，两边签名里的证书指纹和六位数都会对不上。',
    // why something failed, keyed by the protocol's own reason
    'r.not an invite': '这不是一封请帖。',
    "r.the signature is not the inviter's": '请帖的签名对不上，链接被改过。',
    'r.the invite id does not match its content': '请帖的内容被改过。',
    'r.the invite has expired': '请帖已经过期。',
    'r.no mesh name': '请帖不完整。',
    'r.bad room or signaling address': '请帖不完整。',
    'r.bad expiry': '请帖不完整。',
    'r.the fingerprints do not match: someone is between the two devices': '有人在两台设备之间转手连接。已断开，没有接纳任何设备。',
    'r.the other side is not the inviter': '对面不是写这封请帖的设备。已断开。',
    'r.the bind is too old or from the future': '两台设备的时钟相差超过两分钟，请先校准时间。',
    'r.a device cannot pair with itself': '一台设备不能请自己。',
    'r.other': '对方发来的确认对不上（{reason}）。已断开。',
  },
  en: {
    'owner.title': 'Bring in another of your devices',
    'owner.lede': 'Your mesh is made only of your own devices. Write an invitation here, open it on the other device and agree there. When both screens show the same six digits, admit it here.',
    'join.title': 'An invitation',
    'join.lede': 'Another of your devices invites this one into the mesh “{mesh}”. Nothing goes online until you agree.',
    'step.write': 'Write', 'step.agree': 'Agree', 'step.compare': 'Compare', 'step.admit': 'Admit',
    'form.mesh': 'Mesh name',
    'form.name': 'This device is called',
    'form.name.ph': 'Study desktop',
    'form.signal': 'Signaling address',
    'form.signal.hint': 'It only helps the two devices find each other. It cannot read what passes between them.',
    'form.write': 'Write the invitation',
    'out.title': 'Invitation',
    'out.sub': 'Open this link on the other device. One invitation brings in one device.',
    'out.copy': 'Copy link', 'out.copied': 'Copied',
    'out.withdraw': 'Withdraw',
    'out.clock': '{t} left',
    'out.waiting': 'Waiting for the other device to open it and agree…',
    'out.connecting': 'They agreed. Connecting…',
    'card.from': 'From', 'card.mesh': 'Mesh', 'card.until': 'Valid until', 'card.signal': 'Signaling',
    'card.name.ph': 'My phone',
    'card.agree': 'Agree and join',
    'card.offline': 'When you agree, this device makes its own key (the key never leaves this browser) and connects to the inviting device directly.',
    'invalid.title': 'This invitation can’t be used',
    'invalid.next': 'Write a new one on the inviting device and open the new link.',
    'insecure.title': 'Open this over a secure address',
    'insecure.reason': 'Browsers only provide the signing keys on https:// or localhost. This page is on {origin}.',
    'insecure.next': 'Put this page on any HTTPS static host, or open it on localhost on this machine, then open the invitation link again.',
    'compare.title': 'Do both screens show the same number?',
    'compare.sub.owner': 'If they match, admit it. If they don’t, someone is between the two devices: cancel.',
    'compare.sub.device': 'Compare with the six digits on the other screen, and confirm there.',
    'compare.who.owner': 'Asking to join: “{name}” · {did}',
    'compare.who.device': 'Invited by {did}',
    'compare.admit': 'They match — admit',
    'compare.differ': 'They differ — cancel',
    'done.owner.title': '“{name}” is admitted',
    'done.owner.sub': 'It is now part of the mesh “{mesh}”. This device signed the admission, and any of your nodes can check it.',
    'done.device.title': 'Joined “{mesh}”',
    'done.device.sub': 'The inviting device signed an admission. It is stored on this device.',
    'done.save': 'Save the admission',
    'done.again': 'Invite another',
    'mine.title': 'Your mesh',
    'mine.owner': 'This device’s owner identity:',
    'mine.copy': 'Copy',
    'mine.owner.why': 'Add it to your nodes’ owner list, and they will recognize the devices admitted here.',
    'mine.devices': 'Admitted devices',
    'mine.memberships': 'Meshes this device has joined',
    'mine.empty': 'None yet.',
    'mine.volatile': 'This browser won’t keep data for this page: the key and these records go when you close it.',
    'mine.forget': 'Forget this device’s key',
    'mine.forget.sure': 'Click again to confirm: devices it admitted will need to pair again',
    'unnamed': 'Unnamed device',
    'st.cancelled.me': 'Cancelled. No device was admitted.',
    'st.cancelled.them': 'The other side cancelled. No device was admitted.',
    'st.expired': 'The invitation expired. Write a new one if you still need it.',
    'st.withdrawn': 'The invitation was withdrawn.',
    'st.failed': 'Couldn’t reach the other device: {detail}',
    'st.lost': 'The connection dropped. Waiting for it to recover…',
    'st.connecting': 'Connecting to the inviting device…',
    'st.rebuilt': 'The connection was rebuilt. Compare the code again.',
    'st.need.mesh': 'Give the mesh a name.',
    'st.need.signal': 'The signaling address starts with ws:// or wss://.',
    'st.admitting': 'Signing the admission…',
    'st.waiting.admit': 'Waiting for the other side to confirm…',
    'st.bad.admission': 'The admission received does not check out: {reason}',
    'st.copy.failed': 'Couldn’t copy. Select the link by hand.',
    'foot': 'About signaling: it only passes along how to reach each device (SDP and candidates). Data then goes over an encrypted direct link (DTLS) between the two devices. If someone tampers in the middle, the certificate fingerprints in both signatures and the six digits won’t match.',
    'r.not an invite': 'This is not an invitation.',
    "r.the signature is not the inviter's": 'The signature doesn’t match: the link was changed.',
    'r.the invite id does not match its content': 'The invitation was changed.',
    'r.the invite has expired': 'The invitation has expired.',
    'r.no mesh name': 'The invitation is incomplete.',
    'r.bad room or signaling address': 'The invitation is incomplete.',
    'r.bad expiry': 'The invitation is incomplete.',
    'r.the fingerprints do not match: someone is between the two devices': 'Someone is relaying the connection between the two devices. Disconnected; nothing was admitted.',
    'r.the other side is not the inviter': 'The other side is not the device that wrote this invitation. Disconnected.',
    'r.the bind is too old or from the future': 'The two devices’ clocks differ by more than two minutes. Set the time first.',
    'r.a device cannot pair with itself': 'A device cannot invite itself.',
    'r.other': 'The other side’s confirmation doesn’t check out ({reason}). Disconnected.',
  },
};

const $ = (id) => document.getElementById(id);
const store = {
  get(key) { try { return globalThis.localStorage?.getItem(key) ?? null; } catch { return null; } },
  set(key, value) { try { globalThis.localStorage?.setItem(key, value); } catch { /* not kept; fine */ } },
};

let lang = store.get('secdogie.lang') || ((navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en');

function t(key, params = {}) {
  const text = WORDS[lang][key] ?? WORDS.zh[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, k) => String(params[k] ?? ''));
}

function why(reason) {
  const key = `r.${reason}`;
  return key in WORDS[lang] ? t(key) : t('r.other', { reason });
}

function shortDid(did) {
  return did && did.length > 24 ? `${did.slice(0, 14)}…${did.slice(-6)}` : did ?? '';
}

// ---- the page's state ------------------------------------------------------------------

const ctx = {
  mode: 'owner', // 'owner' (inviting) or 'device' (joining)
  phase: 'loading',
  me: null, // { did, sign, keyPair }
  remembered: true,
  invite: null,
  inviteError: null,
  mesh: '',
  name: '',
  fps: null, // { local, remote } of the current connection
  nonce: null,
  myBind: null,
  theirBind: null,
  pendingBind: null,
  code: '',
  admission: null,
  status: null, // { key, params, tone }
  finished: false,
  clock: null,
  forgetArmed: false,
  insecure: false, // no WebCrypto here: not https and not localhost
};

/** What one pairing attempt holds; reset before the next. */
function fresh() {
  return {
    invite: null, fps: null, nonce: null, myBind: null, theirBind: null, pendingBind: null, code: '',
    admission: null, finished: false,
  };
}

const myRole = () => (ctx.mode === 'owner' ? INVITER : DEVICE);
const theirRole = () => (ctx.mode === 'owner' ? DEVICE : INVITER);
const now = () => Math.floor(Date.now() / 1000);

function setStatus(key, params = {}, tone = '') {
  ctx.status = key ? { key, params, tone } : null;
  paintStatus();
}

/** A failure the protocol named; shown in the person's language. */
function setReason(reason) {
  ctx.status = { reason, tone: 'warn' };
  paintStatus();
}

function setPhase(phase) {
  ctx.phase = phase;
  document.body.dataset.phase = phase;
  paint();
}

// ---- painting ------------------------------------------------------------------------

const SECTIONS = {
  form: 'form', waiting: 'out', connecting: 'out', card: 'card', joining: 'card', invalid: 'invalid',
  compare: 'compare', admitting: 'compare', done: 'done', ended: null, loading: null,
};
const STEP = {
  form: 0, waiting: 1, connecting: 1, card: 1, joining: 1, invalid: 1, compare: 2, admitting: 2, done: 4, ended: -1,
};

function paint() {
  const html = document.documentElement;
  html.lang = lang === 'zh' ? 'zh-CN' : 'en';
  document.title = lang === 'zh' ? 'secdogie · 配对' : 'secdogie · Pairing';
  for (const b of document.querySelectorAll('.lang button')) b.setAttribute('aria-pressed', String(b.dataset.lang === lang));
  for (const el of document.querySelectorAll('[data-t]')) el.textContent = t(el.dataset.t);
  for (const el of document.querySelectorAll('[data-ph]')) el.placeholder = t(el.dataset.ph);

  const owner = ctx.mode === 'owner';
  $('title').textContent = t(owner ? 'owner.title' : 'join.title');
  $('lede').textContent = owner ? t('owner.lede') : ctx.invite ? t('join.lede', { mesh: ctx.invite.mesh }) : '';

  const shown = SECTIONS[ctx.phase];
  for (const id of ['form', 'out', 'card', 'invalid', 'compare', 'done']) $(id).hidden = id !== shown;

  const step = STEP[ctx.phase] ?? -1;
  [...$('steps').children].forEach((li, i) => {
    li.toggleAttribute('data-done', step > i || (step >= 0 && !owner && i === 0));
    if (i === step) li.setAttribute('aria-current', 'step');
    else li.removeAttribute('aria-current');
  });

  $('waiting-text').textContent = t(ctx.phase === 'connecting' ? 'out.connecting' : 'out.waiting');
  paintClock();

  if (ctx.invite && !owner) {
    $('card-from').textContent = shortDid(ctx.invite.inviter);
    $('card-from').title = ctx.invite.inviter;
    $('card-mesh').textContent = ctx.invite.mesh;
    $('card-until').textContent = new Date(ctx.invite.expires_at * 1000)
      .toLocaleString(html.lang, { hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' });
    $('card-signal').textContent = new URL(ctx.invite.signal.replace(/^ws/, 'http')).host;
  }
  if (ctx.insecure) {
    $('invalid-title').textContent = t('insecure.title');
    $('invalid-reason').textContent = t('insecure.reason', { origin: location.origin });
    $('invalid-next').textContent = t('insecure.next');
  } else {
    $('invalid-title').textContent = t('invalid.title');
    $('invalid-reason').textContent = ctx.inviteError ? why(ctx.inviteError) : '';
    $('invalid-next').textContent = t('invalid.next');
  }

  if (ctx.code) {
    const code = $('code');
    code.replaceChildren(...[ctx.code.slice(0, 3), ctx.code.slice(3)].map((part) => {
      const s = document.createElement('span');
      s.textContent = part;
      return s;
    }));
    code.setAttribute('aria-label', ctx.code.split('').join(' '));
  }
  $('compare-sub').textContent = t(owner ? 'compare.sub.owner' : 'compare.sub.device');
  if (ctx.theirBind) {
    $('who').textContent = owner
      ? t('compare.who.owner', { name: ctx.theirBind.name || t('unnamed'), did: shortDid(ctx.theirBind.did) })
      : t('compare.who.device', { did: shortDid(ctx.theirBind.did) });
  }
  $('admit').hidden = !owner;
  $('admit').disabled = ctx.phase !== 'compare';
  $('admit').setAttribute('aria-busy', String(ctx.phase === 'admitting'));
  $('differ').disabled = ctx.phase === 'admitting';

  if (ctx.admission) {
    const name = ctx.admission.name || t('unnamed');
    $('done-title').textContent = t(owner ? 'done.owner.title' : 'done.device.title', { name, mesh: ctx.admission.mesh });
    $('done-sub').textContent = t(owner ? 'done.owner.sub' : 'done.device.sub', { mesh: ctx.admission.mesh });
    $('save').hidden = owner;
    $('again').hidden = !owner;
  }
  paintStatus();
  paintMine().catch(() => {});
}

function paintStatus() {
  const el = $('status');
  const s = ctx.status;
  el.textContent = !s ? '' : s.reason ? why(s.reason) : t(s.key, s.params);
  el.dataset.tone = ctx.status?.tone ?? '';
}

function paintClock() {
  if (!ctx.invite || ctx.mode !== 'owner') return;
  const left = Math.max(0, ctx.invite.expires_at - now());
  const m = Math.floor(left / 60);
  const s = String(left % 60).padStart(2, '0');
  $('clock').textContent = t('out.clock', { t: `${m}:${s}` });
}

async function paintMine() {
  const owner = ctx.mode === 'owner';
  const me = owner ? await existingIdentity('owner') : null;
  const items = owner ? await listDevices() : await listMemberships();
  $('mine').hidden = !(me || items.length);
  $('owner-block').hidden = !me;
  $('mine-title').hidden = !owner;
  if (me) $('owner-did').textContent = me.did;
  $('list-title').textContent = t(owner ? 'mine.devices' : 'mine.memberships');
  $('devices').replaceChildren(...items.map((a) => {
    const li = document.createElement('li');
    const label = document.createElement('span');
    label.textContent = owner ? (a.name || t('unnamed')) : a.mesh;
    const when = document.createElement('span');
    when.className = 'when';
    when.textContent = new Date(a.admitted_at * 1000).toLocaleDateString(document.documentElement.lang);
    li.append(label, when);
    return li;
  }));
  $('devices-empty').hidden = items.length > 0;
  $('volatile').hidden = ctx.remembered;
  $('forget').hidden = !me;
  $('forget').textContent = t(ctx.forgetArmed ? 'mine.forget.sure' : 'mine.forget');
}

// ---- the connection ------------------------------------------------------------------

function finish(phase, statusKey, params = {}, tone = '') {
  ctx.finished = true;
  clearInterval(ctx.clock);
  closePeerConnection();
  setPhase(phase);
  setStatus(statusKey, params, tone);
}

onStateChange((state, detail) => {
  if (ctx.finished) return;
  if (state === PeerState.CONNECTED) {
    connected().catch((err) => finish('ended', 'st.failed', { detail: err.message }, 'warn'));
  } else if (state === PeerState.NEGOTIATING && ctx.phase === 'waiting') {
    setPhase('connecting');
  } else if (state === PeerState.DISCONNECTED) {
    setStatus('st.lost', {}, 'warn');
  } else if (state === PeerState.FAILED) {
    finish('ended', 'st.failed', { detail: detail || state }, 'warn');
  }
});

/** The data channel is open: sign what we see of it. */
async function connected() {
  const d = currentDescriptions();
  const fps = d && { local: fingerprintFromSdp(d.local), remote: fingerprintFromSdp(d.remote) };
  if (!fps?.local || !fps?.remote) throw new Error('no DTLS fingerprints in the session description');
  if (ctx.fps && ctx.fps.local === fps.local && ctx.fps.remote === fps.remote) {
    if (ctx.status?.key === 'st.lost') setStatus(null);
    return; // ICE recovered on the same connection: nothing changed
  }
  if (ctx.fps) {
    // a new connection: what was compared before no longer holds
    ctx.theirBind = null;
    ctx.code = '';
    setPhase(ctx.mode === 'owner' ? 'connecting' : 'joining');
    setStatus('st.rebuilt', {}, 'warn');
  }
  ctx.fps = fps;
  ctx.nonce = randomHex32();
  ctx.myBind = null;
  const bind = await createBind(ctx.me, {
    inviteId: ctx.invite.invite_id, role: myRole(), nonce: ctx.nonce, localFp: fps.local, remoteFp: fps.remote,
    name: ctx.name,
  });
  ctx.myBind = bind;
  await sendData({ t: 'pair/bind', bind });
  const pending = ctx.pendingBind;
  ctx.pendingBind = null;
  if (pending) await receivedBind(pending);
}

async function receivedBind(bind) {
  if (!ctx.myBind) {
    ctx.pendingBind = bind; // it overtook our own signing; take it once ours is out
    return;
  }
  const checked = await verifyBind(bind, {
    invite: ctx.invite, expectRole: theirRole(), myLocalFp: ctx.fps.local, myRemoteFp: ctx.fps.remote,
  });
  if (!checked.ok) {
    finish('ended', null);
    setReason(checked.reason);
    return;
  }
  ctx.theirBind = checked.value;
  const [inviterBind, deviceBind] = ctx.mode === 'owner' ? [ctx.myBind, ctx.theirBind] : [ctx.theirBind, ctx.myBind];
  ctx.code = await checkCode(ctx.invite.invite_id, inviterBind, deviceBind);
  setPhase('compare');
  setStatus(ctx.mode === 'owner' ? null : 'st.waiting.admit');
}

onMessage(async (raw) => {
  if (ctx.finished || typeof raw !== 'string' || raw.length > 16384) return;
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch {
    return;
  }
  if (msg?.t === 'pair/bind') {
    await receivedBind(msg.bind);
  } else if (msg?.t === 'pair/cancel') {
    finish('ended', 'st.cancelled.them', {}, 'warn');
  } else if (msg?.t === 'pair/admission' && ctx.mode === 'device' && ctx.theirBind) {
    await receivedAdmission(msg.admission);
  } else if (msg?.t === 'pair/received' && ctx.mode === 'owner' && ctx.phase === 'done') {
    finish('done', null);
  }
});

async function receivedAdmission(admission) {
  const checked = await verifyAdmission(admission, {
    owners: [ctx.invite.inviter], mesh: ctx.invite.mesh, device: ctx.me.did,
  });
  if (!checked.ok || checked.value.invite_id !== ctx.invite.invite_id) {
    finish('ended', 'st.bad.admission', { reason: checked.reason ?? 'another invite' }, 'warn');
    return;
  }
  ctx.admission = checked.value;
  await saveMembership(ctx.admission);
  try {
    await sendData({ t: 'pair/received' });
  } catch { /* the inviter closes on its own after a while */ }
  setTimeout(() => finish('done', null), 300);
  setPhase('done');
  setStatus(null);
}

// ---- the inviting side ----------------------------------------------------------------

function defaultSignal() {
  const fromUrl = new URLSearchParams(location.search).get('signal');
  return fromUrl || store.get('secdogie.signal') || 'ws://localhost:8787/ws';
}

$('invite-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const mesh = $('mesh').value.trim();
  const signal = $('signal').value.trim();
  if (!mesh) return setStatus('st.need.mesh', {}, 'warn');
  if (!/^wss?:\/\//.test(signal)) return setStatus('st.need.signal', {}, 'warn');
  ctx.name = cleanName($('owner-name').value);
  ctx.mesh = mesh;
  store.set('secdogie.signal', signal);
  store.set('secdogie.mesh', mesh);
  store.set('secdogie.owner-name', ctx.name);

  // Start the peer first, inside the click: the browser only allows it from a gesture.
  closePeerConnection();
  Object.assign(ctx, fresh());
  const room = `pair-${randomHex32()}`;
  const joined = startPeerConnection({ signalingUrl: signal, room, log: () => {} });
  joined.catch(() => {}); // awaited below; this only keeps an early failure from going unhandled
  $('write').disabled = true;
  try {
    const { identity, remembered } = await loadOrCreateIdentity('owner');
    ctx.me = identity;
    ctx.remembered = remembered;
    ctx.invite = await createInvite(identity, mesh, { room, signal, ttl: DEFAULT_TTL });
    await joined;
  } catch (err) {
    $('write').disabled = false;
    closePeerConnection();
    return setStatus('st.failed', { detail: err.message }, 'warn');
  }
  const base = new URL('pair.html', location.href);
  base.search = '';
  base.hash = `invite=${encodeInvite(ctx.invite)}`;
  $('link').value = base.href;
  ctx.clock = setInterval(() => {
    paintClock();
    if (now() >= ctx.invite.expires_at && !['done', 'admitting'].includes(ctx.phase)) finish('form', 'st.expired', {}, 'warn');
  }, 1000);
  $('write').disabled = false;
  setStatus(null);
  setPhase('waiting');
});

$('copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('link').value);
    $('copy').textContent = t('out.copied');
    setTimeout(() => { $('copy').textContent = t('out.copy'); }, 1600);
  } catch {
    $('link').select();
    setStatus('st.copy.failed', {}, 'warn');
  }
});

$('withdraw').addEventListener('click', () => finish('form', 'st.withdrawn'));

$('admit').addEventListener('click', async () => {
  if (ctx.phase !== 'compare' || ctx.mode !== 'owner') return;
  setPhase('admitting');
  setStatus('st.admitting');
  try {
    const admission = await createAdmission(ctx.me, ctx.invite.mesh, ctx.theirBind.did, {
      inviteId: ctx.invite.invite_id, name: ctx.theirBind.name,
    });
    await sendData({ t: 'pair/admission', admission });
    await saveDevice(admission);
    ctx.admission = admission;
    clearInterval(ctx.clock);
    setPhase('done');
    setStatus(null);
    // the device says when it has the admission; close then, or after a while regardless
    setTimeout(() => { if (!ctx.finished) finish('done', null); }, 8000);
  } catch (err) {
    finish('ended', 'st.failed', { detail: err.message }, 'warn');
  }
});

$('differ').addEventListener('click', async () => {
  try {
    await sendData({ t: 'pair/cancel' });
  } catch { /* closing tells them anyway */ }
  finish('ended', 'st.cancelled.me');
});

$('again').addEventListener('click', () => {
  closePeerConnection();
  Object.assign(ctx, fresh());
  setStatus(null);
  setPhase('form');
});

$('save').addEventListener('click', () => {
  const blob = new Blob([`${JSON.stringify(ctx.admission, null, 2)}\n`], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `secdogie-admission-${ctx.admission.mesh.replace(/[^\w.-]+/g, '_')}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});

$('copy-owner').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('owner-did').textContent);
  } catch { /* the DID stays selectable */ }
});

$('forget').addEventListener('click', async () => {
  if (!ctx.forgetArmed) {
    ctx.forgetArmed = true;
    paintMine();
    setTimeout(() => { ctx.forgetArmed = false; paintMine(); }, 6000);
    return;
  }
  ctx.forgetArmed = false;
  await forgetIdentity(ctx.mode === 'owner' ? 'owner' : 'device');
  paintMine();
});

// ---- the joining side -----------------------------------------------------------------

$('agree-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (ctx.phase !== 'card') return;
  const checked = await verifyInvite(ctx.invite); // it may have expired while the page sat open
  if (!checked.ok) {
    ctx.inviteError = checked.reason;
    return setPhase('invalid');
  }
  ctx.name = cleanName($('device-name').value);
  store.set('secdogie.device-name', ctx.name);
  const joined = startPeerConnection({ signalingUrl: ctx.invite.signal, room: ctx.invite.room, log: () => {} });
  joined.catch(() => {});
  setPhase('joining');
  $('agree').disabled = true;
  setStatus('st.connecting');
  try {
    const { identity, remembered } = await loadOrCreateIdentity('device');
    ctx.me = identity;
    ctx.remembered = remembered;
    await joined;
  } catch (err) {
    finish('ended', 'st.failed', { detail: err.message }, 'warn');
  }
});

// ---- start ---------------------------------------------------------------------------

for (const b of document.querySelectorAll('.lang button')) {
  b.addEventListener('click', () => {
    lang = b.dataset.lang;
    store.set('secdogie.lang', lang);
    paint();
  });
}

async function start() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  const link = fragment.get('invite');
  if (!globalThis.isSecureContext || !globalThis.crypto?.subtle) {
    ctx.mode = link === null ? 'owner' : 'device';
    ctx.insecure = true;
    setPhase('invalid');
    return;
  }
  if (link === null) {
    ctx.mode = 'owner';
    $('mesh').value = store.get('secdogie.mesh') || 'home';
    $('owner-name').value = store.get('secdogie.owner-name') || '';
    $('signal').value = defaultSignal();
    setPhase('form');
    return;
  }
  ctx.mode = 'device';
  $('device-name').value = store.get('secdogie.device-name') || '';
  const decoded = decodeInvite(link);
  const checked = await verifyInvite(decoded);
  if (!checked.ok) {
    ctx.invite = null; // never show what an unverified link claims
    ctx.inviteError = checked.reason;
    setPhase('invalid');
    return;
  }
  ctx.invite = checked.value;
  setPhase('card');
}

window.addEventListener('hashchange', () => location.reload());
start();
