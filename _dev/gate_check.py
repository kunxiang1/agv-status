# -*- coding: utf-8 -*-
"""从运行中的 server SSE 抓告警样本，按前端四道闸门模拟"去留"，用于核对闸门是否过严/过松。

用法: python _dev/gate_check.py [地图，默认 config.DEFAULT_MAP] [端口，默认 config.WEB_PORT]
前置: python server.py 已在跑（本脚本只读 SSE，不产生对 RCS 的主动请求）
"""
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config as C                                       # noqa: E402

CUR = sys.argv[1] if len(sys.argv) > 1 else C.DEFAULT_MAP
PORT = sys.argv[2] if len(sys.argv) > 2 else str(C.WEB_PORT)

with open(os.path.join(ROOT, "alarm.js"), encoding="utf-8") as f:
    DB = json.loads(re.search(r"ALARM_DB=(\{.*\});", f.read(), re.S).group(1))


def owner(m):
    """与 index.html ownerOf() 同义：AlarmSource / AlarmParam1 里的纯数字才是车号。"""
    for k in ("src", "p1"):
        v = m.get(k) or ""
        if re.fullmatch(r"\d+", v):
            return v
    return m.get("p1") or ""


def keep(m):
    """→ 判定结论文案（✅显示 / 丢在哪道闸门）"""
    if m.get("map") and m["map"] != CUR:
        return "跨图丢"
    o = owner(m)
    if not re.fullmatch(r"\d+", o):
        return "非车号丢(SN/平台)"
    if not (DB.get(m["mt"]) or {}).get("sub", {}).get(m["st"]):
        return "无字典丢"
    try:
        age = datetime.datetime.now() - datetime.datetime.strptime(m["time"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, ValueError):
        return "时间字段异常丢"
    if age.total_seconds() > 86400:
        return "僵尸告警丢(%d天)" % age.days
    return "✅显示 %s：#%s %s" % (m["map"], o, DB[m["mt"]]["sub"][m["st"]]["n"])


if __name__ == "__main__":
    url = "http://127.0.0.1:%s/api/events" % PORT
    # --noproxy：本机若配了 HTTP_PROXY，curl 会绕去代理，导致连不上自己的服务
    r = subprocess.run(["curl", "-s", "--noproxy", "*", "-m", "10", "-N", url],
                       capture_output=True).stdout.decode("utf8", "replace")
    seen = {}
    for ln in r.splitlines():
        if not ln.startswith("data: ") or '"e": "alarm"' not in ln:
            continue
        try:
            m = json.loads(ln[6:])
        except ValueError:
            continue
        if m.get("stat") != "1":                          # 只看新告警，恢复消息无展示意义
            continue
        seen.setdefault(m.get("guid"), (m.get("time", ""), keep(m)))
    for t, v in sorted(seen.values()):
        print(t, "→", v)
    print("样本数:", len(seen), "（地图 %s，端口 %s）" % (CUR, PORT))
