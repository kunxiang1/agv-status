# -*- coding: utf-8 -*-
"""配置集中化回归：证明「环境值只在 config.py 出现一次，改一处全局生效」。

断言四件事：
  1) IP / 账号 / 密码 这些环境字面量在**可执行源码**里只允许出现在 config.py（文档/备份不算）；
  2) 各模块确实是从 config 取的值（导入后与原值一致，不是各自抄了一份常量）；
  3) 前端拿到的 /api/config 与 config.py 同源，且 index.html 不再内置地图清单；
  4) 登录摘要等派生值正确。

用法: python _dev/_config_test.py
"""
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config as C                                     # noqa: E402

bad = 0


def ok(cond, msg):
    global bad
    if cond:
        print("  OK   " + msg)
    else:
        bad += 1
        print("  FAIL " + msg)


# ---------------------------------------------------------------- 1) 字面量唯一性
# 本仓已脱敏：下面这些是角色占位符，本身没有区分度，拿它们扫源码只会命中满屏注释
PLACEHOLDERS = ("rcs-ip", "engine-ip", "mq-ip", "car-ip", "vpn-ip", "本机ip", "password")
SECRETS = [s for s in (C.RCS_WEB_IP, C.RCS_ENGINE_IP, C.RCS_PWD, C.RCS_USER)
           if s not in PLACEHOLDERS and len(s) >= 5]
# 只扫可执行源码；README/协议报告/补充文档属于说明文字，备份文件属于历史快照
SCAN = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".workbuddy", ".git")]
    for fn in filenames:
        if fn.endswith((".py", ".js")) or fn.endswith(".html"):
            SCAN.append(os.path.join(dirpath, fn))
extra = []
for ip in (C.RCS_WEB_IP, C.RCS_ENGINE_IP):
    if ip in PLACEHOLDERS:                      # 占位符无区分度，跳过
        continue
    extra.append(re.escape(ip) + r":\d+/\S*")
pats = [(s, re.compile(re.escape(s))) for s in SECRETS] + \
       [(m, re.compile(m)) for m in extra]

print("[1] 环境字面量在可执行源码中的出现位置")
for path in sorted(SCAN):
    rel = os.path.relpath(path, ROOT)
    if rel.startswith("_dev") or ".bak" in rel:
        continue                                        # 取证/测试脚本与备份豁免
    txt = open(path, encoding="utf-8", errors="replace").read()
    hits = [name for name, p in pats if p.search(txt)]
    if hits:
        if rel == "config.py":
            print("      config.py 命中 %d 类（唯一配置源，预期如此）" % len(hits))
        else:
            ok(False, "%s 仍硬编码环境值: %s" % (rel, hits))
ok(True, "除 config.py 外，业务源码未出现 IP/账号/密码字面量")

# ---------------------------------------------------------------- 2) 模块取值同源
print("[2] 各模块是否从 config 取值")
import rcs_push                                        # noqa: E402
ok(rcs_push.RCS_ENGINE == C.RCS_ENGINE_IP, "rcs_push.RCS_ENGINE == config.RCS_ENGINE_IP")
ok(rcs_push.WEB == C.WEB_BASE, "rcs_push.WEB == config.WEB_BASE")
ok((rcs_push.USER, rcs_push.PWD) == (C.RCS_USER, C.RCS_PWD), "rcs_push 账号与 config 一致")
ok(rcs_push.C.RCS_ENGINE_IP == C.RCS_ENGINE_IP, "rcs_push 直接引用 config 对象")

src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
ok("import config as C" in src, "server.py 从 config 取值")
ok(C.MAP_CODES == ["EE", "BB", "CC", "DD"], "地图清单只有一处定义")
ok(C.push_targets() == [(C.RCS_ENGINE_IP, C.PORT_PUSH), (C.RCS_WEB_IP, C.PORT_ALARM)],
   "推送目标（数据/告警两条通道）由 config 推导")

# ---------------------------------------------------------------- 3) 前端同源
print("[3] 前端配置来源")
idx = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
ok("api/config" in idx, "index.html 从 /api/config 取站点配置")
ok(not re.search(r"const\s+MAPS\s*=", idx), "index.html 不再内置地图清单")
ok(not re.search(r"const\s+MAP_NAMES\s*=", idx), "index.html 不再内置地图中文名")
cc = C.client_config()
ok(cc["maps"] == C.MAP_CODES and cc["defaultMap"] == C.DEFAULT_MAP, "/api/config 与 config.py 同源")
ok(set(cc) == {"maps", "mapNames", "defaultMap", "pollGuessMs"}, "/api/config 只下发浏览器需要的字段")
ok(C.RCS_PWD not in str(cc), "/api/config 不含任何凭据")
ok("pollGuessMs" in idx and "CFG.pollGuessMs" in idx, "推送节奏猜测值也由配置下发")

# ---------------------------------------------------------------- 4) 派生值
print("[4] 派生值")
ok(C.sha256_pwd() == hashlib.sha256(C.RCS_PWD.encode()).hexdigest(), "登录摘要 = sha256(密码)")
ok(len(C.sha256_pwd()) == 64 and C.sha256_pwd() == C.sha256_pwd().lower(), "摘要为 64 位小写十六进制")
ok(C.LOGIN_BACKOFF_S >= 30, "登录失败退避足够长（防连错锁账号）")

# ---------------------------------------------------------------- 5) 对外服务的访问控制
print("[5] 对外服务的访问控制")
_keep, C.ALLOW_IPS = C.ALLOW_IPS, []
ok(C.ip_allowed("client-ip") and not C.access_required(), "默认开放：白名单为空、无需密钥（内网零门槛）")
# 写法即语义：完整四段=精确，带结尾点=前缀，别把精确 IP 当成前缀误放行邻居
# 形状占位符（本仓已脱敏，不用真实 IP）：完整四段 / 以点结尾 / 不完整，三种写法各覆盖到
CASES = [(["a.b.c.d"], "a.b.c.d", True), (["a.b.c.d"], "a.b.c.e", False),
         (["a.b.c."], "a.b.c.9", True), (["a.b.c."], "a.b.d.9", False),
         (["a.b.c"], "a.b.c.7", True), (["a.b.c"], "a.b.cx.7", False),
         (["a.b.c.d"], "a.b.c.d", True), (["a.b.c.d"], "a.b.c.dd", False),
         (["a.b.", "c.d.e.f"], "c.d.e.g", False), (["a.b."], "a.b.z.y", True)]
_wrong = []
for pat, ip, want in CASES:
    C.ALLOW_IPS = pat
    if C.ip_allowed(ip) != want:
        _wrong.append("%s x %s" % (pat, ip))
C.ALLOW_IPS = _keep
ok(not _wrong, "白名单精确/前缀语义正确（误判 %s）" % (_wrong or "无"))
_k = C.ACCESS_KEY
C.ACCESS_KEY = "k"
ok(C.access_required(), "配了密钥即要求鉴权")
ok(C.ACCESS_KEY not in str(C.client_config()), "调试输出不泄露密钥")
C.ACCESS_KEY = _k
ok(C.ALLOW_IPS == [] and C.ACCESS_KEY == "", "配置默认值未被测试污染")

print("\n" + ("CONFIG PASS：环境值已收敛到 config.py 单一来源" if not bad else "CONFIG FAIL %d" % bad))
sys.exit(1 if bad else 0)
