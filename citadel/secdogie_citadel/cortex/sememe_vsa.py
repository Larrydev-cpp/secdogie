"""Sememe-VSA: meaning-level familiarity sensor and compact 128-byte semantic key store.

Replaces character-level Bloom novelty on Gen 2+ without modifying ``senses.py`` and without any
pretrained weights or regex libraries:

1. **Bilingual FMM Sememe Table**: maps Chinese and English words/phrases to shared role-bound
   sememe identifiers ``(ROLE, SEMEME_ID)``. Unmatched tokens fold into a low-weight ``RESIDUAL``
   channel (``0.15``) via deterministic ``blake2b`` projections.
2. **Negation Polarity**: local negation markers (``别``, ``不要``, ``don't``, ``never``, ``not``, ...)
   invert the ``ACT`` role vector and superpose a ``NEG`` polarity vector so ``删掉文件`` and
   ``别删掉文件`` separate cleanly.
3. **Bipolar VSA Binding & Parity Tie-Breaking Quantization**: ``DIM = 1024`` dimensions bound via
   elementwise bipolar Hadamard products, quantized with parity tie-breaking
   ``(vec > 0) | ((vec == 0) & ((arange(1024) & 1).astype(bool)))`` so empty/zero slots never collapse
   into all-ones, and packed to **128 bytes per entry** (32x smaller than float32).
4. **Fast XOR + POPCNT & Non-Linear Novelty Calibration**: similarity is computed via Hamming distance
   over packed 128-byte rows and mapped through ``novelty = (1.0 - max_sim) ** 2.2`` so paraphrases
   (``sim >= 0.50``) stay below the ``0.25`` familiarity threshold while semantically unseen dangers
   (e.g., ``关掉杀毒软件`` vs ``买牛奶提醒``) produce high novelty (``> 0.70``) and trigger ``ASK``.
"""
from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable
from dataclasses import dataclass, field

try:
    import numpy as np
except ImportError:  # pragma: no cover - pure-Python fallback for stdlib-only environments
    np = None  # type: ignore[assignment]

from .familiar import _wide
from .senses import clean

DIM = 1024
PACKED_BYTES = DIM // 8  # 128 bytes per stored semantic key
RESIDUAL_WEIGHT = 0.15
NEG_WEIGHT = 1.15
NOVELTY_GAMMA = 2.2
PACK_MAGIC = b"SDMV1\x00"

ROLE_ACT = "ACT"
ROLE_OBJ = "OBJ"
ROLE_MOD = "MOD"
ROLE_CTX = "CTX"
ROLE_NEG = "NEG"
ROLE_RESIDUAL = "RESIDUAL"

ROLE_PAIR = "PAIR"

ROLE_WEIGHTS = {
    # Balanced role weights + strong predicate-argument relational binding (ACT ⊗ OBJ) ensure
    # neither ACT nor OBJ dominates away the other, and identical verbs on different targets
    # (e.g. "关掉灯" vs "关掉杀毒软件") stay orthogonal in Hamming space.
    ROLE_ACT: 0.72,
    ROLE_OBJ: 0.72,
    ROLE_PAIR: 1.24,
    ROLE_MOD: 0.58,
    ROLE_CTX: 0.52,
    ROLE_NEG: 1.25,
    ROLE_RESIDUAL: RESIDUAL_WEIGHT,
}

# Local negation markers that flip ACT polarity and mark negation explicitly.
NEGATION_ZH = (
    "不要", "别去", "别再", "千万别", "不要再", "不能", "不可", "禁止", "停止", "取消", "切勿", "勿", "别", "没有", "没", "不",
)
NEGATION_EN = frozenset({
    "dont", "don't", "never", "not", "no", "stop", "avoid", "without", "cancel", "forbid", "cannot", "cant", "can't",
})

# Neutral discourse/function fillers skipped from residuals so polite wrappers do not add spurious overlap.
_FILLER_ZH = frozenset({"帮我", "帮忙", "请帮我", "麻烦", "请问", "一下", "一张", "一个", "一些", "的", "了", "把", "给", "我", "你", "请", "一", "张", "个"})
_FILLER_EN = frozenset({"a", "an", "the", "to", "me", "my", "i", "you", "for", "of", "in", "on", "at", "please", "can", "could", "help"})

# Bilingual shared sememe table: surface -> tuple of (ROLE, SEMEME_ID)
# Organized by shared concept across Chinese and English so cross-lingual & same-lingual paraphrases align.
_CONCEPTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # --- Actions (ACT) ---
    (ROLE_ACT, "DISABLE", ("关掉", "关闭", "停用", "禁用", "卸掉", "卸载", "撤掉", "绕过", "disable", "turn off", "shut down", "shut off", "deactivate", "bypass", "uninstall", "kill")),
    (ROLE_ACT, "FORGE", ("伪造", "造假", "假造", "仿造", "篡改", "做假", "开假", "假发票", "假证明", "假合同", "forge", "fake", "falsify", "counterfeit", "fabricate", "tamper")),
    (ROLE_ACT, "DELETE", ("删除", "删掉", "删", "清空", "清掉", "清理", "抹掉", "销毁", "擦除", "清除", "delete", "remove", "erase", "wipe", "purge", "destroy", "clear", "rm")),
    (ROLE_ACT, "FORMAT", ("格式化", "排版", "美化代码", "format", "prettier", "lint")),
    (ROLE_ACT, "MAKE", ("制作", "制造", "搭建", "开发", "生成", "做个", "做一个", "build", "make", "develop", "generate", "design")),
    (ROLE_ACT, "DEFEND", ("防范", "防御", "防止", "防骗", "防诈骗", "保护", "识别", "避免被骗", "防", "protect", "defend", "prevent", "guard against", "avoid")),
    (ROLE_ACT, "REMIND", ("提醒", "备忘", "记一下", "别忘", "闹钟", "remind", "reminder", "alarm", "memo")),
    (ROLE_ACT, "BUY", ("买", "购买", "采购", "下单", "订购", "补货", "buy", "purchase", "order", "shop")),
    (ROLE_ACT, "TRANSFER", ("转账", "转钱", "转给", "打款", "汇款", "付款", "支付", "结账", "交房租", "transfer", "wire", "pay", "payment", "remit")),
    (ROLE_ACT, "SEND", ("发送", "发给", "寄出", "群发", "转发", "推送", "发朋友圈", "发辞职信", "发", "send", "forward", "broadcast", "post")),
    (ROLE_ACT, "STEAL", ("窃取", "盗取", "偷看", "抓取", "破解", "入侵", "攻击", "注入", "钓鱼", "定位", "木马", "勒索软件", "人肉", "监控", "steal", "hack", "crack", "exploit", "inject", "phish", "phishing", "sniff", "scrape", "track", "locate", "spyware", "ddos", "ransomware", "malware", "trojan")),
    (ROLE_ACT, "INSTALL", ("安装", "部署", "配置", "开启", "启用", "打开", "运行", "执行", "install", "setup", "enable", "open", "run", "execute", "launch", "start")),
    (ROLE_ACT, "PLAY", ("播放", "放一首", "听歌", "放点", "play", "listen")),
    (ROLE_ACT, "READ", ("读取", "查看", "检查", "搜索", "查找", "总结", "整理", "分析", "扫描", "杀一下毒", "查一下", "read", "view", "check", "inspect", "search", "find", "summarize", "analyze", "list", "scan")),
    (ROLE_ACT, "WRITE", ("写", "起草", "编辑", "修改", "更新", "保存", "创建", "新建", "write", "draft", "edit", "modify", "update", "save", "create")),
    (ROLE_ACT, "SHARE", ("分享", "公开", "发布", "上传", "泄露", "导出", "同步", "share", "publish", "upload", "leak", "export", "sync")),
    (ROLE_ACT, "HARM", ("伤害", "自残", "自杀", "轻生", "割腕", "我想死", "不如死了", "报复", "威胁", "勒索", "欺骗", "诈骗", "打他", "打人", "杀", "谋杀", "不想活", "活不下去", "撑不下去", "结束这一切", "harm", "hurt", "hurt myself", "suicide", "revenge", "threaten", "blackmail", "scam", "fraud", "deceive", "murder", "want to die", "kill myself", "end my life")),
    (ROLE_ACT, "BACKUP", ("备份", "归档", "恢复", "回滚", "还原", "拷贝", "复制", "拷出来", "拷走", "backup", "archive", "restore", "rollback", "copy", "clone")),
    # --- Objects (OBJ) ---
    (ROLE_OBJ, "ANTIVIRUS", ("杀毒软件", "杀毒", "防病毒", "防火墙", "安全软件", "卫士", "病毒", "defender", "antivirus", "firewall", "security", "edr", "av", "virus")),
    (ROLE_OBJ, "INVOICE", ("发票", "收据", "账单", "报销单", "报销", "凭证", "票据", "合同", "证明", "公章", "签名", "invoice", "receipt", "bill", "voucher", "contract", "certificate", "seal", "signature")),
    (ROLE_OBJ, "MILK", ("牛奶", "鲜奶", "酸奶", "面包", "鸡蛋", "水果", "菜", "杂货", "milk", "bread", "eggs", "groceries", "coffee", "water", "food")),
    (ROLE_OBJ, "CODE", ("代码", "脚本", "程序", "源码", "网页", "网站", "页面", "函数", "code", "script", "program", "webpage", "website", "html", "json", "css")),
    (ROLE_OBJ, "DISK", ("磁盘", "硬盘", "u盘", "移动硬盘", "分区", "回收站", "盘", "disk", "drive", "usb", "usb stick", "flash drive", "hard drive", "partition", "trash", "recycle bin")),
    (ROLE_OBJ, "CACHE", ("缓存", "临时文件", "安装包", "弹窗", "提示框", "窗口", "广告", "popup", "pop-up", "window", "cache", "temp", "banner", "dialog")),
    (ROLE_OBJ, "MEDIA", ("音乐", "歌曲", "歌", "电影", "播客", "music", "song", "songs", "movie", "podcast", "playlist")),
    (ROLE_OBJ, "WEAPON", ("炸弹", "枪", "武器", "毒药", "雷管", "bomb", "gun", "weapon", "explosive", "poison")),
    (ROLE_OBJ, "FILE", ("文件", "文档", "资料", "照片", "图片", "视频", "目录", "文件夹", "数据库", "网盘", "报告", "周报", "表格", "客户资料", "file", "files", "document", "doc", "photo", "photos", "folder", "directory", "database", "report", "spreadsheet", "pdf", "customer list")),
    (ROLE_OBJ, "CREDENTIAL", ("密码", "口令", "私钥", "密钥", "令牌", "验证码", "银行卡", "身份证", "账号", "账户", "安全账户", "password", "passcode", "secret", "key", "token", "otp", "credential", "account", "wallet")),
    (ROLE_OBJ, "MONEY", ("钱", "资金", "余额", "存款", "现金", "美金", "人民币", "比特币", "资产", "房租", "money", "cash", "funds", "balance", "bank", "crypto", "dollar", "salary", "rent")),
    (ROLE_OBJ, "SYSTEM", ("系统", "注册表", "内核", "权限", "管理员", "进程", "内存", "驱动", "电脑", "手机", "system", "registry", "kernel", "admin", "root", "sudo", "process", "memory", "driver", "device", "laptop", "phone")),
    (ROLE_OBJ, "PERSON", ("别人", "他人", "同事", "老板", "客户", "客服", "房东", "孩子", "朋友", "家人", "室友", "对方", "老师", "前任", "竞争对手", "everyone", "someone", "colleague", "boss", "client", "friend", "family", "stranger", "others", "teacher", "kid", "child", "competitor", "rival")),
    (ROLE_OBJ, "MESSAGE", ("邮件", "短信", "微信", "消息", "通知", "评论", "帖子", "私信", "朋友圈", "辞职信", "感谢信", "email", "sms", "message", "chat", "notification", "comment", "reply", "note", "letter")),
    (ROLE_OBJ, "CALENDAR", ("会议", "开会", "日程", "明天", "今晚", "周末", "时间", "几点", "天气", "路线", "meeting", "calendar", "tomorrow", "tonight", "weekend", "weather", "route", "time")),
    (ROLE_OBJ, "SELF", ("自己", "活着", "人生", "自残", "自杀", "轻生", "割腕", "我想死", "不如死了", "不想活", "活不下去", "撑不下去", "结束这一切", "痛苦", "绝望", "myself", "life", "worthless", "hopeless", "suicide", "hurt myself", "want to die", "end it", "kill myself", "end my life")),
    # --- Modifiers & Context (MOD / CTX) ---
    (ROLE_MOD, "ALL", ("所有", "全部", "整个", "全盘", "彻底", "永久", "完全", "all", "every", "entire", "whole", "permanently", "completely")),
    (ROLE_MOD, "SECRET", ("偷偷", "悄悄", "背着", "匿名", "伪装", "隐蔽", "绕开", "外泄", "机密", "secretly", "silently", "quietly", "anonymously", "covert", "hidden", "confidential")),
    (ROLE_MOD, "URGENT", ("立刻", "马上", "赶紧", "紧急", "现在", "immediately", "now", "urgent", "asap", "quickly")),
    (ROLE_MOD, "SAFE", ("只读", "安全", "本地", "测试", "演练", "草稿", "新买的", "刚买的", "刚买", "新的", "空的", "考虑很久", "已经备份", "备份过了", "都备份过了", "备份好了", "有备份", "readonly", "read-only", "safe", "local", "test", "draft", "preview", "backed up", "just bought", "brand new", "empty")),
    (ROLE_MOD, "WORRY", ("联系不上", "失联", "走丢", "找不到", "着急", "担心", "发烧", "not answering", "missing", "lost", "worried")),
    (ROLE_MOD, "QUERY", ("吗", "是不是", "什么", "什么是", "怎么", "如何", "为什么", "几点", "真的吗", "安全吗", "可以吗", "该不该", "要不要", "what", "how", "why", "when", "where", "should", "does", "is this", "is it")),
    (ROLE_CTX, "GREETING", ("你好", "您好", "嗨", "哈喽", "早安", "晚安", "再见", "拜拜", "谢谢", "hi", "hello", "hey", "bye", "goodbye", "thanks", "thank you")),
    (ROLE_CTX, "NIGHT", ("深夜", "半夜", "凌晨", "夜里", "midnight", "late night", "3am")),
    (ROLE_CTX, "WORK", ("公司", "办公", "生产环境", "线上", "服务器", "company", "office", "production", "prod", "server")),
)

# Implied secondary sememes for compact single-word actions (e.g. 转账 implies MONEY).
_IMPLIED_SEMEMES: dict[tuple[str, str], tuple[str, str]] = {
    (ROLE_ACT, "TRANSFER"): (ROLE_OBJ, "MONEY"),
}


def _build_tables() -> tuple[dict[str, tuple[tuple[str, str], ...]], dict[str, tuple[tuple[str, str], ...]], int]:
    zh_acc: dict[str, list[tuple[str, str]]] = {}
    en_acc: dict[str, list[tuple[str, str]]] = {}
    max_zh = max(len(x) for x in _FILLER_ZH)
    for role, sememe_id, terms in _CONCEPTS:
        pair = (role, sememe_id)
        for term in terms:
            t = term.strip().lower()
            if not t:
                continue
            target = zh_acc if any(_wide(ch) for ch in t) else en_acc
            if any(_wide(ch) for ch in t) and len(t) > max_zh:
                max_zh = len(t)
            bucket = target.setdefault(t, [])
            if pair not in bucket:
                bucket.append(pair)
    return (
        {k: tuple(v) for k, v in zh_acc.items()},
        {k: tuple(v) for k, v in en_acc.items()},
        max_zh,
    )


_ZH_SEMEMES, _EN_SEMEMES, _MAX_ZH_LEN = _build_tables()
_MAX_NEG_ZH = max(len(x) for x in NEGATION_ZH)
_NEG_ZH_SET = frozenset(NEGATION_ZH)

# 256-entry POPCNT lookup table for ultra-fast XOR+POPCNT Hamming distance.
_POPCNT_PY = tuple(bin(i).count("1") for i in range(256))
_POPCNT_NP = np.array(_POPCNT_PY, dtype=np.uint8) if np is not None else None

_VEC_CACHE: dict[str, object] = {}


def _bipolar_vec(tag: str):
    """Deterministic ±1 bipolar vector of length ``DIM`` seeded by blake2b(tag)."""
    cached = _VEC_CACHE.get(tag)
    if cached is not None:
        return cached
    raw = bytearray()
    counter = 0
    while len(raw) < PACKED_BYTES:
        h = hashlib.blake2b(f"{tag}:{counter}".encode(), digest_size=64, person=b"sd-vsa-1").digest()
        raw.extend(h)
        counter += 1
    packed = bytes(raw[:PACKED_BYTES])
    if np is not None:
        bits = np.unpackbits(np.frombuffer(packed, dtype=np.uint8))[:DIM]
        vec = np.where(bits > 0, 1.0, -1.0).astype(np.float32)
    else:
        vec = [1.0 if ((packed[i >> 3] >> (7 - (i & 7))) & 1) else -1.0 for i in range(DIM)]
    _VEC_CACHE[tag] = vec
    return vec


def _normalize_en_word(word: str) -> str:
    """Lightweight inflection stripping (no regex) so 'files'->'file', 'disabling'->'disable'."""
    if word in _EN_SEMEMES or word in NEGATION_EN:
        return word
    if len(word) > 4 and word.endswith("ies"):
        cand = word[:-3] + "y"
        if cand in _EN_SEMEMES:
            return cand
    if len(word) > 4 and word.endswith("ing"):
        for cand in (word[:-3], word[:-3] + "e"):
            if cand in _EN_SEMEMES:
                return cand
    if len(word) > 3 and word.endswith("ed"):
        for cand in (word[:-2], word[:-1]):
            if cand in _EN_SEMEMES:
                return cand
    if len(word) > 3 and word.endswith("es"):
        cand = word[:-2]
        if cand in _EN_SEMEMES:
            return cand
    if len(word) > 3 and word.endswith("s"):
        cand = word[:-1]
        if cand in _EN_SEMEMES:
            return cand
    return word


def extract_sememes(text: str) -> tuple[list[tuple[str, str]], list[str], bool]:
    """Forward Maximum Matching (CJK) + phrase/word matching (EN).

    Returns ``(matched_sememes, residual_tokens, negated)``.
    """
    s = clean(text)
    matched: list[tuple[str, str]] = []
    residuals: list[str] = []
    negated = False

    def _emit(pairs: tuple[tuple[str, str], ...]) -> None:
        for pair in pairs:
            matched.append(pair)
            imp = _IMPLIED_SEMEMES.get(pair)
            if imp is not None:
                matched.append(imp)

    # Segment into alternating CJK runs and non-CJK runs
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if _wide(ch):
            # Gather contiguous wide-character span and apply longest-match FMM
            j = i
            while j < n and _wide(s[j]):
                j += 1
            span = s[i:j]
            pos = 0
            slen = len(span)
            while pos < slen:
                # Longest match across sememes, fillers, and negation markers (prevents '别人' -> '别' + '人')
                sem_hit = None
                sem_len = 0
                for L in range(min(_MAX_ZH_LEN, slen - pos), 0, -1):
                    sub = span[pos:pos + L]
                    if sub in _ZH_SEMEMES:
                        sem_hit = _ZH_SEMEMES[sub]
                        sem_len = L
                        break
                neg_len = 0
                for L in range(min(_MAX_NEG_ZH, slen - pos), 0, -1):
                    if span[pos:pos + L] in _NEG_ZH_SET:
                        neg_len = L
                        break
                fil_len = 0
                for L in range(min(_MAX_ZH_LEN, slen - pos), 0, -1):
                    if span[pos:pos + L] in _FILLER_ZH:
                        fil_len = L
                        break

                best_len = max(sem_len, neg_len, fil_len)
                if best_len == 0:
                    residuals.append(span[pos])
                    pos += 1
                elif sem_len == best_len and sem_hit is not None:
                    _emit(sem_hit)
                    pos += sem_len
                elif neg_len == best_len:
                    negated = True
                    pos += neg_len
                else:
                    pos += fil_len
            i = j
        elif ch.isalnum() or ch == "'":
            j = i
            while j < n and (s[j].isalnum() or s[j] in ("'", "-")):
                j += 1
            # Gather consecutive ASCII words so 2-word EN phrases ("turn off", "shut down") match via FMM
            words: list[str] = []
            k = i
            while k < n:
                if _wide(s[k]):
                    break
                if s[k].isalnum() or s[k] == "'":
                    start = k
                    while k < n and (s[k].isalnum() or s[k] in ("'", "-")):
                        k += 1
                    w = s[start:k].strip("-")
                    if w:
                        words.append(w)
                elif s[k].isspace():
                    k += 1
                else:
                    break
            pos = 0
            wlen = len(words)
            while pos < wlen:
                w0 = words[pos]
                w0_clean = w0.replace("'", "")
                if w0 in NEGATION_EN or w0_clean in NEGATION_EN:
                    negated = True
                    pos += 1
                    continue
                if pos + 1 < wlen and f"{w0} {words[pos + 1]}" == "do not":
                    negated = True
                    pos += 2
                    continue
                # Try 3-word then 2-word phrase FMM first
                if pos + 2 < wlen:
                    tri = f"{w0} {words[pos + 1]} {words[pos + 2]}"
                    if tri in _EN_SEMEMES:
                        _emit(_EN_SEMEMES[tri])
                        pos += 3
                        continue
                if pos + 1 < wlen:
                    bi = f"{w0} {words[pos + 1]}"
                    if bi in _EN_SEMEMES:
                        _emit(_EN_SEMEMES[bi])
                        pos += 2
                        continue
                norm = _normalize_en_word(w0)
                if norm in _EN_SEMEMES:
                    _emit(_EN_SEMEMES[norm])
                elif w0 not in _FILLER_EN and len(w0) >= 2:
                    residuals.append(w0)
                pos += 1
            i = max(j, k)
        else:
            i += 1
    return matched, residuals, negated


def encode_float(text: str):
    """Build the unquantized 1024-dim bipolar VSA superposition vector for ``text``.

    Superposes role-filler bindings ``ROLE ⊗ SEMEME`` plus predicate-argument relational bindings
    ``PAIR ⊗ (ACT_sem ⊗ OBJ_sem)`` (falling back to ``ACT_sem ⊗ RES_tok`` when an action has no known
    object sememe, e.g., ``关掉灯`` vs ``关掉杀毒软件``).
    """
    matched, residuals, negated = extract_sememes(text)
    unique_matched: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pair in matched:
        if pair not in seen:
            seen.add(pair)
            unique_matched.append(pair)
    acts = [sid for role, sid in unique_matched if role == ROLE_ACT]
    objs = [sid for role, sid in unique_matched if role == ROLE_OBJ]
    act_sign = -1.0 if negated else 1.0

    if np is not None:
        acc = np.zeros(DIM, dtype=np.float32)
        for role, sid in unique_matched:
            r_vec = _bipolar_vec(f"ROLE:{role}")
            s_vec = _bipolar_vec(f"SEM:{sid}")
            sign = act_sign if role == ROLE_ACT else 1.0
            acc += (sign * ROLE_WEIGHTS[role]) * (r_vec * s_vec)
        r_pair = _bipolar_vec(f"ROLE:{ROLE_PAIR}")
        if acts and objs:
            pair_w = (act_sign * ROLE_WEIGHTS[ROLE_PAIR]) / (len(acts) * len(objs)) ** 0.5
            for a_id in acts:
                va = _bipolar_vec(f"SEM:{a_id}")
                for o_id in objs:
                    acc += pair_w * (r_pair * va * _bipolar_vec(f"SEM:{o_id}"))
        elif acts and residuals:
            pair_w = (act_sign * ROLE_WEIGHTS[ROLE_PAIR]) / (len(acts) * len(residuals)) ** 0.5
            for a_id in acts:
                va = _bipolar_vec(f"SEM:{a_id}")
                for tok in residuals:
                    acc += pair_w * (r_pair * va * _bipolar_vec(f"RES:{tok}"))
        if negated:
            acc += ROLE_WEIGHTS[ROLE_NEG] * (_bipolar_vec(f"ROLE:{ROLE_NEG}") * _bipolar_vec("SEM:NEGATED"))
        r_res = _bipolar_vec(f"ROLE:{ROLE_RESIDUAL}")
        for tok in residuals:
            acc += RESIDUAL_WEIGHT * (r_res * _bipolar_vec(f"RES:{tok}"))
        return acc

    acc_py = [0.0] * DIM
    for role, sid in unique_matched:
        r_vec = _bipolar_vec(f"ROLE:{role}")
        s_vec = _bipolar_vec(f"SEM:{sid}")
        w = (act_sign if role == ROLE_ACT else 1.0) * ROLE_WEIGHTS[role]
        for idx in range(DIM):
            acc_py[idx] += w * (r_vec[idx] * s_vec[idx])
    r_pair = _bipolar_vec(f"ROLE:{ROLE_PAIR}")
    if acts and objs:
        pair_w = (act_sign * ROLE_WEIGHTS[ROLE_PAIR]) / (len(acts) * len(objs)) ** 0.5
        for a_id in acts:
            va = _bipolar_vec(f"SEM:{a_id}")
            for o_id in objs:
                vo = _bipolar_vec(f"SEM:{o_id}")
                for idx in range(DIM):
                    acc_py[idx] += pair_w * (r_pair[idx] * va[idx] * vo[idx])
    elif acts and residuals:
        pair_w = (act_sign * ROLE_WEIGHTS[ROLE_PAIR]) / (len(acts) * len(residuals)) ** 0.5
        for a_id in acts:
            va = _bipolar_vec(f"SEM:{a_id}")
            for tok in residuals:
                vt = _bipolar_vec(f"RES:{tok}")
                for idx in range(DIM):
                    acc_py[idx] += pair_w * (r_pair[idx] * va[idx] * vt[idx])
    if negated:
        r_neg = _bipolar_vec(f"ROLE:{ROLE_NEG}")
        s_neg = _bipolar_vec("SEM:NEGATED")
        w_neg = ROLE_WEIGHTS[ROLE_NEG]
        for idx in range(DIM):
            acc_py[idx] += w_neg * (r_neg[idx] * s_neg[idx])
    r_res = _bipolar_vec(f"ROLE:{ROLE_RESIDUAL}")
    for tok in residuals:
        s_tok = _bipolar_vec(f"RES:{tok}")
        for idx in range(DIM):
            acc_py[idx] += RESIDUAL_WEIGHT * (r_res[idx] * s_tok[idx])
    return acc_py


def quantize_pack(vec) -> bytes:
    """Parity tie-breaking binary quantization packed into 128 bytes.

    Uses ``(vec > 0) | ((vec == 0) & ((arange(DIM) & 1).astype(bool)))`` so zero-valued
    dimensions alternate 0/1 instead of collapsing into an all-ones vector.
    """
    if np is not None:
        arr = np.asarray(vec, dtype=np.float32)
        parity = (np.arange(DIM) & 1).astype(bool)
        bits = (arr > 0) | ((arr == 0) & parity)
        return np.packbits(bits.astype(np.uint8)).tobytes()

    out = bytearray(PACKED_BYTES)
    for idx in range(DIM):
        v = vec[idx]
        bit = 1 if (v > 0.0 or (v == 0.0 and (idx & 1) == 1)) else 0
        if bit:
            out[idx >> 3] |= 1 << (7 - (idx & 7))
    return bytes(out)


def encode_packed(text: str) -> bytes:
    """Encode ``text`` directly into a 128-byte packed Sememe-VSA binary key."""
    return quantize_pack(encode_float(text))


_PASSIVE_ZH = ("被", "遭到", "有人", "把我", "对我", "威胁我", "骗我", "偷我", "盗我")
_PASSIVE_EN = frozenset({"got", "was", "were", "been", "someone", "somebody"})


def is_passive_voice(text: str, matched: list[tuple[str, str]] | None = None) -> bool:
    """Detect passive or victim-report voice ('my account got hacked', 'someone threatened me', '我被骗了')
    so victim reports never collide with active imperative requests ('crack her password', 'murder my boss')."""
    s = clean(text)
    if any(m in s for m in _PASSIVE_ZH):
        return True
    words = [w.strip(".,!?;:\"'()-") for w in s.split()]
    wset = set(words)
    if wset & _PASSIVE_EN:
        return True
    # Syntactic order check: if a sentence contains both OBJ and ACT (e.g. 'my account ... hacked' or
    # 'someone threatened me') with OBJ strictly before ACT and an object-pronoun 'me' or past '-ed' verb,
    # it is a report rather than an imperative command.
    if matched is None:
        matched, _, _ = extract_sememes(text)
    first_act = next((i for i, (r, _) in enumerate(matched) if r == ROLE_ACT), -1)
    first_obj = next((i for i, (r, _) in enumerate(matched) if r == ROLE_OBJ), -1)
    if first_obj >= 0 and first_act > first_obj and ("me" in wset or any(w.endswith("ed") and len(w) > 4 for w in words)):
        return True
    return False


_CLAUSE_SEPS = (",", "，", ";", "；")


def split_clauses(text: str) -> list[str]:
    """Split a compound sentence into clause spans with zero regex."""
    out: list[str] = []
    buf: list[str] = []
    for ch in text:
        if ch in _CLAUSE_SEPS:
            seg = "".join(buf).strip()
            if seg:
                out.append(seg)
            buf = []
        else:
            buf.append(ch)
    seg = "".join(buf).strip()
    if seg:
        out.append(seg)
    return out or [text]


def clause_polarity(text: str) -> tuple[bool, bool, bool, int]:
    """Clause-local negation analysis: ``(any_neg, all_neg, has_affirm_act, clause_count)``.

    Distinguishes pure negation ('不要删掉我的文件' -> ``all_neg=True, has_affirm_act=False``)
    from mixed compound tasks ('照片先别删，帮我整理一下' -> ``any_neg=True, all_neg=False, has_affirm_act=True``).
    """
    clauses = split_clauses(text)
    if len(clauses) <= 1:
        matched, _, neg = extract_sememes(text)
        has_act = any(r == ROLE_ACT for r, _ in matched)
        return neg, neg, (has_act and not neg), 1
    any_neg = False
    neg_clauses = 0
    nonempty = 0
    has_affirm_act = False
    for cl in clauses:
        m, res, neg = extract_sememes(cl)
        if not m and not res and not neg:
            continue
        nonempty += 1
        if neg:
            any_neg = True
            neg_clauses += 1
        elif any(r == ROLE_ACT for r, _ in m):
            has_affirm_act = True
    all_neg = nonempty > 0 and neg_clauses == nonempty
    return any_neg, all_neg, has_affirm_act, len(clauses)


def sememe_profile(text: str) -> tuple[bytes, int, bool, bool, bool]:
    """Return ``(packed_128b, unique_sememe_count, all_negated, is_query, is_greeting)``."""
    matched, _, _ = extract_sememes(text)
    _, all_neg, _, _ = clause_polarity(text)
    uniq = set(matched)
    is_query = (ROLE_MOD, "QUERY") in uniq or "?" in text or "？" in text
    is_greeting = (ROLE_CTX, "GREETING") in uniq
    return encode_packed(text), len(uniq), all_neg, is_query, is_greeting


def hamming_similarity_bytes(a: bytes, b: bytes) -> float:
    """Bipolar-calibrated Hamming similarity in [0.0, 1.0] between two 128-byte keys."""
    if len(a) != PACKED_BYTES or len(b) != PACKED_BYTES:
        raise ValueError(f"expected {PACKED_BYTES}-byte packed keys")
    if np is not None and _POPCNT_NP is not None:
        va = np.frombuffer(a, dtype=np.uint8)
        vb = np.frombuffer(b, dtype=np.uint8)
        dist = int(_POPCNT_NP[np.bitwise_xor(va, vb)].sum())
    else:
        dist = sum(_POPCNT_PY[x ^ y] for x, y in zip(a, b, strict=True))
    return max(0.0, 1.0 - 2.0 * dist / DIM)


def calibrate_novelty(max_sim: float, *, gamma: float = NOVELTY_GAMMA) -> float:
    """Non-linear novelty calibration aligned to the 0.25 familiarity threshold."""
    s = max(0.0, min(1.0, float(max_sim)))
    return float((1.0 - s) ** gamma)


@dataclass
class MeaningStore:
    """Compact 128-byte/item semantic familiarity store with XOR+POPCNT retrieval."""

    _keys_list: list[bytes] = field(default_factory=list)
    _seen: set[bytes] = field(default_factory=set)
    _matrix: object = field(default=None, repr=False)

    def __len__(self) -> int:
        return len(self._keys_list)

    def copy(self) -> MeaningStore:
        dup = MeaningStore(list(self._keys_list), set(self._seen))
        if np is not None and self._matrix is not None:
            dup._matrix = self._matrix.copy()
        return dup

    def _rebuild_matrix(self) -> None:
        if np is None:
            self._matrix = None
            return
        if not self._keys_list:
            self._matrix = np.zeros((0, PACKED_BYTES), dtype=np.uint8)
            return
        self._matrix = np.frombuffer(b"".join(self._keys_list), dtype=np.uint8).reshape(len(self._keys_list), PACKED_BYTES).copy()

    def add(self, texts: Iterable[str]) -> int:
        added = 0
        for t in texts:
            if not t or not t.strip():
                continue
            key = encode_packed(t)
            if key not in self._seen:
                self._seen.add(key)
                self._keys_list.append(key)
                added += 1
        if added and np is not None:
            self._rebuild_matrix()
        return added

    def max_similarity(self, text: str) -> float:
        if not self._keys_list or not text or not text.strip():
            return 0.0
        key = encode_packed(text)
        if key in self._seen:
            return 1.0
        if np is not None and _POPCNT_NP is not None:
            if self._matrix is None or len(self._matrix) != len(self._keys_list):
                self._rebuild_matrix()
            q = np.frombuffer(key, dtype=np.uint8)
            dists = _POPCNT_NP[np.bitwise_xor(self._matrix, q)].sum(axis=1)
            min_dist = int(dists.min())
            return max(0.0, 1.0 - 2.0 * min_dist / DIM)
        best = 0.0
        for row in self._keys_list:
            sim = hamming_similarity_bytes(row, key)
            if sim > best:
                best = sim
        return best

    def novelty(self, text: str) -> tuple[float, tuple[str, ...]]:
        """Return ``(calibrated_novelty, unfamiliar_spans)``."""
        if not text or not text.strip():
            return 0.0, ()
        if not self._keys_list:
            matched, residuals, _ = extract_sememes(text)
            spans = tuple(dict.fromkeys([sid.lower() for _, sid in matched] + residuals))
            return 1.0, spans[:6]
        sim = self.max_similarity(text)
        nov = calibrate_novelty(sim)
        if nov < 0.25:
            return nov, ()
        matched, residuals, _ = extract_sememes(text)
        spans = tuple(dict.fromkeys(residuals + [sid.lower() for _, sid in matched]))
        return nov, spans[:6]

    def pack(self) -> bytes:
        header = PACK_MAGIC + struct.pack("<I", len(self._keys_list))
        return header + b"".join(self._keys_list)

    @classmethod
    def unpack(cls, data: bytes) -> MeaningStore:
        if len(data) < len(PACK_MAGIC) + 4 or not data.startswith(PACK_MAGIC):
            raise ValueError("invalid Sememe-VSA MeaningStore buffer")
        count = struct.unpack("<I", data[len(PACK_MAGIC):len(PACK_MAGIC) + 4])[0]
        body = data[len(PACK_MAGIC) + 4:]
        if len(body) < count * PACKED_BYTES:
            raise ValueError("truncated Sememe-VSA MeaningStore buffer")
        keys = [body[i * PACKED_BYTES:(i + 1) * PACKED_BYTES] for i in range(count)]
        store = cls(keys, set(keys))
        store._rebuild_matrix()
        return store


__all__ = [
    "DIM",
    "MeaningStore",
    "NEGATION_EN",
    "NEGATION_ZH",
    "NOVELTY_GAMMA",
    "PACKED_BYTES",
    "RESIDUAL_WEIGHT",
    "calibrate_novelty",
    "clause_polarity",
    "encode_float",
    "encode_packed",
    "extract_sememes",
    "hamming_similarity_bytes",
    "is_passive_voice",
    "quantize_pack",
    "sememe_profile",
    "split_clauses",
]
