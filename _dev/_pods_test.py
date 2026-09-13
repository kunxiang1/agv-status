# -*- coding: utf-8 -*-
"""货架↔储位表离线用例：不动网络，用现场拉回的 JSON 固件验证译码/组装/落点/增量维护。

固件来源：_dev/rest_out/podBerthMat_{BB,DD,EE,CC}.json（真实 RCS 数据，2026-09-12 拉）。
若固件缺失则跳过对应对账（译码/组装/增量维护部分永远可跑）。
增量维护用例全程不碰真网：落盘路径改到临时文件，地图节点用真实 maps/BB.json。
"""
import json
import math
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import rcs_pods  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print("%s %s %s" % ("✔" if cond else "✘", name, detail))
    if not cond:
        fails.append(name)


# ---- 1. 译码纯函数 ----
xy = rcs_pods.decode_berth_xy("0004771BB0052930")
check("mapDataCode 译码格式", xy == (4.771, 52.930), "got %r" % (xy,))
check("非法串拒绝", rcs_pods.decode_berth_xy("garbage") is None and
      rcs_pods.decode_berth_xy("") is None and rcs_pods.decode_berth_xy(None) is None)

# ---- 2. 组装纯函数（含 P 前缀托盘标记与坏行剔除） ----
rows = [{"podCode": "L00278", "mapDataCode": "0004771BB0052930", "areaCode": "zfh",
         "positionCode": "004771BB052930", "materialLot": ""},
        {"podCode": "P00281", "mapDataCode": "0057775DD0032020", "areaCode": "zk3",
         "positionCode": "057775DD032020"},
        {"podCode": "L0bad0", "mapDataCode": "nope", "areaCode": ""},
        {"podCode": "", "mapDataCode": "", "areaCode": ""}]
got = rcs_pods.assemble("BB", rows)
check("组装剔除坏行", len(got) == 2, "got %d" % len(got))
check("L 货架字段", got[0] == {"code": "L00278", "x": 4.771, "y": 52.93, "pal": False,
                               "area": "zfh", "pos": "004771BB052930"}, json.dumps(got[0], ensure_ascii=False))
check("P 托盘标记", got[1]["pal"] is True and got[1]["code"] == "P00281")

# ---- 3. 简称解析（动态发现的核心纯函数） ----
names = rcs_pods.parse_short_names([
    {"code": "EE", "shortName": "二厂1楼"}, {"code": "CC", "shortName": "二厂2楼"},
    {"code": "BB", "shortName": ""}, {}, {"code": "  ", "shortName": "x"},
    {"shortName": "无code行"}])
check("简称解析/缺省回退图码", names == {"EE": "二厂1楼", "CC": "二厂2楼", "BB": "BB"},
      json.dumps(names, ensure_ascii=False))

# ---- 4. 真实固件对账：全部货架必须精确落在地图节点上（实测距离 0.0000） ----
for mp in ("BB", "DD", "EE", "CC"):
    fp = os.path.join(ROOT, "_dev", "rest_out", "podBerthMat_%s.json" % mp)
    if not os.path.isfile(fp):
        print("－ 固件缺失，跳过 %s 对账" % mp)
        continue
    fx = json.load(open(fp, encoding="utf-8"))
    if str(fx.get("code")) != "0" or not fx.get("data"):
        print("－ %s 固件是失败样本(code=%s)，跳过" % (mp, fx.get("code")))
        continue
    got = rcs_pods.assemble(mp, fx["data"])
    # 条数用自洽口径（固件本身即真值）：解析不丢行、且现场确实是这个量级
    # ——写死数字会在每次重拉固件（车扛走/放下货架）后误报
    check("%s 全量条数=固件行数" % mp, len(got) == len(fx["data"]) and len(got) >= 40,
          "got %d / 固件 %d" % (len(got), len(fx["data"])))
    nodes = json.load(open(os.path.join(ROOT, "maps", mp + ".json"), encoding="utf-8"))["nodes"]
    worst = 0.0
    for p in got:
        d = min(math.hypot(n[1] - p["x"], n[2] - p["y"]) for n in nodes)
        worst = max(worst, d)
    check("%s 落点全部贴节点(<0.01m)" % mp, worst < 0.01, "max=%.4f" % worst)
    check("%s 货架号非空" % mp, all(p["code"] for p in got))

    # ---- 4b. 落点规则的数据驱动体检（用真实储位坐标，全部 420 个）----
    cands = rcs_pods._berth_nodes(mp)
    pathxy = {(round(n[1], 3), round(n[2], 3)) for n in nodes if n[3] == 16}
    check("%s 候选节点已剔除路径点" % mp,
          len(cands) == sum(1 for n in nodes if n[3] != 16) and
          not ({(round(x, 3), round(y, 3)) for x, y in cands} & pathxy))

    reject = 0            # 车停在货架真位置上：必须一次不弃权、且返回同一格
    for p in got:
        spot = rcs_pods._nearest_berth(mp, p["x"], p["y"])
        if not spot or math.hypot(spot[0] - p["x"], spot[1] - p["y"]) > 0.01:
            reject += 1
    check("%s 真值位置零误杀（%d 个）" % (mp, len(got)), reject == 0, "误杀 %d 个" % reject)

    flip = []             # 车停在相邻两格中点：位置数据分不清 → 必须弃权，绝不猜
    for p in got:
        for q in got:
            gap = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
            if rcs_pods._PLACE_SAME_SPOT < gap < 1.0:
                mid = ((p["x"] + q["x"]) / 2, (p["y"] + q["y"]) / 2)
                if rcs_pods._nearest_berth(mp, mid[0], mid[1]) is not None:
                    flip.append((p["code"], q["code"], round(gap, 2)))
    check("%s 相邻格中点必弃权（无猜错）" % mp, not flip, "猜错 %d 例 %s" % (len(flip), flip[:3]))

# ---- 5. 增量维护（取/放货不重拉）：落盘改临时文件，回调改收集器，全程不碰真网真盘 ----
tmpdir = tempfile.mkdtemp(prefix="pods_store_")
real_store, real_cb = rcs_pods.STORE_PATH, rcs_pods._on_updated
real_cache = json.dumps(rcs_pods.payload(), ensure_ascii=False)
real_bnodes = rcs_pods._berth_nodes
rsc = []
rcs_pods.STORE_PATH = os.path.join(tmpdir, "store.json")
rcs_pods.set_on_updated(lambda: rsc.append(1))
try:
    # 落点用「真实在库货架坐标」：它们天然是候选节点（路径点已过滤，不受影响）
    fpb = os.path.join(ROOT, "_dev", "rest_out", "podBerthMat_BB.json")
    seeds = [(p["x"], p["y"]) for p in rcs_pods.assemble(
             "BB", json.load(open(fpb, encoding="utf-8"))["data"])] if os.path.isfile(fpb) else []
    if len(seeds) < 4:                               # 固件缺失时兜底：取非路径点节点
        seeds += [(n[1], n[2]) for n in
                  json.load(open(os.path.join(ROOT, "maps", "BB.json"), encoding="utf-8"))["nodes"]
                  if n[3] != rcs_pods._PATH_TYPE][:4]
    b1, b2, b3, b4 = seeds[:4]

    with rcs_pods._cache_lock:                       # 播种：L00278 停在 b1、P00281 停在 b2
        rcs_pods._cache["pods"] = {"BB": [
            {"code": "L00278", "x": b1[0], "y": b1[1], "pal": False, "area": "", "pos": ""},
            {"code": "P00281", "x": b2[0], "y": b2[1], "pal": True, "area": "", "pos": ""}]}

    ch = rcs_pods.apply_car_event("", "L00278", 0, 0, "BB")          # 车 1 扛起 L00278
    tbl = rcs_pods.payload()["pods"]
    check("取货→表里摘除", ch and not any(p["code"] == "L00278" for rows in tbl.values() for p in rows) and
          len(rsc) == 1, "BB 余 %d 条" % len(tbl.get("BB", [])))

    ch = rcs_pods.apply_car_event("L00278", "", b3[0] * 1000, b3[1] * 1000, "BB")   # 车 1 把它放到 b3
    tbl = rcs_pods.payload()["pods"]
    hit = [p for p in tbl.get("BB", []) if p["code"] == "L00278"]
    check("放货→落到最近储位节点", ch and len(hit) == 1 and
          math.hypot(hit[0]["x"] - b3[0], hit[0]["y"] - b3[1]) < 0.01, json.dumps(hit, ensure_ascii=False))
    check("放货行形状(托盘标记/空区域/储位号合成)", hit and hit[0]["pal"] is False and
          hit[0]["area"] == "" and hit[0]["pos"] ==
          "%06dBB%06d" % (round(b3[0] * 1000), round(b3[1] * 1000)), json.dumps(hit, ensure_ascii=False))

    # 一格一货架：把 L77777 放到已被 L00278 占着的 b3 → 旧记录必须被顶掉，不能两架叠一格
    ch = rcs_pods.apply_car_event("L77777", "", b3[0] * 1000, b3[1] * 1000, "BB")
    at_b3 = [p["code"] for p in rcs_pods.payload()["pods"].get("BB", [])
             if (p["x"], p["y"]) == (round(b3[0], 3), round(b3[1], 3))]
    check("落点已被占→顶掉旧记录（一格一货架）", ch and at_b3 == ["L77777"], json.dumps(at_b3))

    ch = rcs_pods.apply_car_event("P00281", "L99999",            # 车 2 换货：放下 P00281、扛起 L99999
                                  b4[0] * 1000, b4[1] * 1000, "BB")
    tbl = rcs_pods.payload()["pods"]
    codes = [p["code"] for rows in tbl.values() for p in rows]
    hit4 = [p for p in tbl.get("BB", []) if p["code"] == "P00281"]
    check("换货→放下的落表/扛走的摘除",
          ch and "L99999" not in codes and "P00281" in codes and
          math.hypot(hit4[0]["x"] - b4[0], hit4[0]["y"] - b4[1]) < 0.01,
          json.dumps(codes, ensure_ascii=False))

    ch = rcs_pods.apply_car_event("L88888", "", 999999.0, 999999.0, "BB")     # 放到没有节点的远地
    check("放货无节点→不记表，等人工校准", not ch, "changed=%s" % ch)

    # ---- 落点判据本体（合成节点，不依赖地图文件）：认可半径 / 决断余量 / 成对节点 ----
    rcs_pods._berth_nodes = lambda qr: [(0.0, 0.0), (0.0, 0.08), (0.0, 0.96)]
    check("认可半径外→弃权", rcs_pods._nearest_berth("BB", 0.0, 0.6) is None)
    check("相邻格中点→弃权（不猜隔壁格）", rcs_pods._nearest_berth("BB", 0.0, 0.48) is None)
    s = rcs_pods._nearest_berth("BB", 0.0, 0.05)
    check("成对节点算同一处（不误判歧义）",
          s is not None and math.hypot(s[0], s[1] - 0.08) < 0.05, "got %r" % (s,))
    rcs_pods._berth_nodes = real_bnodes

    check("增量变化均已落盘", os.path.isfile(rcs_pods.STORE_PATH) and
          "L77777" in json.dumps(json.load(open(rcs_pods.STORE_PATH, encoding="utf-8")), ensure_ascii=False))
    rcs_pods.load_store()                            # 坏路径/正常路径都读得回
    check("load_store 读回一致",
          json.dumps(rcs_pods.payload()["pods"], ensure_ascii=False) ==
          json.dumps(json.load(open(rcs_pods.STORE_PATH, encoding="utf-8"))["pods"], ensure_ascii=False))

    ch = rcs_pods.apply_car_event("L00278", "L00278", 0, 0, "BB")    # 同值不变不算事件
    check("podCode 未变化→无动作", not ch)
finally:
    rcs_pods.STORE_PATH = real_store
    rcs_pods.set_on_updated(real_cb)
    rcs_pods._berth_nodes = real_bnodes                # 合成节点替身务必还原
    with rcs_pods._cache_lock:                       # 还原缓存，不污染其它用例
        rcs_pods._cache.update(json.loads(real_cache))

# ---- 6. payload 快照形状（不触发网络） ----
snap = rcs_pods.payload()
check("payload 含 ts/pods/err", set(snap) == {"ts", "pods", "err"})

print()
if fails:
    print("失败: " + "；".join(fails))
    sys.exit(1)
print("货架清单离线用例全部通过 ✔")
