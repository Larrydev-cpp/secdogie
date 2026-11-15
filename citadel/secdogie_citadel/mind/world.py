"""The world model: what an act changes, and what follows from that.

Nothing here is about a particular request. It is what the mind knows about
how things go:

- **Acts** (``ACTS``): the weighty things a person may ask for. Each one
  only says which *effect* it has on the world (the thing is gone; they
  have the money; they know it; the program runs; someone was watched; many
  accounts exist), which facts it carries with it, and what people are
  usually after when they ask for it (``Goal``).
- **Rules** (``RULES``): what follows from what. "It is gone, and there is no
  other copy: it is lost." "It is lost, and later you will be sorry: regret."
  "They know the code, and they are not who they say: they can sign in as
  you." Each rule is general, and carries the one sentence that says it.
  ``closure`` applies them until nothing new follows, and keeps the chain.
- **Scene** (``scene``): one request as a small world: the act, what it is
  done to, the facts read from the words, the moment, the usage reader and
  the person's history, and how much is at stake.

The mind does not look up what a request leads to. It builds the scene and
lets the rules run (``plan.py``), once for every way the unknown things
(``belief.py``) could turn out.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import lexicon as L
from .perceive import Frame, Situation


@dataclass(frozen=True)
class Things:
    """What the usage reader and the person's own words say the request touches."""

    names: tuple[str, ...] = ()
    found: bool = False  # it is on this computer
    used: bool = False  # it is in use, or the person spoke of it
    recent: bool = False  # changed or opened in the past month
    unused: bool = False  # empty, or never touched since it arrived
    photos: bool = False  # what it holds is mostly photos and videos
    size: int = 0
    stories: tuple[tuple[str, ...], ...] = ()  # the usage story of each thing, sentence by sentence


NOTHING = Things()


@dataclass(frozen=True)
class Known:
    """What the mind has learned about this person from their own record: counts, not training data."""

    held: int = 0  # weighty steps it held back before
    steady: int = 0  # times they came back after a cooling-off, calm, and went ahead
    undone: int = 0  # times they asked to restore or undo something
    goals: tuple[tuple[str, str, int], ...] = ()  # (act, goal id, times it was plainly their reason)

    def times(self, act: str, gid: str) -> int:
        return next((n for a, g, n in self.goals if a == act and g == gid), 0)

    def prior(self, act: str, goal: Goal) -> float:
        """The goal's prior for this person: a Dirichlet with four pseudo-counts on the written-down priors,
        updated with the reasons they plainly gave before."""
        n = sum(n for a, _, n in self.goals if a == act)
        return (4 * goal.prior + self.times(act, goal.id)) / (4 + n) if n else goal.prior

    @property
    def autonomy_lift(self) -> float:  # 0 .. 0.25: their settled choices have held before
        return 0.25 * self.steady / (self.steady + 2)

    def cool_days(self, base: int) -> int:
        """A cooling-off shortens for someone whose choices held after one: 7, then 5, then 3 days."""
        return max(3, base - 2 * min(2, self.steady))


NOKNOWN = Known()


# ---- acts ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Goal:
    """What someone may be after when they ask for an act."""

    id: str
    prior: float
    atom: str  # the atom that, once true, means they got it
    zh: str
    en: str
    cues: tuple[tuple[str, float], ...] = ()  # (a fact of the scene, likelihood ratio)
    say_zh: str = ""  # what they could say that shows this is their reason
    say_en: str = ""
    if_zh: str = ""  # ... and the condition it states
    if_en: str = ""


@dataclass(frozen=True)
class Act:
    id: str
    effect: str  # what doing it makes true
    carries: tuple[str, ...]  # facts that come with the act itself
    unknowns: tuple[str, ...]  # the unknown things its consequences turn on
    goals: tuple[Goal, ...]
    steps: tuple[str, ...]  # the gentler steps that can stand in for it or go before it (plan.STEPS)
    zh: str  # the verb, as the companion says it: 删
    en: str
    it_zh: str  # the object, when the person named none: 这笔钱
    it_en: str
    undo_zh: str  # can it be taken back: knowledge about the act
    undo_en: str
    base: float = 0.5  # how much is at stake when the scene says nothing more


def _g(gid, prior, atom, zh, en, *cues, say_zh="", say_en="", if_zh="", if_en=""):
    return Goal(gid, prior, atom, zh, en, tuple(cues), say_zh, say_en, if_zh, if_en)


_GONE = ("space", "tidy", "unseen", "private", "rid")
_MESSAGE_GOALS = (
    _g("inform", 0.5, "said", "把事情说清楚", "to say how things are"),
    _g("vent", 0.08, "vented", "把气撒出来", "to let the anger out", ("angry", 6.0), ("hurtful", 6.0), ("late", 1.5)),
    _g("mend", 0.1, "mended", "想和好", "to make up", ("sorry_words", 5.0)),
    _g("end", 0.12, "said", "想做个了断", "to end it", ("breakup", 4.0), ("final", 4.0)),
    _g("plain", 0.2, "said", "就是要说这句话", "simply to say it"),
)
_MONEY_UNDO = ("钱转出去，多半追不回来。", "Money that has been sent is mostly gone.")

ACTS = {a.id: a for a in (
    Act("delete", "gone", (), ("later", "copy"), (
        _g("space", 0.22, "freed", "腾出地方", "to free up space", ("space_words", 6.0), ("big", 1.6), ("upset", 0.4)),
        _g("tidy", 0.2, "tidied", "让眼前清爽一点", "to tidy things up", ("tidy_words", 5.0), ("junk", 4.0), ("upset", 0.5)),
        _g("unseen", 0.18, "unseen", "不想再看到它们", "not to see them any more",
           ("breakup", 4.0), ("sad", 2.5), ("with_person", 2.5), ("unsee_words", 5.0), ("angry", 3.0)),
        _g("private", 0.1, "private", "不想让别人看到", "to keep others from seeing it", ("asked_to_delete", 8.0)),
        _g("rid", 0.3, "rid", "就是不要了", "simply not wanting it"),
    ), ("hide", "lock", "move_out", "wait"), "删", "delete", "它们", "them",
        "删除是改不回来的一步；就算先进了废纸篓，清空以后也就没有了。",
        "Deleting can't be undone; even from the trash, once it is emptied it is gone.", 0.35),
    Act("wipe", "gone", ("whole",), ("later", "copy"), (
        _g("sell", 0.2, "gone", "要把它卖掉或者送人", "to sell it or give it away", ("sell_words", 6.0)),
        _g("fix", 0.25, "gone", "让它重新好用", "to get it working well again", ("fix_words", 5.0)),
        _g("fresh", 0.1, "gone", "想把一切清掉重来", "to clear everything and start over", ("upset", 4.0), ("breakup", 3.0)),
        _g("rid", 0.45, "gone", "就是想清空它", "simply wanting it cleared"),
    ), ("copy_first", "wait"), "格式化", "format", "这块盘", "this disk",
        "格式化以后，里面的东西再也找不回来。", "After formatting, nothing on it can be brought back.", 0.9),
    Act("close_account", "gone", ("account",), ("later", "copy"), (
        _g("private", 0.25, "private", "不想再留下痕迹", "not to leave traces behind", ("privacy_words", 5.0)),
        _g("leave", 0.3, "unseen", "不想再用它", "not to use it any more", ("leave_words", 4.0)),
        _g("rid", 0.45, "rid", "就是要注销", "simply closing it"),
    ), ("hide", "copy_first", "wait"), "注销", "close", "这个账号", "this account",
        "注销以后，账号连同里面的聊天记录、照片，会一起没了。", "Once closed, the account goes, with its messages and photos.", 0.75),
    Act("transfer", "given", (), ("party",), (
        _g("help", 0.3, "reached", "帮家里人", "to help family", ("to_family", 4.0)),
        _g("due", 0.25, "reached", "付该付的钱", "to pay what is owed", ("due_words", 5.0),
           say_zh="是房租，我确认过收款人了", say_en="It's the rent, and I verified the payee", if_zh="这是该付的钱",
           if_en="it is money you owe"),
        _g("gift", 0.15, "reached", "送一份心意", "to send a gift", ("gift_words", 4.0)),
        _g("grow", 0.1, "grown", "投资赚钱", "to invest", ("returns_words", 5.0)),
        _g("told", 0.05, "reached", "有人让你转", "because someone told you to", ("told_to", 4.0)),
        _g("plain", 0.15, "reached", "就是要转这笔钱", "simply sending this money"),
    ), ("verify", "shrink"), "转", "send", "这笔钱", "this money", *_MONEY_UNDO, 0.4),
    Act("lend", "given", ("loan",), ("party",), (
        _g("help", 0.45, "reached", "帮人渡个难关", "to help someone through a hard patch", ("to_family", 3.0), ("long_known", 3.0)),
        _g("told", 0.1, "reached", "对方开口找你借", "because they asked you for it", ("told_to", 3.0)),
        _g("plain", 0.45, "reached", "就是要借给对方", "simply lending it"),
    ), ("verify", "shrink"), "借", "lend", "这笔钱", "this money",
        "借出去的钱不一定回得来，还常常连带着交情。", "A loan does not always come back, and it often takes a friendship with it.", 0.4),
    Act("share_secret", "told", ("credential",), ("party",), (
        _g("told", 0.4, "delivered", "对方找你要", "because they asked you for it", ("told_to", 3.0)),
        _g("help", 0.3, "delivered", "帮家里人办事", "to help family with something", ("to_family", 4.0)),
        _g("own", 0.15, "delivered", "用在你自己的另一台设备上", "to use it on another device of your own", ("own_device", 6.0)),
        _g("plain", 0.15, "delivered", "就是要给对方", "simply giving it to them"),
    ), ("verify", "direct"), "给出去", "hand over", "这个", "this",
        "验证码、密码一旦到了别人手里，转走钱、登上账号，只要几秒，收不回来。",
        "With a code or a password, moving money or signing in takes seconds. It can't be taken back.", 0.85),
    Act("send", "told", (), ("later",), _MESSAGE_GOALS, ("wait", "soften"), "发", "send", "这条消息", "this message",
        "话发出去，就收不回来了。", "Words that have been sent can't be taken back.", 0.5),
    Act("post", "told", ("public",), ("later",), _MESSAGE_GOALS, ("wait", "soften"), "发", "post", "这条动态", "this post",
        "发出去的话收不回来，看到的人会一直记得。", "What is posted can't be withdrawn, and those who saw it remember.", 0.9),
    Act("resign", "told", ("final",), ("later",), _MESSAGE_GOALS, ("wait", "soften"), "发", "send", "这封辞职信", "the resignation",
        "辞职信发出去，就收不回来了。", "A resignation, once sent, can't be taken back.", 0.9),
    Act("run_unknown", "running", (), ("party",), (
        _g("work", 0.3, "works", "工作上要用", "needing it for work", ("work_source", 5.0),
           say_zh="是公司 IT 发的，官网下载的", say_en="It's from IT at work, from the official site", if_zh="是工作上要用、来源可靠",
           if_en="it is for work, from a source you trust"),
        _g("free", 0.15, "works", "想用破解版、外挂，省点钱或者省点事", "to use a cracked copy or a cheat", ("cracked", 6.0)),
        _g("fix", 0.1, "works", "装不上，想先把防护关掉", "it won't install, so the protection has to go", ("shield", 8.0)),
        _g("plain", 0.45, "works", "就是要运行它", "simply running it"),
    ), ("verify",), "运行", "run", "这个东西", "this",
        "它一运行，能读你的文件、装上别的东西、偷走登录信息；运行过了，就很难说清它做过什么。",
        "Once it runs it can read your files, install other things and take your sign-ins. Afterwards it is hard to say what it did.",
        0.8),
    Act("track", "watched", (), (), (
        _g("know", 0.35, "learned", "心里不踏实，想知道对方在做什么", "being uneasy and wanting to know what they are doing",
           ("suspect_words", 4.0), ("to_partner", 3.0)),
        _g("cling", 0.15, "learned", "放不下前任", "not being able to let an ex go", ("to_ex", 8.0)),
        _g("safe", 0.2, "located", "担心对方的安全", "worrying about their safety", ("safety_words", 5.0), ("kin_only", 2.0)),
        _g("plain", 0.3, "learned", "就是想看看", "simply wanting to look"),
    ), ("ask_them", "share", "ask_around"), "偷偷看", "look in secret at", "对方", "them",
        "看过了就收不回来；被对方知道，信任也回不来。", "Once seen it can't be unseen; and if they find out, the trust does not come back.", 0.5),
    Act("bulk", "made_many", (), (), (
        _g("test", 0.25, "tested", "在自己的系统里测试", "to test in your own system", ("own_system", 12.0), ("builder", 3.0),
           say_zh="在公司的 staging 测试环境注册十个测试账号", say_en="Register ten test accounts on our staging environment",
           if_zh="是在你自己的系统里测试", if_en="it is a test in your own system"),
        _g("share", 0.2, "shared_out", "给家里人或者同事用", "for family or colleagues to use", ("for_others", 6.0), ("to_family", 2.0)),
        _g("reap", 0.2, "made_many", "多领几份优惠或者奖励", "to collect an offer or a reward several times", ("promo_words", 6.0)),
        _g("plain", 0.35, "made_many", "就是要这么多账号", "simply wanting that many accounts"),
    ), ("proper",), "注册", "register", "这些账号", "these accounts",
        "别人的平台一般不许一人多号；被发现时，连你自己的主号都可能一起被封。",
        "Platforms don't allow one person many accounts. When it is noticed, your own main account may be banned with them.", 0.5),
)}


# ---- rules: what follows from what ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    id: str
    needs: tuple[str, ...]  # all of these hold
    gives: str  # then this holds
    strength: float = 1.0  # how fully
    lacks: tuple[str, ...] = ()  # and none of these
    zh: str = ""  # the sentence that says it ("" for a step too small to say)
    en: str = ""


RULES = (
    # something is gone
    Rule("lost", ("gone", "nocopy"), "lost", lacks=("copied",), zh="没有别的副本，这一步之后就再也找不回来",
         en="there is no other copy, so after this it can't be brought back"),
    Rule("regret", ("lost", "sorry"), "regret", zh="过后你想把它要回来，可已经没有了", en="later you want it back, and it is gone"),
    Rule("closed", ("gone", "account", "sorry"), "regret", 0.6, zh="记录可以导出，账号本身和里面的联系人却回不来",
         en="the records can be exported, but the account and its contacts do not come back"),
    Rule("freed", ("gone",), "freed"), Rule("freed2", ("moved_out",), "freed", 0.95, zh="这台电脑腾出了地方，东西还在别处",
                                            en="this computer gets the room back, and the things are kept elsewhere"),
    Rule("unseen", ("gone",), "unseen"), Rule("unseen2", ("hidden",), "unseen", 0.85, zh="眼前看不见，但它还在，想反悔随时可以",
                                              en="it is out of sight but still there, and you can change your mind any time"),
    Rule("unseen3", ("moved_out",), "unseen", 0.5), Rule("unseen4", ("locked",), "unseen", 0.7),
    Rule("private", ("gone",), "private"), Rule("private2", ("locked",), "private", 0.9, zh="换一个只有你知道的口令，别人打不开，你自己还留着",
                                                en="a passphrase only you know: no one else can open it, and you still have it"),
    Rule("private3", ("hidden",), "private", 0.45), Rule("private4", ("moved_out",), "private", 0.3),
    Rule("tidied", ("gone",), "tidied"), Rule("tidied2", ("hidden",), "tidied", 0.7), Rule("tidied3", ("moved_out",), "tidied", 0.8),
    Rule("tidied4", ("locked",), "tidied", 0.5),
    Rule("rid", ("gone",), "rid"), Rule("rid2", ("hidden",), "rid", 0.6), Rule("rid3", ("moved_out",), "rid", 0.6),
    Rule("rid4", ("locked",), "rid", 0.5),
    # money changes hands
    Rule("taken", ("given", "hostile"), "money_lost", zh="钱到了对方手里，就追不回来",
         en="once they have the money, it does not come back"),
    Rule("reached", ("given", "honest"), "reached", zh="钱到了该到的人手里", en="the money reaches the person it is meant for"),
    Rule("grown", ("reached",), "grown", 0.75),
    # someone learns something
    Rule("hijack", ("told", "credential", "hostile"), "account_taken", lacks=("direct",), zh="对方拿到它，就能以你的名义登录",
         en="with it, they can sign in as you"),
    Rule("drained", ("account_taken",), "money_lost", zh="账号里的钱和信息，几秒钟就能被转走",
         en="what is in the account can be moved out within seconds"),
    Rule("delivered", ("told", "credential", "honest"), "delivered", zh="对方办完了你要办的事", en="they do what you needed done"),
    Rule("delivered2", ("direct", "honest"), "delivered", 0.9,
         zh="当面说，或者打你自己存的号码说，冒充的人截不走", en="said in person, or on a call to a number you saved, an impostor can't catch it"),
    Rule("said", ("told",), "said"), Rule("vented", ("told",), "vented"),
    Rule("vented2", ("drafted",), "vented", 0.6, zh="话写下来了，一个字也没丢，气也出了一半", en="the words are written down and kept, and half the anger is out"),
    Rule("hurt", ("told", "hurtful"), "they_hurt", lacks=("softened",), zh="这句话会伤到对方", en="these words will hurt them"),
    Rule("mended", ("said",), "mended", lacks=("they_hurt",)),
    Rule("unsaid", ("told", "sorry"), "regret", lacks=("softened",), zh="等你平静下来，会希望这句话没有发出去",
         en="once you are calm, you wish it had not been sent"),
    Rule("unsaid2", ("told", "sorry", "softened"), "regret", 0.5),
    # a program runs
    Rule("owned", ("running", "hostile"), "computer_taken", zh="它一运行，就能动这台电脑上的一切：文件、密码、登录着的账号",
         en="once it runs it can touch everything on this computer: files, passwords, the accounts you are signed in to"),
    Rule("works", ("running", "honest"), "works", zh="它装好了，能用", en="it installs and works"),
    # someone is watched
    Rule("wronged", ("watched",), "they_wronged", 0.8, lacks=("goal:safe", "goal:cling"),
         zh="不经同意看别人的私事，伤到的是对方，也是你们之间的信任", en="looking without their say-so wrongs them, and breaks the trust between you"),
    Rule("wronged2", ("watched", "goal:cling"), "they_wronged", zh="对方已经离开了你的生活，这是在闯进去",
         en="they have left your life, and this is forcing a way back in"),
    Rule("wronged3", ("watched", "goal:safe"), "they_wronged", 0.2),
    Rule("unlawful", ("they_wronged",), "rule_broken"),
    Rule("learned", ("watched",), "learned", 0.7), Rule("learned2", ("asked_them",), "learned", 0.65,
                                                       zh="直接问，比偷偷看更能知道你想知道的，也不伤信任",
                                                       en="asking tells you more than looking in secret, and keeps the trust"),
    Rule("learned3", ("shared",), "learned", 0.35),
    Rule("located", ("watched",), "located", 0.6), Rule("located2", ("shared",), "located", 0.8,
                                                        zh="对方知道，也同意，担心的时候随时能看到", en="they know and agree, and you can look whenever you worry"),
    Rule("located3", ("asked_around",), "located", 0.9, zh="先打给对方身边的人，问问最后见到的人；真的找不到，马上报警",
         en="the people around them are asked first; if they truly can't be found, the police are called at once"),
    # many accounts
    Rule("tested", ("made_many", "goal:test"), "tested", zh="是你自己的系统，测试账号本来就该这么建", en="it is your own system, where test accounts belong"),
    Rule("breaks", ("made_many",), "rule_broken", 0.65, lacks=("goal:test", "goal:share"),
         zh="别人的平台不许一人多号", en="someone else's platform does not allow one person many accounts"),
    Rule("breaks2", ("made_many", "goal:share"), "rule_broken", 0.2),
    Rule("abuse", ("made_many", "goal:reap"), "others_harmed", 0.5, zh="成批的新账号去领优惠，占的是别人的份", en="offers collected in bulk are taken from other people's share"),
    Rule("abuse2", ("made_many", "goal:plain"), "others_harmed", 0.3, zh="成批的新账号，常被拿去刷单、骗人、骚扰别人",
         en="new accounts in bulk are what fake reviews, fraud and harassment are prepared with"),
    Rule("shared_out", ("made_many",), "shared_out"), Rule("shared_out2", ("each_own",), "shared_out", 0.85,
                                                           zh="每个人用自己的手机号注册，账号是各自的，谁也不会被连累",
                                                           en="each person registers with their own number: the accounts are their own, and no one is put at risk"),
    Rule("tested2", ("each_own",), "tested", 0.9),
)

# what a derived fact costs, and whom: (kind of cost, weight)
COSTS = {"money_lost": ("self", 1.0), "computer_taken": ("self", 1.0), "regret": ("regret", 1.0), "they_hurt": ("others", 0.35),
         "they_wronged": ("others", 1.0), "others_harmed": ("others", 1.0), "rule_broken": ("norm", 1.0)}


def closure(atoms: dict[str, float]) -> tuple[dict[str, float], tuple[tuple[Rule, float], ...]]:
    """Apply the rules to ``atoms`` (what is true, and how fully) until nothing new follows. Returns everything
    that holds and the chain of rules that fired, in order."""
    state = dict(atoms)
    chain: list[tuple[Rule, float]] = []
    fired: set[str] = set()
    changed = True
    while changed:
        changed = False
        for r in RULES:
            if r.id in fired or any(state.get(a, 0.0) > 0 for a in r.lacks):
                continue
            if all(state.get(a, 0.0) > 0 for a in r.needs):
                degree = min(state[a] for a in r.needs) * r.strength
                fired.add(r.id)
                if degree > state.get(r.gives, 0.0):
                    state[r.gives] = degree
                    chain.append((r, degree))
                    changed = True
    return state, tuple(chain)


# What a fact of the scene is, in words: (Chinese, English). A fact the letter never names has no entry.
FACTS = {
    "dear": ("里面是照片、聊天记录这样的回忆", "it holds memories: photos, messages"), "with_person": ("里面有另一个人", "someone else is in them"),
    "in_use": ("它还在用", "it is still in use"), "recent": ("最近还动过它", "it was changed or opened lately"),
    "unused": ("它放进来以后从没动过", "it has not been touched since it arrived"), "junk": ("是些用不着的东西", "they are things no one needs"),
    "work": ("里面是你的工作", "it holds your work"), "big": ("它占了不少地方", "it takes up a lot of room"),
    "space_words": ("你说到空间", "you mention space"), "tidy_words": ("你说到没用的、重复的", "you call them useless or duplicates"),
    "unsee_words": ("你说不想再看到", "you say you don't want to see them"), "asked_to_delete": ("有人要你删", "someone asked you to delete it"),
    "sell_words": ("你说要转手", "you say it is changing hands"), "fix_words": ("你说它不好用了", "you say it isn't working well"),
    "backed": ("你说备份过了，或者是一块新盘", "you say it is backed up, or new"), "privacy_words": ("你在意隐私", "you care about privacy"),
    "leave_words": ("你说已经不用它了", "you say you no longer use it"), "credential": ("这是一把钥匙：谁拿到它，谁就能以你的名义登录、付款",
                                                                        "this is a key: whoever holds it can sign in and pay as you"),
    "hurtful": ("话说得很重", "the words are harsh"), "public": ("是发给所有人看的", "it goes out for everyone to see"),
    "final": ("这是一封辞职信", "it is a resignation"), "to_family": ("对方是家里人", "it is for family"), "to_friend": ("对方是认识的人", "you know them"),
    "to_boss": ("对方是你的上司", "it goes to your boss"), "to_ex": ("对方是前任", "they are your ex"), "to_partner": ("对方是你的另一半", "they are your partner"),
    "to_stranger": ("对方的身份你没法确认", "you can't verify who they are"), "scam_words": ("对方用的是骗子常用的说法", "they use the words scammers use"),
    "safe_account": ("「安全账户」只在骗局里出现", "a “safe account” only ever appears in scams"),
    "secrecy": ("对方让你别告诉别人", "they tell you to keep it secret"), "official": ("对方自称客服、银行或者警察", "they claim to be support, a bank or the police"),
    "verified": ("你说确认过了", "you say you verified it"), "due_words": ("是该付的钱", "it is money owed"), "gift_words": ("是一份心意", "it is a gift"),
    "returns_words": ("说到带你赚钱", "it promises to make you money"), "told_to": ("是对方让你这样做的", "they told you to"),
    "own_device": ("是给你自己用", "it is for your own use"), "work_source": ("是工作上、或者从官方来的", "it is from work or an official source"),
    "prize": ("来路像是骗局", "where it comes from looks like a scam"), "cracked": ("是破解版或者外挂", "it is a crack or a cheat"),
    "shield": ("你要关掉的是电脑的防护", "it is the computer's protection you would turn off"),
    "long_known": ("你们认识很久了，或者是当面", "you have known them a long time, or it is in person"), "sorry_words": ("你想和好", "you want to make up"),
    "suspect_words": ("你在怀疑", "you suspect something"), "safety_words": ("你在担心对方的安全", "you fear for their safety"),
    "kin_only": ("是家里人", "they are family"), "own_system": ("是你自己的测试环境", "it is your own test environment"),
    "builder": ("你做测试或者开发", "you test or build software"), "for_others": ("是给别人用", "it is for others to use"),
    "promo_words": ("说到优惠、抽奖", "offers or prize draws are mentioned"), "large_sum": ("数目不小", "it is a large sum"),
    "upset": ("你现在心里不平静", "you are upset right now"), "sad": ("你很难过", "you are sad"), "angry": ("你在生气", "you are angry"),
    "rushed": ("你被催得很急", "you are being rushed"), "breakup": ("你说到分开", "you speak of a breakup"), "late": ("夜很深了", "it is very late"),
    "impulse": ("你说得很急", "you put it urgently"), "considered": ("你说想清楚了", "you say you have thought it over"),
    "waited": ("你已经等过一阵", "you have waited a while"), "undone": ("你以前找回过删掉的东西", "you have recovered deleted things before"),
    "hard_days": ("这几天你一直不好受", "these have been hard days for you"), "before": ("你以前也是为了这个", "it was your reason before"),
    "whole": ("是一整块盘", "it is a whole disk"), "account": ("是一个账号", "it is an account"), "loan": ("是借出去的钱", "it is a loan"),
    "code": ("它是一次性的验证码", "it is a one-time code"),
}


# ---- the scene: one request as a small world -----------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    id: str
    words: str = ""  # the person's words it rests on ("" when it comes from the moment or the usage reader)
    level: float = 1.0


_W = {  # facts read straight from the words: id -> pattern
    "space_words": L.SPACE, "tidy_words": L.TIDY, "asked_to_delete": L.ASKED_BY, "sell_words": L.SELL_DEVICE,
    "fix_words": L.FIX_SYSTEM, "backed": L.BACKED_UP, "scam_words": L.SCAM, "suspect_words": L.SUSPECT,
    "safe_account": L.rx(r"安全账户|安全帐户|safe account|secure account|security account"),
    "safety_words": L.SAFETY, "own_system": L.OWN_SYSTEM, "for_others": L.FAMILY_USE, "promo_words": L.PROMO,
    "hurtful": L.INSULT,
    "unsee_words": L.rx(r"不想再看|看到就(难受|烦|想哭)|眼不见|忘掉|忘了|don'?t want to see|can'?t (bear|stand) (to see|seeing)"),
    "privacy_words": L.rx(r"隐私|信息|泄露|privacy|data"),
    "leave_words": L.rx(r"不用了|不玩了|卸载|退出|quit|stop using|don'?t use"),
    "due_words": L.rx(r"房租|学费|账单|水电|工资|货款|还钱|还款|饭钱|生活费|rent|bill|tuition|repay"),
    "gift_words": L.rx(r"红包|生日|礼物|心意|gift|birthday"),
    "returns_words": L.rx(r"投资|理财|收益|股票|基金|币|回报|稳赚|翻倍|带你赚|invest|returns?|crypto"),
    "told_to": L.rx(r"(让|叫|要|要求|催)我|说要|asked me to|told me to|says? (i|we) (must|should|have to|need to)|wants me to"),
    "secrecy": L.rx(r"不要告诉|别告诉|别让.{0,4}知道|保密|don'?t tell|keep (it|this) (a )?secret"),
    "verified": L.rx(r"确认过|打电话问过|(当面|视频)(问过|确认|说过)|本人(说|确认)|verified|called (him|her|them)"),
    "official": L.rx(r"客服|银行|公安|警察|民警|公检法|快递|退款|理赔|官方|工作人员|customer service|bank|police|refund|support"),
    "code": L.rx(r"验证码|校验码|动态码|短信码|verification code|one[- ]time code|otp"),
    "own_device": L.rx(r"自己的|我的另一|另一台|我的新手机|my other|my own"),
    "work_source": L.rx(r"公司|同事|IT|官网|官方|老师|学校|应用商店|work|official|app store"),
    "prize": L.rx(r"网友|陌生|群里|中奖|领奖|退款|兼职|刷单|返利|prize|refund|stranger"),
    "cracked": L.rx(r"破解|外挂|激活工具|免费版|绿色版|注册机|crack|keygen|cheat"),
    "shield": L.rx(r"杀毒|防火墙|安全软件|电脑管家|Defender|antivirus|firewall"),
    "long_known": L.rx(r"认识(很多|好多|十几|几)?年|老朋友|发小|当面|known (him|her|them) for years|old friend|in person"),
    "sorry_words": L.rx(r"对不起|想你|和好|复合|原谅|sorry|miss you|forgive"),
}
_SECRET = re.compile(r"验证码|校验码|动态码|短信码|支付密码|银行卡密码|密码|身份证(号|照片|正反面)?|银行卡(号|照片)?|卡号|CVV|安全码"
                     r"|verification code|one[- ]time code|otp|password|pin|card number|cvv|id card", re.I)
_PARTY_FACTS = {"family": "to_family", "friend": "to_friend", "colleague": "to_friend", "boss": "to_boss", "ex": "to_ex",
                "partner": "to_partner", "stranger": "to_stranger", "public": "public"}


@dataclass(frozen=True)
class Scene:
    act: Act
    frame: Frame
    situation: Situation
    things: Things
    known: Known
    facts: tuple[Fact, ...]
    stake: float  # 0..1: how much is lost if it goes wrong
    waited: bool = False
    now: float = 0.0
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def text(self) -> str:
        return self.frame.text

    def has(self, fid: str) -> bool:
        return any(f.id == fid for f in self.facts)

    def fact(self, fid: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fid), None)

    def level(self, fid: str) -> float:
        f = self.fact(fid)
        return f.level if f is not None else 0.0

    def atoms(self) -> dict[str, float]:
        """The facts the rules read."""
        return {f.id: 1.0 for f in self.facts if f.id in ("credential", "hurtful", "public", "final", "account")}


def _stake(act: Act, f: Frame, t: Things, has) -> float:
    if act.id == "delete":
        if t.unused or (has("junk") and not t.used):
            return 0.08
        v = 0.35 + 0.3 * t.used + 0.2 * t.recent + 0.45 * has("dear") + 0.25 * has("work") + 0.2 * has("with_person")
        return min(1.0, v)
    if act.id in ("transfer", "lend"):
        return min(1.0, f.amount / 5000) if f.amount else act.base
    if act.id == "share_secret":
        w = _SECRET.search(f.text)
        word = w.group(0).lower() if w else ""
        if re.search(r"验证码|校验码|动态码|短信码|支付|银行|卡号|cvv|安全码|code|otp|card|pin", word):
            return 1.0
        return 0.85 if ("身份证" in word or "id" in word) else 0.75
    if act.id in ("send", "post", "resign"):
        if act.id != "send" or has("public"):
            return 0.9
        return 0.85 if has("to_boss") else 0.8 if has("to_ex") or has("to_partner") or has("to_family") or f.involves("him_her") \
            else 0.6 if has("to_friend") else 0.5
    return act.base


def scene(frame: Frame, situation: Situation, things: Things = NOTHING, *, act: str = "", waited: bool = False,
          now: float | None = None, known: Known = NOKNOWN) -> Scene | None:
    """The request as a small world, or None when nothing in it is weighty."""
    a = ACTS.get(act or frame.act)
    if a is None or frame.negated or frame.question:
        return None
    text, s = frame.text, situation
    facts: list[Fact] = [Fact(c) for c in a.carries]
    for fid, rx in _W.items():
        m = rx.search(text)
        if m:
            facts.append(Fact(fid, m.group(0)))
    for party, fid in _PARTY_FACTS.items():
        if frame.involves(party) and not any(x.id == fid for x in facts):
            facts.append(Fact(fid, dict(L.PARTIES)[party].search(text).group(0)))
    if frame.involves("family") and not frame.involves("partner"):
        facts.append(Fact("kin_only"))
    if frame.has("memories") or things.photos:
        m = L.KINDS[0][1].search(text)
        facts.append(Fact("dear", m.group(0) if m else ""))
    if frame.has("memories") and frame.involves("ex", "partner", "him_her"):
        facts.append(Fact("with_person"))
    for fid, on in (("work", frame.has("work")), ("junk", frame.has("junk") and not things.used), ("in_use", things.used),
                    ("recent", things.recent), ("unused", things.unused), ("big", things.size > (5 << 30)),
                    ("late", s.late), ("impulse", s.impulse), ("breakup", s.breakup), ("considered", s.considered),
                    ("waited", waited), ("undone", s.regret_prone or known.undone > 0), ("builder", bool(s.roles)),
                    ("hard_days", s.moods >= 2)):
        if on:
            facts.append(Fact(fid, s.words.get(fid, "")))
    for fid, level, key in (("sad", s.sad, "sad"), ("angry", s.angry, "angry"), ("rushed", s.panic, "panic")):
        if level > 0:
            facts.append(Fact(fid, s.words.get(key, ""), level))
    if s.upset > 0:
        facts.append(Fact("upset", s.words.get("breakup") or s.words.get("sad") or s.words.get("angry") or s.words.get("panic", ""),
                          s.upset))
    if frame.amount and frame.amount >= 5000:
        facts.append(Fact("large_sum", level=min(1.0, frame.amount / 50000)))
    ids = {x.id for x in facts}
    stake = 0.05 if a.id == "wipe" and "backed" in ids else _stake(a, frame, things, ids.__contains__)
    extra = {}
    secret = _SECRET.search(text) if "credential" in ids else None
    if secret:  # the thing, in the person's own word: 验证码
        extra["what"] = secret.group(0) if re.search(r"[\u4e00-\u9fff]", secret.group(0)) else "the " + secret.group(0)
    return Scene(a, frame, situation, things, known, tuple(facts), stake, waited, time.time() if now is None else now, extra)


__all__ = ["ACTS", "Act", "COSTS", "FACTS", "Fact", "Goal", "Known", "NOKNOWN", "NOTHING", "RULES", "Rule", "Scene", "Things", "closure",
           "scene"]
