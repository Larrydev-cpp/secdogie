"""The Socratic questions: what the cortex asks itself about everything it is told.

Each question is a population of answer neurons, one neuron per answer. Whichever answer neuron charges
highest is the cortex's answer, and how far it stands above the others is how sure it is.

The questions are the same for every sentence in every language. They name no request and no word: they
are the cortex's method. Their wording is used in two places only: the fixed format the external model's
judgments are written in, and the labels the Gate page shows next to each answer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    id: str
    en: str  # the question, as the external model and the Gate page read it
    zh: str
    answers: tuple[tuple[str, str, str], ...]  # (answer id, what it means in English, in Chinese)
    default: str  # the answer when a lesson says nothing about this question
    applies: tuple[str, ...]  # the kinds of sentence it is asked for ("" = always)

    @property
    def choices(self) -> tuple[str, ...]:
        return tuple(a for a, _, _ in self.answers)

    def index(self, answer: str) -> int:
        return self.choices.index(answer)

    def meaning(self, answer: str, lang: str = "en") -> str:
        for a, en, zh in self.answers:
            if a == answer:
                return zh if lang == "zh" else en
        return answer


ALWAYS = ("",)
TASK = ("task",)
WEIGHED = ("task", "ask")

QUESTIONS: tuple[Question, ...] = (
    Question("kind", "What is the person doing by saying this?", "对方说这句话是在做什么？", (
        ("chat", "greeting, thanking, saying goodbye or small talk", "打招呼、道谢、道别或闲聊"),
        ("ask", "asking a question or for advice: words are enough", "提问或求建议：回答就够了"),
        ("feel", "telling how they feel or what happened to them", "在说自己的感受或经历"),
        ("task", "asking for something to be done", "要我去做一件事"),
        ("unclear", "cannot tell what they mean", "看不出是什么意思"),
    ), "unclear", ALWAYS),
    Question("self", "Could they be in danger from themselves?", "对方会不会有伤害自己的危险？", (
        ("no", "nothing suggests it", "看不出"),
        ("yes", "they may be thinking of hurting themselves or ending their life", "可能想伤害自己或轻生"),
    ), "no", ALWAYS),
    Question("others", "If it were done or answered exactly as asked, whom would it fall on?",
             "如果照原话去做或回答，会落到谁身上？", (
                 ("self", "only the person and their own things", "只有对方自己和自己的东西"),
                 ("fine", "others, in an ordinary way they would accept", "牵涉别人，但是对方能接受的寻常事"),
                 ("words", "someone gets harsh words in the person's name", "有人会收到以对方名义说的重话"),
                 ("harm", "someone who has not agreed is hurt, robbed, intruded on, deceived or endangered",
                  "没同意的人会被伤害、被拿走东西、被侵入、被骗或陷入危险"),
                 ("unsure", "it depends on something unknown, such as whether they agreed", "取决于不知道的事，比如对方同不同意"),
             ), "self", WEIGHED),
    Question("seat", "Sitting where the others it touches sit: would they object?", "坐到被牵涉的人的位置上：他们会反对吗？", (
        ("nobody", "it touches nobody else", "不牵涉别人"),
        ("no", "they would not mind", "他们不会介意"),
        ("yes", "they would object", "他们会反对"),
        ("unsure", "cannot tell", "说不准"),
    ), "nobody", WEIGHED),
    Question("clear", "Is it clear what to do, and to which thing?", "要做什么、对哪个东西做，清楚吗？", (
        ("yes", "the act and the thing are named or obvious", "动作和对象都说了或很明显"),
        ("no", "the act or the thing is missing", "缺了动作或对象"),
    ), "yes", TASK),
    Question("effect", "What would doing it change?", "做了会改变什么？", (
        ("nothing", "it only looks, finds, reads, shows, opens, plays, translates or reminds", "只是看、找、读、显示、打开、播放、翻译或提醒"),
        ("makes", "it creates, edits, moves, copies or tidies something of their own", "新建、修改、移动、复制或整理自己的东西"),
        ("removes", "it deletes files, photos or messages", "删除文件、照片或聊天记录"),
        ("erases", "it wipes a whole disk or device: formatting, factory reset, reinstalling", "清空整块盘或整台设备：格式化、恢复出厂、重装"),
        ("closes", "it closes or deletes an account", "注销或删除一个账号"),
        ("pays", "it sends or pays money", "转账或付款"),
        ("lends", "it lends money", "借钱给别人"),
        ("tells", "it gives out a password, code, card or ID number or other private details", "交出密码、验证码、卡号、证件号或其他隐私"),
        ("says", "it sends a message or email, or posts something, in their name", "以对方的名义发消息、发邮件或发帖"),
        ("runs", "it installs or runs a program, or turns off the computer's protection", "安装或运行程序，或关掉电脑的防护"),
        ("watches", "it looks into someone's private things or whereabouts", "查看别人的私事或行踪"),
        ("registers", "it signs up for accounts, many at once", "注册账号，一次很多个"),
        ("other", "something else", "别的"),
    ), "nothing", TASK),
    Question("to", "Whom does it go to, or whom is it about?", "它是给谁的，或者是关于谁的？", (
        ("none", "nobody in particular", "没有特定的人"),
        ("own", "their own other device or account", "对方自己的另一台设备或另一个账号"),
        ("known", "someone they know in person: family, a partner, a friend, a colleague, their boss",
         "当面认识的人：家人、伴侣、朋友、同事、上司"),
        ("stranger", "someone they cannot check: a caller, 'support', 'the bank', someone met online",
         "没法核实的人：来电、「客服」、「银行」、网上认识的人"),
        ("public", "everyone: a public post", "所有人：公开发布"),
        ("unsure", "cannot tell", "说不准"),
    ), "none", TASK),
    Question("undo", "Once done, could it be put back?", "做了以后能恢复原样吗？", (
        ("easily", "yes, without loss", "能，没有损失"),
        ("hardly", "only with effort, or only partly", "费劲，或者只能恢复一部分"),
        ("never", "no", "不能"),
        ("unsure", "cannot tell", "说不准"),
    ), "easily", TASK),
    Question("pushed", "Is someone else behind it?", "背后有没有别人在推？", (
        ("nobody", "it is their own idea", "是对方自己的主意"),
        ("known", "someone they know in person", "是对方认识的人"),
        ("stranger", "someone they cannot check: a caller, 'support', 'the bank', someone met online",
         "是没法核实的人：来电、「客服」、「银行」、网上认识的人"),
        ("unsure", "cannot tell", "说不准"),
    ), "nobody", TASK),
    Question("mood", "How do they seem right now?", "对方现在状态怎么样？", (
        ("calm", "settled", "平静"),
        ("upset", "sad, angry, hurt, panicked or rushed", "难过、生气、受伤、慌张或被催"),
        ("unsure", "cannot tell", "说不准"),
    ), "calm", TASK),
    Question("assured", "Do they say the risk is already taken care of?", "对方说风险已经处理过了吗？", (
        ("none", "they say nothing of it", "没提"),
        ("thought", "they have thought it over", "说想清楚了"),
        ("checked", "they checked who the other side is, their own way", "说用自己的办法核实过对方"),
        ("copied", "there is another copy, or nothing of value in it", "说有备份，或者里面没有要紧的东西"),
    ), "none", TASK),
    Question("dear", "What is the thing to them?", "这个东西对对方来说是什么？", (
        ("ordinary", "an everyday thing", "寻常的东西"),
        ("dear", "memories: photos or messages with someone close", "回忆：和亲近的人有关的照片或聊天"),
        ("work", "their work or studies", "工作或学业"),
        ("junk", "leftovers nobody needs", "没人需要的剩余物"),
        ("unsure", "cannot tell", "说不准"),
    ), "ordinary", TASK),
    Question("stake", "How much is at stake if it goes wrong?", "出错的话损失有多大？", (
        ("small", "little", "不大"),
        ("large", "a lot: a large sum, a whole disk, an account, a job, a relationship",
         "很大：一大笔钱、整块盘、一个账号、一份工作、一段关系"),
        ("unsure", "cannot tell", "说不准"),
    ), "small", TASK),
    Question("why", "Why do they want it?", "对方为什么要这样做？", (
        ("unsaid", "they do not say", "没说"),
        ("space", "to free up room", "腾地方"),
        ("tidy", "to tidy up", "整理清爽"),
        ("forget", "not to see it any more, to move on", "不想再看到，想翻篇"),
        ("privacy", "to keep it from others", "不让别人看到"),
        ("sell", "the device is changing hands", "设备要转手"),
        ("fix", "to get something working again", "让东西重新好用"),
        ("owed", "money that is owed: rent, bills, a debt", "该付的钱：房租、账单、欠款"),
        ("help", "to help someone they know", "帮认识的人"),
        ("gift", "a gift", "一份心意"),
        ("profit", "a promise of gain: returns, a prize, an offer", "图回报：收益、奖品、优惠"),
        ("vent", "to let anger out", "撒气"),
        ("mend", "to make up, or to say something kind", "想和好，或者说句好话"),
        ("quit", "to end something: a job, a relationship", "想了断：一份工作、一段关系"),
        ("safety", "someone's safety", "担心某人的安全"),
        ("suspect", "wanting to know what someone is doing", "想知道某人在做什么"),
        ("test", "testing their own system", "测试自己的系统"),
        ("other", "some other reason", "别的原因"),
    ), "unsaid", TASK),
)

BY_ID = {q.id: q for q in QUESTIONS}
SIZES = tuple(len(q.answers) for q in QUESTIONS)
OFFSETS = tuple(sum(SIZES[:i]) for i in range(len(SIZES)))
OUTPUTS = sum(SIZES)  # answer neurons in all
KINDS = BY_ID["kind"].choices


def applies(q: Question, kind: str) -> bool:
    """Is ``q`` asked about a sentence of this kind?"""
    return "" in q.applies or kind in q.applies


def span(qid: str) -> slice:
    i = next(n for n, q in enumerate(QUESTIONS) if q.id == qid)
    return slice(OFFSETS[i], OFFSETS[i] + SIZES[i])


__all__ = ["BY_ID", "KINDS", "OFFSETS", "OUTPUTS", "QUESTIONS", "Question", "SIZES", "applies", "span"]
