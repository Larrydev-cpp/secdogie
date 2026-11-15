"""The concepts the mind reads a sentence with, in Chinese and English.

This is the one word list the gate reads from. Each entry names a concept
and the words that show it:

- **acts**: what the sentence would do, as a *primitive*. A primitive says
  what the act touches (a body, someone's freedom, property, privacy, a
  name, trust, data, a system) and how (it destroys, takes, enters, watches,
  exposes, deceives, threatens; or it opens, finds, reads, writes, tidies).
  ``principles.py`` reasons on those properties, not on phrases.
- **things**: what an act is done to, as a class (a person, a running
  program, a file, a device or a place, an account, something private,
  something held in confidence, something dangerous).
- **whose**: a possessive before a thing ("他的", "my").
- **marks**: how it is said (a wish, a polite request, "how do I", a
  question, "should I", protecting oneself, a story or a game, in secret).
- **idioms**: words that only look like an act ("累死了", "杀毒",
  "killing it"). An idiom is longer than the act inside it, so the scanner's
  longest match keeps the act from being read.

``scan`` turns a sentence into tokens by longest match. Nothing here is
learned; every entry can be read, argued with and changed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

ACT, THING, OWN, MARK, IDIOM = "act", "thing", "own", "mark", "idiom"


@dataclass(frozen=True)
class Prim:
    """An act primitive: what it touches, and how."""

    id: str
    touches: str  # body, liberty, property, privacy, name, trust, data, system, nothing
    way: str  # destroys, takes, enters, watches, exposes, deceives, threatens, insults, or an ordinary way
    wrong: bool = False  # done to what is another's, without their consent, it is a wrong
    zh: str = ""  # how the companion names it
    en: str = ""


PRIMS = {p.id: p for p in (
    Prim("harm", "body", "destroys", True, "伤害一个人", "hurting someone"),
    Prim("harm_weak", "body", "destroys", True, "伤害一个人", "hurting someone"),  # only with a living object right after
    Prim("threaten", "liberty", "threatens", True, "威胁、逼迫别人", "threatening or coercing someone"),
    Prim("take", "property", "takes", True, "拿走别人的东西", "taking what is someone else's"),
    Prim("intrude", "system", "enters", True, "闯进别人的账号或设备", "breaking into someone else's account or device"),
    Prim("surveil", "privacy", "watches", True, "暗中盯着一个人", "watching someone without their knowing"),
    Prim("expose", "privacy", "exposes", True, "把别人的私事公开出去", "exposing someone's private life"),
    Prim("defame", "name", "destroys", True, "毁掉别人的名声、骚扰别人", "ruining someone's name or harassing them"),
    Prim("insult", "name", "insults", True, "骂人", "insulting someone"),
    Prim("damage", "property", "destroys", True, "毁坏别人的东西", "wrecking what is someone else's"),
    Prim("deceive", "trust", "deceives", True, "骗人", "deceiving someone"),
    # ordinary acts: on the person's own things, or on nothing in particular, they simply go ahead
    Prim("open", "nothing", "opens", zh="打开", en="opening"),
    Prim("stop", "system", "stops", zh="关掉", en="stopping"),
    Prim("find", "nothing", "finds", zh="查找", en="finding"),
    Prim("read", "nothing", "reads", zh="查看", en="reading"),
    Prim("write", "nothing", "writes", zh="写", en="writing"),
    Prim("organise", "data", "moves", zh="整理", en="organising"),
    Prim("edit", "data", "changes", zh="修改", en="editing"),
    Prim("schedule", "nothing", "plans", zh="安排", en="scheduling"),
    Prim("set", "system", "changes", zh="设置", en="setting"),
    Prim("install", "system", "changes", zh="安装", en="installing"),
    Prim("login", "system", "enters", zh="登录", en="signing in"),
    Prim("give", "data", "sends", zh="交给别人", en="handing over"),
    Prim("make", "nothing", "makes", zh="做", en="making"),
    Prim("get", "nothing", "gets", zh="弄到", en="getting"),
    Prim("vague", "nothing", "", zh="处理", en="handling"),  # "处理一下", "deal with it": an act, but which one?
)}
ORDINARY = frozenset({"open", "stop", "find", "read", "write", "organise", "edit", "schedule", "set", "install", "login",
                      "make", "get", "give"})

_P = (r"他们|她们|(?<![其吉])他(?!们)|她(?!们)|某人|别人|人家|对方|那个人|这个人|那家伙|这家伙|那个女的|那个男的")
_ROLE = (r"老板|领导|上司|主管|经理|同事|同学|老师|邻居|室友|房东|前任|前男友|前女友|前妻|前夫|男朋友|女朋友|老公|老婆|丈夫|妻子"
         r"|对象|朋友|闺蜜|兄弟|网友|陌生人|客户|员工|下属|孩子|小孩|儿子|女儿|爸爸?|妈妈?|父母|爷爷|奶奶|哥哥?|姐姐?|弟弟?|妹妹?|医生|司机")
_ROLE_EN = (r"boss|manager|coworker|co-worker|colleague|classmate|teacher|neighbou?r|roommate|landlord|ex|ex-\w+|wife|husband"
            r"|girlfriend|boyfriend|partner|friend|client|customer|employee|kid|child|son|daughter|mom|mum|mother|dad|father"
            r"|brother|sister|doctor|driver")

# (concept id, Chinese, English). English entries are matched on word boundaries.
_ACTS = (
    ("harm", r"杀死|杀害|杀掉|杀了|弄死|整死|搞死|打死|毒死|勒死|掐死|捅死|砍死|烧死|淹死|干掉|做掉|灭口|下毒|投毒|殴打|暴打|痛打|谋杀|行凶"
             r"|强奸|强暴|性侵|猥亵|虐待|弄残|打残|绑架|拐卖|拐走|囚禁|软禁|关起来",
     r"murder\w*|assassinat\w*|strangl\w*|rape\w*|molest\w*|kidnap\w*|abduct\w*|tortur\w*|maim\w*|beat (\w+ )?up|run (\w+ )?over"),
    ("harm_weak", r"杀|打|揍|踢|踹|扇|捅|砍|咬|撞|收拾|教训|修理|伤害|弄伤|打伤",
     r"kill\w*|shoot\w*|shot|hit(s|ting)?|beat\w*|attack\w*|hurt\w*|injur\w*|poison\w*|punch\w*|kick\w*|slap\w*|stab\w*|chok\w*"
     r"|assault\w*"),
    ("threaten", r"威胁|恐吓|吓唬|勒索|敲诈|要挟|逼迫|胁迫", r"threaten\w*|intimidat\w*|blackmail\w*|extort\w*|coerc\w*"),
    ("take", r"偷走|偷(?!偷|懒|笑|闲|看|拍|听|窥)|盗取|盗走|盗刷|盗用|抢劫|抢走|抢夺|窃取|顺走|私吞|侵吞|挪用|骗走|霸占",
     r"steal\w*|stole|stolen|rob\w*|loot\w*|shoplift\w*|embezzl\w*|pickpocket\w*"),
    ("intrude", r"黑进|黑掉|黑入|入侵|攻破|破解|撬开|盗号|撞库|拖库|爆破|绕过|提权|越权",
     r"hack\w* into|hack\w*|break\w* into|broke into|crack\w*|brute.?forc\w*|bypass\w*|phish\w*"),
    ("surveil", r"人肉|开盒|跟踪|尾随|盯梢|监视|监听|窃听|偷拍|偷看|偷窥|偷听|查(出|到|一下|查)?(?=.{0,10}(住址|地址|住哪|家在哪|手机号|行踪|开房))",
     r"stalk\w*|spy\w* on|dox+\w*|eavesdrop\w*|wiretap\w*|surveil\w*|follow (him|her|them) home|find out where \w+ lives?"),
    ("expose", r"曝光|泄露|泄漏|散布|外泄|爆料|挂人|发到网上|传到网上|挂到网上|放到网上|公之于众|发到群里|群发",
     r"leak\w*|expos\w*|out (him|her|them)|post\w* (\w+ ){0,3}online|put\w* (\w+ ){0,3}online|publish\w*"),
    ("defame", r"造谣|诽谤|污蔑|抹黑|网暴|人身攻击|骚扰|辱骂|诅咒|轰炸",
     r"defam\w*|slander\w*|libel\w*|harass\w*|cyberbull\w*|smear\w*|bully\w*|spread\w* (\w+ )?(rumou?rs|lies)"),
    ("insult", r"骂|喷|怼|嘲讽|羞辱|嘲笑", r"insult\w*|curse \w+ out|yell\w* at|mock\w*|humiliat\w*"),
    ("damage", r"搞坏|弄坏|砸了|砸烂|砸掉|砸|烧了|烧掉|放火|纵火|毁掉|摧毁|破坏|搞垮|搞瘫|瘫痪|攻击|炸掉|炸了|植入|划花|扎破",
     r"destroy\w*|sabotag\w*|vandali[sz]\w*|smash\w*|burn\w* down|set fire to|ddos\w*|take down|wreck\w*|infect\w*|trash\w*"),
    ("deceive", r"骗(?!局|子)|诈骗|忽悠|坑(?=他|她|人|别人)|冒充|假冒|伪造|造假|代考|替考",
     r"scam\w*|defraud\w*|trick \w+ into|forg\w*|impersonat\w*|con (him|her|them)"),

    ("open", r"打开|开启|启动|点开|进入|运行|执行", r"open\w*|launch\w*|start\w*|go to|visit|bring up|run|execute"),
    ("stop", r"关闭|关掉|关上|关机|退出|结束|停止|暂停|终止|中止|重启|锁屏|休眠|睡眠",
     r"close|quit|exit|stop|pause|end|terminate|shut ?down|restart|reboot|lock|sleep"),
    ("find", r"搜索|搜一下|搜|查找|查一下|查询|查查|查|找一下|找出|找到|找找|找", r"search\w*|look up|look for|find|google|locate"),
    ("read", r"看看|看一下|看下|看一眼|查看|浏览|读一下|读|列出|列一下|统计|数一下|比较|对比|分析|检查|核对|预览|告诉我",
     r"show|list|read|check|view|count|compare|analy[sz]e|review|inspect|tell me|look at|preview"),
    ("write", r"写|起草|草拟|拟一|撰写|编写|生成|制作|画|设计|新建|创建|记一下|记下来|记录|翻译|总结|概括|摘要|润色|改写|续写|回一封|做一?(个|份|张)",
     r"write|draft|compose|create|generate|make|draw|design|translate|summari[sz]e|rewrite|polish|note down|take notes|outline"),
    ("organise", r"整理|归档|分类|排序|归类|移动|移到|挪到|挪|复制|拷贝|粘贴|重命名|改名|备份|同步|压缩|解压|打包|合并|拆分|转换|转成|转为"
                 r"|导出|导入|保存|另存|上传|下载|清理|去重|封存|加密",
     r"organi[sz]e|tidy|sort|archive|file|move|copy|paste|rename|back ?up|sync|compress|zip|unzip|extract|merge|split|convert"
     r"|export|import|save|upload|download|clean up|dedupe|encrypt"),
    ("edit", r"修改|改成|改为|改一下|编辑|替换|调整|更新|升级|修复|修一下|调试|优化|重构"
             r"|格式化(一下)?(这段|这个|这份|我的)?(代码|文档|文本|表格|json|单元格|日期|字符串|输出)",
     r"edit|modify|change|replace|update|upgrade|fix|debug|adjust|refactor|optimi[sz]e"
     r"|format (the |this |my )?(code|document|text|table|cell|date|string|json|output|file)s?"),
    ("schedule", r"提醒|日程|安排|预约|约(个|一下)?会|闹钟|倒计时|订会议室",
     r"remind\w*|schedule|calendar|set an? (alarm|timer|reminder)|book a meeting"),
    ("set", r"播放|放一?(首|个|点)|调高|调低|调大|调小|调亮|调暗|设置|设为|切换|静音|打印|扫描|截个?图|录屏|计算|算一下|算算|求和|杀毒|查杀|查毒|扫毒",
     r"play|mute|unmute|turn (up|down|on|off)|set|switch|toggle|enable|print|scan|calculate|compute|sum up|run a virus scan"),
    ("install", r"安装|装上|卸载", r"install|uninstall"),
    ("login", r"登录|登陆|登出|登上|注册", r"log ?in(to)?|sign ?in(to)?|log ?out|sign ?out|sign ?up|register"),
    ("give", r"发给|发送给|传给|交给|卖给|提供给|透露给|转给|拷给", r"send|give|hand|sell|pass|share|forward|email"),
    ("make", r"制造|配制|组装|合成|造(?![谣假成句型访])|做(?=(一|个|出|些|点)*(炸弹|炸药|爆炸物|毒药|毒品|武器|枪|木马|病毒|勒索))",
     r"build|manufactur\w*|assembl\w*|synthesi[sz]e|cook"),
    ("get", r"买|弄到|搞到|弄一?(把|个|点|些)|搞一?(把|个|点|些)|获取|拿到|预订|预定|订购|订|下单|购买|续费|充值",
     r"buy|get|obtain|acquire|order|purchase|book|reserve|renew|subscribe"),
    ("vague", r"处理|弄一下|搞一下|解决|搞定", r"handle|deal with|take care of|sort out"),
)

_THINGS = (
    ("self", r"我自己|自己|(?<![咱俺])我(?!们)", r"myself|me"),
    ("person", rf"{_P}|{_ROLE}|(?<=[打杀伤骂害骗坑整揍咬撞踢])人|[小老阿][一-鿿](?=[，。,.!！\s]|$)|你",
     rf"him|her|them|someone|somebody|people|person|(a|the|that|this) (man|woman|guy|girl|kid|child|stranger|person)|{_ROLE_EN}|you"),
    ("living", r"狗|猫|宠物|小动物|动物", r"dogs?|cats?|pets?|animals?"),
    ("process", r"进程|程序|应用|软件|窗口|标签页|任务|服务|线程|会话|容器|端口|脚本|弹窗|广告|红点|通知|报错|错误|问题|bug",
     r"process(es)?|programs?|apps?|applications?|windows?|tabs?|tasks?|services?|jobs?|threads?|sessions?|containers?|daemons?"
     r"|scripts?|popups?|ads?|notifications?|errors?|warnings?|issues?|bugs?"),
    ("file", r"文件夹|文件|文档|照片|图片|相册|视频|表格|报告|周报|日报|邮件|代码|项目|桌面|下载|目录|笔记|简历|合同|发票|PPT|PDF|备份|回收站",
     r"folders?|files?|documents?|docs?|photos?|pictures?|images?|videos?|spreadsheets?|reports?|emails|mail|code|projects?"
     r"|desktop|downloads?|director(y|ies)|notes?|resume|cv|contracts?|invoices?|slides?|backups?|trash|recycle bin"),
    ("device", r"[A-Za-z]\s*盘|硬盘|磁盘|U盘|电脑|手机|笔记本|平板|服务器|系统|网络|数据库|路由器|摄像头|wifi|设备",
     r"drive|disk|usb|laptop|computer|pc|phone|tablet|server|system|network|database|router|camera|wi-?fi|device"),
    ("place", r"车|房子|房间|家|门|窗|锁|店|办公室|网站", r"car|house|home|door|lock|shop|store|office|website|site"),
    ("account", r"账号|帐号|账户|帐户|微信|QQ|邮箱|支付宝|网银|银行卡|钱包|社交账号",
     r"accounts?|inbox|mailbox|e-?mail( account)?|wechat|bank account|wallet|profile|instagram|facebook|whatsapp|icloud"),
    ("money", r"钱|存款|积蓄|工资卡|现金|财物", r"money|cash|savings"),
    ("private", r"住址|地址|住哪|家在哪|手机号|电话号码|电话|身份证号?|行踪|位置|定位|隐私|私事|裸照|私照|私密照|聊天记录|通话记录|开房记录|病历"
                r"|短信|消息|密码",
     r"address|phone number|location|whereabouts|private (photos?|info|life|messages?)|nudes?|medical records?|messages?|texts?"
     r"|chat (logs?|history)|call (logs?|history)|passwords?|ssn|social security number"),
    ("confidential", r"客户资料|客户名单|客户信息|客户数据|商业机密|机密|内部文件|内部资料|内部数据|保密文件|保密资料|源代码|核心代码|财务数据|标书|报价单",
     r"customer (data|lists?|records?)|client (data|lists?|records?)|trade secrets?|confidential \w+|internal (documents?|data|files?)"
     r"|source code|proprietary \w+"),
    ("outsider", r"竞争对手|对手公司|对家|友商|竞品公司|外人", r"competitors?|rivals?|a rival \w+|outsiders?"),
    ("danger", r"炸弹|炸药|爆炸物|毒药|毒品|武器|枪支|手枪|步枪|枪|子弹|管制刀具|迷药|迷魂药|勒索病毒|勒索软件|木马|恶意软件|恶意代码|恶意程序"
               r"|僵尸网络|钓鱼网站|钓鱼页面|钓鱼邮件|间谍软件|病毒",
     r"bombs?|explosives?|poison|narcotics|illegal drugs|weapons?|guns?|firearms?|rifles?|ammo|ammunition|ransomware|malware"
     r"|trojans?|keyloggers?|botnets?|spyware|phishing (page|site|email|kit)s?|virus(es)?"),
    ("foe", r"Boss|怪物|怪|敌人|敌军|丧尸|反派", r"boss(es)?|monsters?|enem(y|ies)|npcs?|zombies?|villains?"),
)

_OWN = (
    ("org", r"(公司|单位|学校|部门|团队|别的公司)的", r"(the |my |our )?(company|office|school|team|employer|firm)'s"),
    ("other", rf"({_P}|{_ROLE}|[小老阿][\u4e00-\u9fff]|[\u4e00-\u9fff]{{1,2}}(总|哥|姐|叔|姨|老师|经理))(家)?的",
     rf"his|their|someone(?: else)?'s|somebody(?: else)?'s|other people's|(my |the |our )?({_ROLE_EN})'s|(?-i:[A-Z][a-z]+)'s|her(?= \w)"),
    ("self", r"我自己的|我的|自己的|我们的|咱们?的|我家的?", r"my own|my|our|mine"),
)

_MARKS = (
    ("wish", r"真想|好想|很想|想要|恨不得|巴不得|真希望|想",
     r"i want to|i wanna|i wish|i could|i feel like|i'd love to|i'm going to|i'm gonna|i swear i'll"),
    ("polite", r"帮我|帮忙|帮(他|她)|请你|请|麻烦你?|给我|替我|能不能|可不可以|能否|可以.{0,12}吗|能.{0,12}吗",
     r"please|can you|could you|would you|will you|help me|i need you to|i want you to|i'd like you to|go ahead and"),
    ("howto", r"怎么|如何|怎样|咋|教我|教教我|方法|步骤|教程|攻略", r"how to|how do i|how can i|how would i|ways? to|teach me|tutorial|steps to"),
    ("should", r"该不该|应不应该|要不要|值不值得|好不好|合不合适|行不行|我应该.{0,12}吗|我该.{0,12}吗|可以.{0,6}吗",
     r"should i|shall i|is it (a good idea|ok|okay|wise|safe|smart) to|would it be (ok|okay|wise)|do you think i should|ought i"),
    ("guard", r"防范|防止|防身|防御|防|保护|自卫|报警|举报|求助|怎么办|识别|检测|查杀|清除|杀毒|扫描|被人?(打|威胁|跟踪|骗|偷|盗|黑|骚扰|勒索)"
              r"|遭到|受到|是不是|是否|算不算|反诈|警惕|小心|避免|中了?(病毒|毒|木马)|有病毒|感染了?病毒|新闻|报道",
     r"protect\w*|defend\w*|self-defen[cs]e|report (it|him|her|them|this|that)|report to|call the police|what should i do|being (stalked|threatened|harassed|hacked|scammed|followed)"
     r"|(got|was|been) (hacked|scammed|robbed|attacked|threatened)|detect\w*|remove|scan|avoid|prevent|is (this|it|that) a|infected|(has|got|have) a virus|the news|news about"),
    ("story", r"游戏里|游戏中|游戏|小说|电影|电视剧|剧本|故事|角色|副本|关卡|剧情|漫画",
     r"in (the|a|my|this) (game|novel|story|movie|film|script|show)|game|novel|story|movie|screenplay|fiction|character"),
    ("secret", r"偷偷|悄悄|背着|暗中|瞒着|不让.{0,6}知道|别让.{0,6}知道|神不知鬼不觉",
     r"secretly|without (him|her|them|anyone) knowing|behind (his|her|their) back|covertly|quietly"),
    ("other_does", r"(他|她|他们|有人|对方|那个人|老板|同事|前任|男朋友|女朋友|老公|老婆)(一直|总是|老是|又|还|说|想|要|扬言|天天)*(在|要|想|会)?$",
     r"(he|she|they|someone|somebody|my \w+) (keeps?|is|are|was|were|wants? to|said (he|she|they) (would|will)|will|would)$"),
)

# Words that only look like an act. Longer than the act inside them, so they win.
_IDIOMS = (
    r"笑死|累死|气死|烦死|热死|冷死|饿死|渴死|困死|忙死|吓死|急死|羡慕死|爱死|想死你|想死我了|难受死|无聊死|尴尬死|打死也不|打死不"
    r"|杀价|杀青|杀毒|查杀|秒杀|杀手锏|打卡|打工|打车|打字|打印|打包|打扫|打折|打算|打扰|打听|打招呼|打电话|打游戏|打球|打分|打断|打磨|打造"
    r"|打底|打赏|打理|打瞌睡|打盹|打喷嚏|打针|打疫苗|打败|打赢|打量|打动|打破|打通|打个比方|打草稿|打比方"
    r"|死机|死循环|死锁|死链|卡死|闪退|抢票|抢购|抢红包|抢单|抢先|抢救|抢答|抢修|偷懒|偷笑|偷闲|防盗|盗版"
    r"|玩具枪|水枪|喷枪|热熔胶枪|胶枪|枪手|扫码枪|病毒式|炸鸡|炸薯条|油炸|爆炸性新闻|问题不大|没问题|有问题吗"
    r"|格式化(一下)?(这段|这个|这份|我的)?(代码|文档|文本|表格|json|单元格|日期|字符串|输出)|修理(电脑|手机|车|自行车|水管)|收拾(房间|屋子|东西|行李|桌子|桌面)"
    r"|解决问题|攻击性|破解版|破解补丁|破解软件|砍价|砍一刀|咬文嚼字|撞库测试|骗局|骗子|踢足球|踢球|画个|骂醒",
    r"dying to|to die for|killing it|killed it|killer (feature|app|deal|workout)|kill(ing)? time|kill switch"
    r"|beat the (deadline|clock|traffic|rush|heat)|beats? me|hit (the road|me up|send|enter|save|play|pause|the (button|gym|hay|sack|books))"
    r"|shoot (me |him |her |them )?(an? |the )?(email|message|text|note|photo|video|picture|pic)s?|shot of|screen ?shot|deadlines?"
    r"|dead (link|lock|battery|pixel|code|end)s?|deadlock|blow(ing|s)? (up )?my (phone|mind|inbox)|bomb(ed)? (the|my) (test|exam|interview)"
    r"|photo ?bomb\w*|bath bombs?|steal(ing)? the show|life ?hacks?|hackathons?|hacky|break(ing)? (a leg|the ice|even|down|up)"
    r"|cracked (version|software|app|game)s?|poison ivy|toy guns?|glue guns?|water guns?|nerf guns?|heat guns?|attack (surface|vector)s?|virus scan|anti-?virus"
    r"|format (the |this |my )?(code|document|text|table|cell|date|string|json|output|file)s?|crack(ing)? (a joke|up)|fake news",
)


@dataclass(frozen=True)
class Token:
    kind: str
    id: str
    start: int
    end: int
    words: str


def _compile(zh: str, en: str) -> list[re.Pattern]:
    out = []
    if zh:
        out.append(re.compile(zh, re.I))
    if en:
        out.append(re.compile(rf"(?<![\w'])(?:{en})(?![\w'])", re.I))
    return out


_TABLE: list[tuple[str, str, re.Pattern]] = []
for _kind, _entries in ((ACT, _ACTS), (THING, _THINGS), (OWN, _OWN)):
    for _id, _zh, _en in _entries:
        _TABLE.extend((_kind, _id, rx) for rx in _compile(_zh, _en))
_TABLE.extend((IDIOM, "idiom", rx) for rx in _compile(*_IDIOMS))
_MARK_TABLE = [(mid, rx) for mid, zh, en in _MARKS for rx in _compile(zh, en)]
_RANK = {ACT: 0, IDIOM: 1, OWN: 2, THING: 3}


_NOUN_OR_VERB = frozenset({"email", "e-mail", "mail", "text", "texts", "message", "messages", "post", "report", "order", "book",
                           "play", "list", "show", "note", "notes", "copy", "file", "share", "set", "design", "draft", "record",
                           "search", "change", "update", "fix", "lock", "export", "import", "download", "upload", "backup",
                           "review", "count", "view", "check", "exit", "end", "sleep", "scan", "print", "move", "split"})
_DETERMINER = re.compile(r"\b(my|her|his|their|our|your|the|a|an|this|that|these|those|\w+'s)\s+$", re.I)


def scan(text: str) -> tuple[Token, ...]:
    """The concepts in ``text``, in order: the longest match wins where two overlap (an idiom over the act inside it,
    "打开" over "打"); between equals, an act, then an idiom, then a possessive, then a thing."""
    found = []
    for kind, cid, rx in _TABLE:
        for m in rx.finditer(text):
            if m.end() <= m.start():
                continue
            if kind == ACT and m.group(0).lower() in _NOUN_OR_VERB and _DETERMINER.search(text[:m.start()]):
                continue  # "her email", "the report": a thing here, not an act
            found.append(Token(kind, cid, m.start(), m.end(), m.group(0)))
    found.sort(key=lambda t: (-(t.end - t.start), _RANK[t.kind], t.start))
    taken: list[Token] = []
    for t in found:
        if all(t.end <= o.start or t.start >= o.end for o in taken):
            taken.append(t)
    return tuple(sorted(taken, key=lambda t: t.start))


def marks(text: str, tokens: tuple[Token, ...] = ()) -> dict[str, str]:
    """How it is said: each mark present, with the words that show it. A wish inside an idiom ("想死你了") does
    not count."""
    idioms = [(t.start, t.end) for t in tokens if t.kind == IDIOM]
    out: dict[str, str] = {}
    for mid, rx in _MARK_TABLE:
        if mid == "other_does":
            continue
        for m in rx.finditer(text):
            if mid != "wish" or not any(a <= m.start() and m.end() <= b for a, b in idioms):
                out.setdefault(mid, m.group(0))
                break
    return out


_OTHER_DOES = [rx for mid, rx in _MARK_TABLE if mid == "other_does"]


def other_does(before: str) -> bool:
    """The words before an act say someone else is the one doing it ("他一直在…", "he keeps …")."""
    before = before.rstrip()
    return any(rx.search(before) for rx in _OTHER_DOES)


_NOT_A_WIPE = re.compile(
    r"格式化(一下)?(这段|这个|这份|我的)?(代码|文档|文本|表格|json|单元格|日期|字符串|输出)"
    r"|\bformat (the |this |my )?(code|document|text|table|cell|date|string|json|output|file)s?\b", re.I)


def mask(text: str) -> str:
    """``text`` with "格式化这段代码" blanked out (same length), so that a weighty-act pattern does not read it as
    wiping a disk."""
    return _NOT_A_WIPE.sub(lambda m: "□" * len(m.group(0)), text)


def language(text: str) -> str:
    """zh when the sentence is written in Chinese characters, else en."""
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return "zh" if cjk and cjk * 2 >= latin else ("en" if latin else "zh")


__all__ = ["ACT", "IDIOM", "MARK", "ORDINARY", "OWN", "PRIMS", "Prim", "THING", "Token", "language", "marks", "mask",
           "other_does", "scan"]
