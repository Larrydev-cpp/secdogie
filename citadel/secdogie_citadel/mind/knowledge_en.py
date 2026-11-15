"""The English names of what the mind knows (``knowledge.py`` holds the
knowledge itself, with its Chinese names): each motive, each option with
the words that choose it and what it keeps, the evidence a belief rests on,
and what a person can say to show they have thought it over.

A phrase in ``UNLOCK`` is only ever offered after the mind has checked, by
thinking the request through again with the phrase added, that it really
turns the answer into going ahead.
"""
from __future__ import annotations

MOTIVE = {
    ("delete", "free_space"): "to free up space", ("delete", "tidy"): "to tidy things up",
    ("delete", "move_on"): "not to see them any more, not to be stung by them again",
    ("delete", "vent"): "to wipe it out while you are angry",
    ("delete", "privacy"): "because someone asked you to, or so no one else sees it",
    ("delete", "plain"): "simply not wanting it",
    ("wipe", "sell"): "to sell it or give it away", ("wipe", "fix"): "to get it working well again",
    ("wipe", "fresh_start"): "to clear everything and start over", ("wipe", "nothing_to_lose"): "there being nothing on it to keep",
    ("wipe", "plain"): "simply wanting it formatted",
    ("transfer", "family"): "to help family", ("transfer", "obligation"): "to pay what is owed",
    ("transfer", "gift"): "to send a gift", ("transfer", "invest"): "to invest",
    ("transfer", "scam"): "someone telling you to, who is very likely a scammer", ("transfer", "plain"): "simply sending this money",
    ("send", "inform"): "to say how things are", ("send", "vent"): "to let the anger out", ("send", "reconcile"): "to make up",
    ("send", "end"): "to end it", ("send", "plain"): "simply sending it",
    ("bulk", "testing"): "to test in your own system", ("bulk", "family"): "for family or colleagues to use",
    ("bulk", "promo"): "to collect an offer or a reward several times", ("bulk", "plain"): "simply wanting that many accounts",
    ("track", "suspicion"): "being uneasy and wanting to know what they are doing", ("track", "stalk_ex"): "not being able to let an ex go",
    ("track", "safety"): "worrying about their safety", ("track", "plain"): "simply wanting to look",
    ("close_account", "privacy"): "not to leave traces behind", ("close_account", "leave"): "not wanting to use it any more",
    ("close_account", "vent"): "to walk away while you are upset", ("close_account", "plain"): "simply closing it",
    ("share_secret", "scam"): "someone trying to trick you out of a code or a password", ("share_secret", "family"): "to help family with something",
    ("share_secret", "own"): "to use it on another device of your own", ("share_secret", "plain"): "simply giving it to them",
    ("run_unknown", "trusted"): "needing it for work, from a source you trust",
    ("run_unknown", "stranger"): "a stranger sent it and no one knows what it does",
    ("run_unknown", "cheat"): "to use a cracked copy or a cheat", ("run_unknown", "defense_off"): "it won't install, so the protection has to go",
    ("run_unknown", "plain"): "simply running it",
    ("lend", "friend"): "to help a friend or family through a hard patch", ("lend", "online"): "someone you met online asking you for money",
    ("lend", "plain"): "simply lending it",
}

# (name, what to say to choose it, what it keeps or avoids)
OPTION = {
    ("delete", "do"): ("delete it", "", "it is what you said"),
    ("delete", "seal"): ("archive it out of sight", "Archive {thing}",
                         "it goes into a hidden folder for later: out of sight, still there, and you can change your mind any time"),
    ("delete", "encrypt"): ("compress and encrypt it", "Encrypt {thing}", "a passphrase only you know: no one else can open it, and you still have it"),
    ("delete", "offload"): ("move it to an external drive or the cloud", "Move {thing} to an external drive",
                            "this computer gets the room back, and the things are kept elsewhere"),
    ("wipe", "do"): ("format it", "", "it is what you said"),
    ("wipe", "backup_first"): ("back up, then format", "Back it up first", "what you want to keep goes somewhere else first, so formatting loses nothing"),
    ("transfer", "do"): ("send it", "", "it is what you said"),
    ("transfer", "verify"): ("call them first to check", "I'll call them first to check",
                             "call the number you saved yourself, or ask in person; if it really is them, a few minutes cost nothing"),
    ("send", "do"): ("send it now", "", "it is what you said"),
    ("send", "delay"): ("draft it and send it in the morning", "Draft it and send it tomorrow morning",
                        "nothing is lost, and you read it once more after a night's sleep"),
    ("send", "soften"): ("say it without wounding", "Make it calmer, then send it", "what you mean still gets said, without words you can't take back"),
    ("bulk", "do"): ("register them", "", "it is what you said"),
    ("bulk", "own_way"): ("find a proper way", "", "for family, each person registers with their own number; for testing, test accounts in your own test environment"),
    ("track", "do"): ("look in secret", "", "you would know at once"),
    ("track", "talk"): ("ask them directly", "Help me work out how to ask", "asking tells you more than looking in secret, and keeps the trust"),
    ("track", "consent"): ("agree to share locations", "Help me set up location sharing", "they know, they agree, and you can look whenever you worry"),
    ("track", "call_help"): ("find people first, and call the police if you truly can't reach them", "",
                             "call the people around them first; if they are really missing, call the police"),
    ("close_account", "do"): ("close it", "", "it is what you said"),
    ("close_account", "export_first"): ("export, then close", "Export the records from {thing} first", "the records are kept before the account goes"),
    ("close_account", "pause"): ("step away for a while", "Sign out and leave it for a while", "the quiet is the same, and you can come back"),
    ("share_secret", "do"): ("send it", "", "it is what you said"),
    ("share_secret", "keep"): ("not send it yet", "", "a code and a password are for you alone; a real bank or support line never asks for them"),
    ("share_secret", "in_person"): ("say it in person or on a call", "I'll tell them in person",
                                    "in person, or on a call to the number you saved: nothing left in a chat, nothing an impostor can catch"),
    ("run_unknown", "do"): ("run it", "", "it is what you said"),
    ("run_unknown", "check_first"): ("find out where it comes from first", "Find out where it comes from first",
                                     "get the real one from the official site or the app store; I can look at what an unknown one would do without running it"),
    ("lend", "do"): ("lend it", "", "it is what you said"),
    ("lend", "verify"): ("see them on video or in person first", "I'll video-call them first", "one look on video, or one question in person; if it is them, a little later costs nothing"),
    ("lend", "smaller"): ("lend only what you could lose without minding", "Lend a smaller amount first", "decide beforehand: if it never comes back, it should not hurt"),
}
OPTION.update({(a, k): OPTION[("send", k)] for a in ("post", "resign", "message") for k in ("do", "delay", "soften")})
MOTIVE.update({(a, k): MOTIVE[("send", k)] for a in ("post", "resign", "message")
               for k in ("inform", "vent", "reconcile", "end", "plain")})

UNLOCK = {
    "我想清楚了": "I've thought it over",
    "我想清楚了，现在就发": "I've thought it over, send it now",
    "已经备份过了": "I've already backed it up",
    "我打电话确认过了，是本人": "I called them and verified it",
    "是房租，我确认过收款人了": "It's the rent, and I verified the payee",
    "在公司的 staging 测试环境注册十个测试账号": "Register ten test accounts on our staging environment",
    "是公司 IT 发的，官网下载的": "It's from IT at work, from the official site",
    "是我认识多年的朋友，当面借的": "It's an old friend, lent in person",
}

EVIDENCE = {
    "你说到空间": "you mention space", "它占了不少地方": "it takes up a lot of room", "你说到没用的、重复的": "you call them useless or duplicates",
    "是些用不着的东西": "they are things no one needs", "你说到分开": "you speak of a breakup", "你很难过": "you are sad",
    "里面有另一个人": "someone else is in them", "你说不想再看到": "you say you don't want to see them", "你在生气": "you are angry",
    "你说得很急": "you put it urgently", "夜很深了": "it is very late", "有人要你删": "someone asked you to delete it",
    "你说要转手": "you say it is changing hands", "你说它不好用了": "you say it isn't working well", "你心里不好受": "you are upset",
    "你说备份过了，或者是一块新盘": "you say it is backed up, or new", "是给家里人": "it is for family", "是该付的钱": "it is money owed",
    "是一份心意": "it is a gift", "说到收益": "it mentions returns", "对方用的是骗子常用的说法": "they use the words scammers use",
    "对方是你不认识、没见过的人，或者是自称的身份": "you can't verify who they are", "你被催得很急": "you are being rushed",
    "对方让你别告诉别人": "they tell you to keep it secret", "你说确认过了": "you say you verified it",
    "你以前也是为了这个": "it was your reason before", "是你自己的测试环境": "it is your own test environment",
    "你做测试或者开发": "you test or build software", "是给别人用": "it is for others to use", "说到家里人": "family is mentioned",
    "说到优惠、抽奖": "offers or prize draws are mentioned", "你在怀疑": "you suspect something", "对方是你的另一半": "they are your partner",
    "对方是前任": "they are your ex", "你在担心对方的安全": "you fear for their safety", "是家里人": "they are family",
    "对方自称客服、银行或者警察": "they claim to be support, a bank or the police", "验证码从来只该你自己用": "a code is only ever for you",
    "是给你自己用": "it is for your own use", "是工作上、或者从官方来的": "it is from work or an official source",
    "来路像是骗局": "where it comes from looks like a scam", "是破解版或者外挂": "it is a crack or a cheat",
    "你要关掉的是电脑的防护": "it is the computer's protection you would turn off",
    "你们认识很久了，或者是当面借的": "you have known them a long time, or it is in person", "说到带你赚钱": "it promises to make you money",
    "对方催得很急": "they are rushing you", "是一封辞职信": "it is a resignation", "你在意隐私": "you care about privacy",
    "你不想再用": "you don't want to use it", "你想和好": "you want to make up",
}

__all__ = ["EVIDENCE", "MOTIVE", "OPTION", "UNLOCK"]
