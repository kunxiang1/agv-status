# 从运行中的 server SSE 抓告警样本，按前端四道闸门模拟去留
import os, subprocess, json, re, time, datetime
CUR = "CC"   # 改成你现场 maps/ 下的地图简称
DB = json.loads(re.search(r"ALARM_DB=(\{.*\})", open(os.path.join(os.path.dirname(__file__), "..", "alarm.js"), encoding="utf8").read(), re.S).group(1))
def owner(m):
    import re
    return m["src"] if re.fullmatch(r"\d+", m.get("src") or "") else (m["p1"] if re.fullmatch(r"\d+", m.get("p1") or "") else m.get("p1") or "")
def keep(m):
    if m.get("map") and m["map"] != CUR: return "跨图丢"
    o = owner(m)
    if not re.fullmatch(r"\d+", o): return "非车号丢(SN/平台)"
    if not (DB.get(m["mt"]) or {}).get("sub", {}).get(m["st"]): return "无字典丢"
    age = datetime.datetime.now() - datetime.datetime.strptime(m["time"], "%Y-%m-%d %H:%M:%S")
    if age.total_seconds() > 86400: return "僵尸告警丢(%d天)" % (age.days,)
    return "✅显示 %s：#%s %s" % (m["map"], o, DB[m["mt"]]["sub"][m["st"]]["n"])
r = subprocess.run("curl -s -m 10 -N http://127.0.0.1:8899/api/events", shell=True, capture_output=True).stdout.decode("utf8", "replace")
seen = {}
for ln in r.splitlines():
    if not ln.startswith("data: ") or '"e": "alarm"' not in ln: continue
    m = json.loads(ln[6:])
    if m["stat"] != "1": continue
    seen.setdefault(m["guid"], (m["time"], keep(m)))
for t, v in sorted(seen.values()):
    print(t, "→", v)
print("样本数:", len(seen))
