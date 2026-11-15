"""The mind's words: what it recognises in a request, in Chinese and English.

This is knowledge written down, not learned. Each entry names a concept the
reasoning works with: a kind of act, a kind of thing, a kind of person, a
feeling, a pressure that marks a scam, a sign that the person has thought it
over. A pattern is the evidence that the concept is present; the reasoning
(``reason.py``) decides what it means together with everything else.
"""
from __future__ import annotations

import re


def rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


# ---- acts: what the request would do ---------------------------------------------------------------
_SECRET = r"(验证码|校验码|动态码|短信码|支付密码|银行卡密码|密码|身份证(号|照片|正反面)?|银行卡(号|照片)?|卡号|CVV|安全码)"
_GIVE = r"(发给|发送给|告诉|报给|念给|说给|给|提供给|截图给|拍给)"
_UNKNOWN = r"(脚本|程序|软件|安装包|\.exe|exe|apk|插件|补丁|外挂|破解版?|激活工具|链接|文件)"

ACTS = (
    ("share_secret", rx(rf"(把|将)?\s*(我的|这个|收到的|刚收到的)?\s*{_SECRET}.{{0,6}}{_GIVE}|{_GIVE}(?!我).{{0,8}}{_SECRET}"
                        r"|(send|give|tell|share|read out)\b.{0,30}\b(verification code|one[- ]time code|otp|password|pin"
                        r"|card number|cvv|id card)|(verification code|password|otp|card number)\b.{0,20}\bto\b")),
    ("run_unknown", rx(rf"(运行|执行|打开|安装|装上|点开|双击)(一下)?.{{0,12}}{_UNKNOWN}"
                       rf"|{_UNKNOWN}.{{0,10}}(运行|执行|安装|装上|打开|点开)"
                       r"|(关掉|关闭|卸载|禁用)(一下)?(杀毒|防火墙|安全软件|电脑管家|Windows ?Defender|Defender)"
                       r"|\b(run|install|open|execute)\b.{0,24}\b(script|program|app|\.exe|exe|apk|installer|attachment)\b"
                       r"|\b(disable|turn off|uninstall)\b.{0,12}\b(antivirus|firewall|defender)\b")),
    ("lend", rx(r"借(钱|款)?给|借(他|她|对方|网友|朋友|同事)|借.{0,6}[0-9一二两三四五六七八九十百千万几]+\s*(块|元|万|千)"
                r"|\b(lend|loan)\b")),
    ("crisis_give", rx(r"(东西|钱|存款|遗产)(都|全)?(送人|分给|留给)|把.{0,8}(都|全)?(送人|分掉)|give (all )?(my )?(things|stuff) away")),
    ("wipe", rx(r"格式化|重装系统|恢复出厂|抹掉(整个)?(磁盘|硬盘)|重新分区|format\s+(the\s+|my\s+)?([\w:]+\s+){0,2}(disk|drive|usb|phone)|factory reset"
                r"|wipe (the |my )?(disk|drive|phone|laptop)")),
    ("track", rx(r"(偷偷|悄悄|背着(他|她))?(查|看|翻|定位|监控|跟踪|偷看)(一下)?(他|她|男朋友|女朋友|老公|老婆|对象|前任|前男友|前女友"
                 r"|孩子|儿子|女儿)的?(位置|定位|手机|聊天记录|微信|消息|短信|通话记录|行踪|相册)"
                 r"|(监控|跟踪|定位|偷看)(一下)?(他|她|男朋友|女朋友|老公|老婆|对象|前任|前男友|前女友)"
                 r"|(track|spy on|read) (his|her|my (boy|girl)friend's|my (husband|wife)'s|my ex'?s?)")),
    ("transfer", rx(r"转账|转给|(?<!跳)转(到|入|进)(?!下一|上一)|转\s*[0-9]+\s*给|红包给|发红包|转(一大笔|一笔|很多|好多|些|点)?钱|把.{0,10}钱.{0,4}转|转(过去)?\s*[0-9一二两三四五六七八九十百千万几]+\s*(块|元|万|千)|给.{1,10}转(钱|账)?\s*[0-9一二两三四五六七八九十百千万几]*"
                    r"|打钱|汇款|垫付|充值到|\bwire\b|transfer\b.{0,20}\b(money|funds|to)\b|send (him |her |them )?money|venmo|zelle"
                    r"|paypal (him|her)|\btransfer [0-9]")),
    ("close_account", rx(r"注销|销号|删号|delete my (\w+ )?account|close my (\w+ )?account|deactivate my (\w+ )?account")),
    ("resign", rx(r"辞职|离职|递辞呈|quit my job|resign")),
    ("post", rx(r"发布|发帖|发(到|个|条|一条)?(朋友圈|微博|动态|推特|小红书|抖音|公众号|群里)|公开|\bpost\b|publish|tweet")),

    ("send", rx(r"发(给|送|出去)|发(个|封|一封|条|一条)?(邮件|消息|短信|微信)|回复|转发|寄给|告诉(他|她|老板|领导|妈|爸)"
                r"|\bsend\b|\breply\b|\bforward\b|\be-?mail\b|\btext (him|her)\b|\bmessage (him|her|them)\b")),
    ("sell", rx(r"卖掉|卖了|挂(到)?闲鱼|出掉|二手卖|\bsell\b")),
    ("buy", rx(r"(?<!新)买(?!的)|下单|购买|订(一?张|个)?(票|机票|酒店|外卖)|续费|\bbuy\b|purchase|order")),
    ("delete", rx(r"删除|删掉|删了|删去|删一下|删干净|删光|删个干净|清空|清掉|扔掉|丢掉|抹掉|移除|\brm\b|\bdelete\b|\bremove\b|\berase\b")),
)

# ---- things: what an act is done to ------------------------------------------------------------------
KINDS = (
    ("memories", rx(r"照片|相片|相册|合照|合影|视频|录像|聊天记录|对话记录|日记|信件|情书|语音|录音|纪念|回忆|礼物|送的(东西)?"
                    r"|photos?|pictures?|videos?|chat (logs?|history)|diary|letters|voice (notes|memos)|memories|gifts?")),
    ("work", rx(r"文档|报告|论文|代码|项目|周报|合同|报表|ppt|设计稿|方案|简历|document|report|thesis|code|project|contract")),
    ("junk", rx(r"缓存|安装包|临时文件|垃圾|重复的?|空文件夹|下载文件夹里的|没用的|cache|installers?|temp|duplicates?|junk")),
    ("account", rx(r"账号|帐号|账户|微信号|\baccount\b")),
    ("device", rx(r"[A-Za-z]\s*盘|硬盘|磁盘|移动硬盘|U盘|电脑|手机|笔记本|系统|drive|disk|laptop|phone")),
)

# ---- people a request involves --------------------------------------------------------------------------
PARTIES = (
    ("ex", rx(r"前任|前男友|前女友|前妻|前夫|\bmy ex\b|\bex-")),
    ("partner", rx(r"男朋友|女朋友|老公|老婆|对象|另一半|boyfriend|girlfriend|husband|wife|partner")),
    ("family", rx(r"[妈爸]|父母|爷爷|奶奶|姥姥|姥爷|外婆|外公|儿子|女儿|孩子|哥哥?|姐姐?|弟弟?|妹妹?|家人|mom|dad|parents|son|daughter|kids?|family")),
    ("boss", rx(r"老板|领导|上司|主管|经理|\bboss\b|manager")),
    ("colleague", rx(r"同事|colleague|coworker")),
    ("friend", rx(r"朋友|同学|闺蜜|兄弟|室友|friend|roommate")),
    ("stranger", rx(r"网友|陌生人|刚认识|没见过面|网上认识|自称|客服|警察说|公检法|群里的人|对方说是|stranger"
                    r"|someone (i met )?online|met online|customer service|claims to be|says (he|she|they) (is|are) from|support agent")),
    ("public", rx(r"朋友圈|微博|大家|所有人|公开|群里|public|everyone")),
    ("him_her", rx(r"(?<![其吉])他(?!们)|她(?!们)|\bhim\b|\bher\b")),
)

# ---- feelings, pressure, and having thought it over --------------------------------------------------------
CRISIS = rx(r"不想活|想死|去死吧?我|自杀|轻生|结束(自己|生命)|活着没意思|活不下去|不如死了|跳楼|割腕|安眠药|吞药"
            r"|kill myself|suicide|end my life|don'?t want to live|better off dead")
SAD = rx(r"难过|伤心|心痛|心碎|想哭|哭了|失恋|好累|绝望|孤单|没意思|\bsad\b|heartbroken|crying|lonely|hopeless|devastated|miserable")
ANGRY = rx(r"生气|气死|恨死?|烦死|受够了|一刀两断|滚|讨厌死|忍不了|吵架|吵了一架|mad|angry|furious|hate|sick of|fed up")
BREAKUP = rx(r"分手|分了|离婚|被甩|甩了|出轨|背叛|broke up|break ?up|divorce|cheated")
PANIC = rx(r"着急|急用|赶紧|马上|立刻|来不及|不然就|否则|快点|怎么办|吓死|慌|urgent|right now|immediately|asap|or else|panic")
IMPULSE = rx(r"马上|立刻|现在就|赶紧|一个不留|彻底|永远|统统|干干净净|全删|全部删|right now|immediately|all of it|forever")
CONSIDERED = rx(r"想清楚了|考虑(了)?(很久|好久|过了|清楚)|冷静下来了|不是一时冲动|已经决定|决定好了|别劝我|我确定|认真想过"
                r"|thought it (through|over)|i'?ve decided|not on impulse|i'?m sure")
SCAM = rx(r"安全账户|安全帐户|保证金|解冻|刷流水|刷单返利|垫付|验证码|中奖|先交|手续费|公检法|涉嫌洗钱|洗钱|内部消息|稳赚|高收益|带你赚钱"
          r"|冻结|不要告诉(别人|家里|任何人)|别告诉(别人|家里|任何人)|贷款.{0,4}(先|交)|退款.{0,4}(先|要)|客服(说|让|要)"
          r"|security account|safe account|frozen|unfreeze|guaranteed returns?|gift cards?|don'?t tell anyone"
          r"|refund fee|processing fee|wire it now|keep (it|this) (a )?secret")
INSULT = rx(r"滚|去死|贱|恨你|你个|垃圾|混蛋|傻[逼子]|不要脸|王八|scum|idiot|hate you|go to hell|loser")
REGRET_PAST = rx(r"恢复|找回|后悔|删错|还原|recover|restore|undelete|regret")
OWN_SYSTEM = rx(r"测试(环境|账号|帐号|用户|服务器|站|库)|staging|sandbox|沙箱|本地|localhost|开发环境|dev\s*env"
                r"|(我|我们|自己|公司|团队)(自己)?的?(测试)?(系统|网站|服务|产品|应用|平台|app|后台)|内部系统|压测|自动化测试|e2e")
FAMILY_USE = rx(r"给(家里人|家人|爸妈|父母|孩子|老人|员工|同事)(用|注册)|帮(家里人|家人|爸妈|父母)")
PROMO = rx(r"新人(优惠|红包|券)|拉新|领券|抽奖|薅|助力|砍一刀|返现|promo|referral bonus|giveaway")
BACKED_UP = rx(r"备份过|已经备份|备份好了|已备份|新(买的)?(硬盘|盘|U盘|移动硬盘|电脑|手机)|空(的)?(硬盘|盘)|里面没(有)?(东西|用的)"
               r"|backed up|new (\w+ )?(drive|disk|laptop|phone)|empty (drive|disk)|it'?s empty|nothing on it")
SELL_DEVICE = rx(r"卖|转手|送人|二手|换(新)?(手机|电脑)|sell|give (it )?away|trade in")
FIX_SYSTEM = rx(r"卡|慢|中毒|病毒|蓝屏|重装|崩溃|坏了|slow|virus|crash|broken|reinstall")
SPACE = rx(r"空间|内存|存储|满了|不够|腾|太大|storage|space|full|room")
TIDY = rx(r"重复|没用的?|模糊|垃圾|乱|整理|duplicates?|blurry|junk|clutter|tidy")
ASKED_BY = rx(r"(他|她|对方|前任).{0,4}(让|要|要求|希望)我删|隐私|(he|she|they) asked me to delete|privacy")
SAFETY = rx(r"孩子|儿子|女儿|老人|爸|妈|失联|走丢|联系不上|不接电话|没回家|missing|can'?t reach|kid|child")
SUSPECT = rx(r"出轨|怀疑|不放心|偷偷|背着我|骗我|cheating|suspect|secretly|behind my back")
MONEY = rx(r"(?P<n>[0-9]+(\.[0-9]+)?|[一二两三四五六七八九十百千几]+)\s*(?P<u>万|千|块|元|块钱|rmb|yuan|dollars?|usd|美元)"
            r"|[¥$]\s*(?P<m>[0-9][0-9,]*)")
PURPOSE = rx(r"(因为|为了|好让|免得|省得|这样|so that|because|since)\s*(?P<why>[^，。；,;.!?！？]{2,40})")
CONTENT = rx(r"(说|告诉(他|她|他们)|内容是|写着|：|:|\bsaying\b|\bthat says\b|\btelling (him|her|them)\b)\s*(?P<what>.{2,80})$")
QUESTION = rx(r"[？?]\s*$|吗[。？?]?\s*$|^(怎么|如何|为什么|是不是|能不能|该不该|要不要)|^(how|what|why|should i|can i|is it)\b")
NEGATED = rx(r"^(别|不要|不用|先不|暂时不|don'?t|do not|never)")
NEAR_NO = rx(r"(先别|别|不要|不用|先不|暂时不|千万别|不想|不打算|don'?t|do not|never|not)\s*(急着|马上|现在|去|再)?\s*$")  # just before an act
MONEY_BIG = rx(r"一大笔|很多钱|好多钱|全部(的)?(积蓄|存款|钱)|所有(的)?(钱|积蓄|存款)|积蓄|养老钱|all my (money|savings)|life savings")
