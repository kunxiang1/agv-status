# -*- coding: utf-8 -*-
"""按图分组订阅的纯逻辑用例：事件归属哪张图 + 该投给哪些订阅分组。

背景：四图全推等于每台客户端收 4 倍数据（看不到的图推过去就是白费流量与 CPU）。
分组后浏览器连 `/api/events?map=BB` 只收 BB 的事件；**与地图无关的系统级事件（货架表 pods 等）
照旧发给所有人**——搜索要用的全量货架表就靠这条，分组不能把它挡掉。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config as C  # noqa: E402
import rcs_push  # noqa: E402
import server  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print("%s %s %s" % ("✔" if cond else "✘", name, detail))
    if not cond:
        fails.append(name)


# ---- 1. event_map：四种推送事件各从哪个字段取地图 ----
check("status 取 a.mapCode", rcs_push.event_map({"e": "status", "a": {"mapCode": "BB"}}) == "BB")
check("path/offline/alarm 取 map",
      rcs_push.event_map({"e": "path", "map": "CC"}) == "CC" and
      rcs_push.event_map({"e": "offline", "map": "DD"}) == "DD" and
      rcs_push.event_map({"e": "alarm", "map": "EE"}) == "EE")
check("货架表 pods 等系统级事件 → 无地图", rcs_push.event_map({"e": "pods", "pods": {}}) == "")
check("缺字段不抛异常", rcs_push.event_map({}) == "" and rcs_push.event_map({"a": None}) == "")

# ---- 2. route_keys：该投给哪些分组 ----
check("分组开启：有图事件 → 『全部』+ 该图", server.route_keys("BB") == ("", "BB"))
check("分组开启：无图事件 → 全部（None）", server.route_keys("") is None)
old = C.SSE_GROUP_BY_MAP
try:
    C.SSE_GROUP_BY_MAP = False
    check("分组关闭：任何事件都发全部（回退开关有效）",
          server.route_keys("BB") is None and server.route_keys("") is None)
finally:
    C.SSE_GROUP_BY_MAP = old
check("开关恢复默认（不影响后续用例）", C.SSE_GROUP_BY_MAP is True)

print()
if fails:
    print("失败: " + "；".join(fails))
    sys.exit(1)
print("按图分组订阅用例通过 ✔")
