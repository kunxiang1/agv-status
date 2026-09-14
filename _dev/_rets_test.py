# -*- coding: utf-8 -*-
"""地图背景（rcs_rets）离线用例：XML 解析 + 空表保护 + 产物形状。

不连 RCS（解析器是纯函数；落盘保护用假 opener 注入失败）。
现场对账（可选，需本机能连 8181）：
  python rcs_rets.py            # 重拉并打印各图 (多边形数, 标注数)
  python _dev/_rets_test.py --live    # 拉真实数据并与现有 maps/rets.json 比对（应等价）
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import rcs_rets as R                                   # noqa: E402

fails = []


def ok(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def poly(pts, area=None):
    s = "<MapRet>" + "".join('<Point xpos="%s" ypos="%s"/>' % p for p in pts)
    if area:
        s += "<Area " + area + "/>"
    return s + "</MapRet>"


# ---- 1) 多边形：单位换算 / 点数门槛 / 颜色属性顺序无关 ----
print("[1] 多边形解析")
one = R.parse_content(poly([(1500, 2500), (3500, 2500), (3500, 4500)],
                          'color_a="255" color_r="218" color_g="220" color_b="223"'))
ok(len(one["polys"]) == 1, "3 点应产出 1 个多边形")
ok(one["polys"][0]["pts"] == [[1.5, 2.5], [3.5, 2.5], [3.5, 4.5]],
   "毫米→米换算应为 x/1000（实际 %r）" % (one["polys"][0]["pts"],))
ok(one["polys"][0]["color"] == "#dadcdf", "官方灰 a,r,g,b 顺序应解析为 #dadcdf")

# 属性顺序被打乱也必须解析出同一颜色（旧实现用固定顺序正则会静默退回兜底灰）
scrambled = R.parse_content(poly([(0, 0), (1000, 0), (0, 1000)],
                                 'color_r="10" color_g="20" color_b="30" color_a="255"'))
ok(scrambled["polys"][0]["color"] == "#0a141e",
   "颜色属性乱序仍须解析正确（实际 %s）" % scrambled["polys"][0]["color"])

ok(R.parse_content(poly([(0, 0), (1000, 0)]))["polys"] == [], "少于 3 点必须丢弃")
ok(R.parse_content(poly([(0, 0), (1000, 0), (0, 1000)]))["polys"][0]["color"] == "#dadcdf",
   "缺 <Area> 应退回兜底色而不是抛异常")

# ---- 2) 标注：矩形中心 / 竖排 / 实体反转义 / 属性乱序 / 缺属性跳过 ----
print("[2] 文字标注解析")
nm = ('<RetName start_x="100" start_y="200" end_x="900" end_y="3800" size="14" '
      'font="Microsoft YaHei" font_color_a="255" font_color_r="0" font_color_g="0" '
      'font_color_b="0">待\n曝\n光\n区</RetName>')
L = R.parse_content(nm)["labels"]
ok(len(L) == 1 and L[0]["text"] == "待\n曝\n光\n区", "竖排逐字换行必须原样保留")
ok(L[0]["x"] == 0.5 and L[0]["y"] == 2.0, "坐标为文字框中心（米）（实际 %r）" % ((L[0]["x"], L[0]["y"]),))
ok(L[0]["h"] > L[0]["bw"], "竖排名须满足 框高>>框宽（bw=%.2f h=%.2f）" % (L[0]["bw"], L[0]["h"]))
ok(L[0]["size"] == 14.0 and L[0]["font"] == "Microsoft YaHei", "字号/字体须透传")

ent = R.parse_content('<RetName start_x="0" start_y="0" end_x="1000" end_y="1000" '
                      'size="12" font_color_r="0" font_color_g="0" font_color_b="0">'
                      '开料&amp;内层前处理</RetName>')["labels"]
ok(ent and ent[0]["text"] == "开料&内层前处理", "&amp; 必须反转义（否则漏字）")

sc = R.parse_content('<RetName end_x="100" end_y="200" start_x="900" start_y="3800" size="13" '
                     'font_color_r="1" font_color_g="2" font_color_b="3">X</RetName>')["labels"]
ok(sc and sc[0]["x"] == 0.5 and sc[0]["color"] == "#010203",
   "标注属性乱序仍须解析正确（x=%.2f color=%s）" % (sc[0]["x"], sc[0]["color"]))

ok(R.parse_content('<RetName size="9">X</RetName>')["labels"] == [],
   "缺坐标属性的标注须跳过而不是整层崩掉")

# ---- 3) 落盘保护：全失败不得把已有背景抹成空 ----
print("[3] 全量失败时的空表保护")
path = R.OUT_PATH
bak = open(path, "rb").read() if os.path.exists(path) else None
try:
    with open(path, "w", encoding="utf-8") as f:     # 先埋一份"好背景"
        json.dump({"ts": 1.0, "maps": {"BB": {"polys": [{"pts": [[0, 0], [1, 0], [0, 1]],
                                                          "color": "#dadcdf"}], "labels": []}}}, f)

    class Boom:
        def open(self, *a, **k):
            raise RuntimeError("模拟每图请求失败")

    orig_login = R._login_opener
    R._login_opener = lambda *a, **k: Boom()
    try:
        res = R.pull_all()
    finally:
        R._login_opener = orig_login
    after = json.load(open(path, encoding="utf-8"))["maps"]
    ok(after.get("BB", {}).get("polys"), "四图全失败时必须保留旧背景（实际 %r）" % (sorted(after),))
    ok(res.get("BB", {}).get("polys"), "全失败时返回值也应是旧背景（而非空）")

    # 全失败且旧产物已损坏/不存在 -> 允许写空，但不能抛异常
    os.remove(path)
    R._login_opener = lambda *a, **k: Boom()
    try:
        R.pull_all()
    except Exception as e:
        ok(False, "无旧产物且全失败时不得抛异常：%r" % (e,))
    else:
        ok(json.load(open(path, encoding="utf-8"))["maps"] == {}, "无旧产物时写空表可接受")
    R._login_opener = orig_login

    # 部分成功时必须合并保留未成功图的旧数据
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ts": 1.0, "maps": {"BB": {"polys": [{"pts": [[9, 9], [9, 8], [8, 9]], "color": "#000000"}], "labels": []},
                                       "CC": {"polys": [{"pts": [[1, 1], [1, 2], [2, 1]], "color": "#000000"}], "labels": []}}}, f)

    class HalfOp:
        def open(self, req, *a, **k):
            body = json.loads(req.data.decode())
            if body["mapCode"] != "CC":
                raise RuntimeError("除 CC 外都失败")

            class Resp:
                def read(self):
                    return json.dumps({"shareInfos": [{"type": "1", "content": poly(
                        [(0, 0), (1000, 0), (0, 1000)], 'color_r="1" color_g="2" color_b="3"')}]}).encode()
            return Resp()

    R._login_opener = lambda *a, **k: HalfOp()
    try:
        R.pull_all()
    finally:
        R._login_opener = orig_login
    after = json.load(open(path, encoding="utf-8"))["maps"]
    ok(after.get("BB", {}).get("polys"), "部分成功时未成功图的旧背景必须保留（实际 %r）" % (sorted(after),))
    ok(after.get("CC", {}).get("polys") and after["CC"]["polys"][0]["color"] == "#010203",
       "成功图必须被新数据覆盖")

    # ---- 3b) 区域字典（A11 getAreaAndSecByMapCode）的独立失败保护 ----
    print("[3b] 区域字典的独立失败保护")
    orig_areas = R.pull_areas
    try:
        # 背景 XML 成功、但区域字典单独失败：已拉好的中文名字典绝不能被抹空
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": 1.0, "maps": {
                "BB": {"polys": [{"pts": [[0, 0], [1, 0], [0, 1]], "color": "#000000"}],
                       "labels": [], "areas": {"zfh": "阻焊火山灰"}},
                "CC": {"polys": [{"pts": [[0, 0], [1, 0], [0, 1]], "color": "#000000"}],
                       "labels": [], "areas": {"aa": "外光暂存区"}}}}, f)

        class OkOp:
            def open(self, req, *a, **k):
                class Resp:
                    def read(self):
                        return json.dumps({"shareInfos": [{"type": "1", "content": poly(
                            [(0, 0), (1000, 0), (0, 1000)])}]}).encode()
                return Resp()

        R._login_opener = lambda *a, **k: OkOp()
        R.pull_areas = lambda op, mc, timeout=12: (_ for _ in ()).throw(RuntimeError("A11 挂了"))
        try:
            R.pull_all()
        finally:
            R._login_opener, R.pull_areas = orig_login, orig_areas
        after = json.load(open(path, encoding="utf-8"))["maps"]
        ok(after["BB"].get("areas") == {"zfh": "阻焊火山灰"},
           "区域字典单独失败时已有的中文名必须保留（实际 %r）" % (after["BB"].get("areas"),))
        ok(after["CC"].get("areas") == {"aa": "外光暂存区"}, "同上次")

        # 旧产物条目缺 areas 键（上一版升级而来）+ 部分图失败：必须补齐键，
        # 否则前端 RETS[qr].areas 为 undefined，悬停提示退化成显示原始码
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": 1.0, "maps": {
                "BB": {"polys": [{"pts": [[0, 0], [1, 0], [0, 1]], "color": "#000000"}], "labels": []},
                "CC": {"polys": [{"pts": [[0, 0], [1, 0], [0, 1]], "color": "#000000"}], "labels": []}}}, f)
        R._login_opener = lambda *a, **k: HalfOp()
        R.pull_areas = lambda op, mc, timeout=12: {"zz": "测试区"}
        try:
            R.pull_all()
        finally:
            R._login_opener, R.pull_areas = orig_login, orig_areas
        after = json.load(open(path, encoding="utf-8"))["maps"]
        missing = [k for k, v in after.items() if "areas" not in v]
        ok(not missing, "所有图都必须带 areas 键（缺 %r 会让悬停退化成原始码）" % (missing,))
        ok(all(isinstance(v.get("areas"), dict) for v in after.values()), "areas 必须是字典")
    finally:
        R.pull_areas = orig_areas

    # ---- 3c) pull_areas 解析健壮性 ----
    print("[3c] 区域字典解析")
    class RowsOp:
        def __init__(self, rows): self.rows = rows
        def open(self, req, *a, **k):
            rows = self.rows
            class Resp:
                def read(self):
                    return json.dumps({"rows": rows}).encode()
            return Resp()

    ok(R.pull_areas(RowsOp([{"areaCode": "zhhsh", "areaName": "阻焊火山灰"},
                            {"areaCode": "aa", "areaName": "aa"}]), "CC") ==
       {"zhhsh": "阻焊火山灰", "aa": "aa"}, "正常行应译成 码→名")
    ok(R.pull_areas(RowsOp([{"areaCode": "x"}]), "CC") == {"x": "x"}, "缺 areaName 应退回显示码本身")
    ok(R.pull_areas(RowsOp([{"areaName": "无名"}]), "CC") == {}, "缺 areaCode 的行应跳过而不是抛异常")
    ok(R.pull_areas(RowsOp([{"areaCode": ""}]), "CC") == {}, "空 areaCode 不应成为字典键")
    ok(R.pull_areas(RowsOp([]), "CC") == {}, "空 rows 应返回空字典")
    ok(R.pull_areas(RowsOp(None), "CC") == {}, "rows 为 null 应返回空字典")

    # ---- 3d) 地标码→区域名（A12 mapData/findListWithPages，空储位悬停的数据源）----
    print("[3d] 地标区域表解析（A12，分页）")
    class PagesOp:
        """模拟分页：按 start/limit 返回对应切片，总计 total 行。"""
        def __init__(self, rows): self.rows = rows
        def open(self, req, *a, **k):
            import urllib.parse as _up
            body = req.data.decode() if getattr(req, "data", None) else ""
            qs = dict(_up.parse_qsl(body))
            start, limit = int(qs.get("start", 1)), int(qs.get("limit", 500))
            data = self.rows[start - 1:start - 1 + limit]
            total = len(self.rows)
            class Resp:
                def read(self):
                    return json.dumps({"total": total, "data": data}).encode()
            return Resp()

    rows_a12 = [
        {"dataName": "045132BB065293", "areaCode": "外形机台储位", "dataTyp": "1", "stgSecCode": "wxjt"},
        {"dataName": "058518BB068475", "areaCode": "", "dataTyp": "16", "stgSecCode": ""},   # 路径点：跳过
        {"dataName": "045132BB062396", "areaCode": "", "dataTyp": "1", "stgSecCode": ""},    # 储位但无区域：跳过
        {"dataName": "045132BB061426", "areaCode": "字符暂存区", "dataTyp": "0", "stgSecCode": "zfh"},
        {"dataName": "007432BB050163", "areaCode": "字符机台区", "dataTyp": "10", "stgSecCode": "字符机台"},  # 工作区：**要收**（用户 2026-09-14 报漏网）
        {"dataName": "028939BB048068", "areaCode": "", "dataTyp": "10", "stgSecCode": ""},   # 工作区但无区域：跳过
        {"dataName": "", "areaCode": "无名区", "dataTyp": "1", "stgSecCode": ""},             # 缺地标码：跳过
    ]
    sa = R.pull_slot_areas(PagesOp(rows_a12), "BB", page=2)   # page=2 逼它翻多页
    ok(sa == {"045132BB065293": "外形机台储位", "045132BB061426": "字符暂存区",
              "007432BB050163": "字符机台区"},
       "只留「带区域名的储位类(0/1)+工作区(10)」，键=地标码 值=区域名（实际 %r）" % (sa,))
    ok("058518BB068475" not in sa, "路径点(16) 无区域名，必须被过滤（否则悬停会冒出无意义条目）")
    ok(R.pull_slot_areas(PagesOp([]), "BB") == {}, "空表应返回空字典且不抛异常")

    # ---- 3d2) 区域名→库区序号（stgSec/getStgSec4Ocx，客户端「库区编辑→库区类型名称」同源）----
    print("[3d2] 库区序号表解析（getStgSec4Ocx）")
    class SecOp:
        """getStgSec4Ocx 返回的是**数组**（不是 {rows:...}）。"""
        def __init__(self, rows): self.rows = rows
        def open(self, req, *a, **k):
            data = self.rows
            class Resp:
                def read(self): return json.dumps(data).encode()
            return Resp()
    rows_sec = [
        {"areaTypText": "字符机台区", "maskNum": 109, "areaTypCode": "ZFJT", "stgSecCode": "6"},
        {"areaTypText": "沉金暂存区", "maskNum": 128, "areaTypCode": "10", "stgSecCode": "10l"},
        {"areaTypText": "", "maskNum": 999, "areaTypCode": "x"},          # 缺名：跳过
        {"areaTypText": "无序号区", "maskNum": None, "areaTypCode": "y"},  # 缺序号：跳过
    ]
    seq = R.pull_area_seq(SecOp(rows_sec), "BB")
    ok(seq == {"字符机台区": 109, "沉金暂存区": 128},
       "区域名→maskNum；缺名或缺序号的行走跳过（实际 %r）" % (seq,))
    ok(R.pull_area_seq(SecOp([]), "BB") == {}, "空表应返回空字典")

    # ---- 3e) 地标区域表 + 库区序号表的独立失败保护（与 A11 字典同一口径）----
    print("[3e] 地标区域表/库区序号表独立失败保护")
    orig_sa, orig_seq = R.pull_slot_areas, R.pull_area_seq
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": 1.0, "maps": {
                "BB": {"polys": [{"pts": [[0, 0], [1, 0], [0, 1]], "color": "#000000"}], "labels": [],
                       "areas": {"zfh": "阻焊火山灰"}, "slotAreas": {"045132BB065293": "外形机台储位"},
                       "areaSeq": {"字符机台区": 109}}}}, f)

        class OkOp2:
            def open(self, req, *a, **k):
                class Resp:
                    def read(self):
                        return json.dumps({"shareInfos": [{"type": "1", "content": poly(
                            [(0, 0), (1000, 0), (0, 1000)])}]}).encode()
                return Resp()

        R._login_opener = lambda *a, **k: OkOp2()
        R.pull_areas = lambda op, mc, timeout=12: {"zfh": "阻焊火山灰"}
        R.pull_slot_areas = lambda op, mc, timeout=20, page=500: (_ for _ in ()).throw(RuntimeError("A12 挂了"))
        R.pull_area_seq = lambda op, mc, timeout=12: (_ for _ in ()).throw(RuntimeError("4Ocx 挂了"))
        try:
            R.pull_all()
        finally:
            R._login_opener, R.pull_areas, R.pull_slot_areas, R.pull_area_seq = \
                orig_login, orig_areas, orig_sa, orig_seq
        after = json.load(open(path, encoding="utf-8"))["maps"]
        ok(after["BB"].get("slotAreas") == {"045132BB065293": "外形机台储位"},
           "地标区域表单独失败时，已有映射必须保留（否则空储位区域突然消失）（实际 %r）" % (after["BB"].get("slotAreas"),))
        ok(after["BB"].get("areaSeq") == {"字符机台区": 109},
           "库区序号表单独失败时，已有序号必须保留（实际 %r）" % (after["BB"].get("areaSeq"),))
        missing2 = [k for k, v in after.items() if "slotAreas" not in v]
        ok(not missing2, "所有图都必须带 slotAreas 键（缺 %r 会让空储位取不到区域）" % (missing2,))
        missing3 = [k for k, v in after.items() if "areaSeq" not in v]
        ok(not missing3, "所有图都必须带 areaSeq 键（缺 %r 悬停显示不了序号）" % (missing3,))
    finally:
        R.pull_slot_areas, R.pull_area_seq = orig_sa, orig_seq
finally:
    if bak is not None:
        with open(path, "wb") as f:
            f.write(bak)                              # 还原真实产物
    else:
        os.path.exists(path) and os.remove(path)

# ---- 4) 现场产物形状（有 maps/rets.json 就跑；无则跳过）----
print("[4] 现有产物形状")
if bak:
    d = json.loads(bak)
    ok(isinstance(d.get("ts"), (int, float)), "产物须带 ts")
    maps = d.get("maps") or {}
    ok(bool(maps), "产物须含至少一张图（实际 %r）" % (sorted(maps),))
    bad = []
    for qr, v in maps.items():
        for p in v.get("polys") or []:
            if len(p.get("pts") or []) < 3:
                bad.append("%s:多边形点数不足" % qr)
            if not (p.get("color") or "").startswith("#"):
                bad.append("%s:颜色非法" % qr)
        for L in v.get("labels") or []:
            if "x" not in L or "y" not in L:
                bad.append("%s:标注缺坐标" % qr)
        if "areas" not in v:
            bad.append("%s:缺 areas 键（悬停会退化成原始码）" % qr)
        elif not isinstance(v["areas"], dict):
            bad.append("%s:areas 不是字典" % qr)
        if "slotAreas" not in v:
            bad.append("%s:缺 slotAreas 键（空储位取不到区域）" % qr)
        elif not isinstance(v["slotAreas"], dict):
            bad.append("%s:slotAreas 不是字典" % qr)
        else:
            legacy = 0                                 # 官方偶有「1+7位x+图码+7位y」16 位老式码（DD 实测 1 条），
            for k, nm in v["slotAreas"].items():       # 前端 cellCode() 只产 14 位、永远匹配不上 → 无害，容忍
                if not nm:
                    bad.append("%s:slotAreas 条目空值 %r" % (qr, k)); break
                if len(k) == 16 and k[7:9] == qr:      # 16 位老式码（1+7位x+图码+7位y）
                    legacy += 1; continue
                if len(k) != 14 or k[6:8] != qr:       # 常规：6位x毫米+图码+6位y毫米 = 14 字符
                    bad.append("%s:slotAreas 条目非法 %r→%r" % (qr, k, nm)); break
        if "areaSeq" not in v:
            bad.append("%s:缺 areaSeq 键（库区序号＝区域可靠标识，rcs 侧判据）" % qr)
        elif not isinstance(v["areaSeq"], dict):
            bad.append("%s:areaSeq 不是字典" % qr)
        else:
            for nm, sq in v["areaSeq"].items():        # 键=区域名，值=库区序号（整数）
                if not nm or isinstance(sq, bool) or not isinstance(sq, (int, float)):
                    bad.append("%s:areaSeq 条目非法 %r→%r" % (qr, nm, sq)); break
    ok(not bad, "现有产物结构必须合法（%s）" % ("; ".join(bad[:3]) or "OK"))
else:
    print("  SKIP 无 maps/rets.json（未同步过地图）")

# ---- 4b) 防回归：绝不在服务启动时拉（低频数据，只该由「同步地图」触发）----
print("[4b] 启动链不得联系 RCS（低频数据只在同步地图时拉）")
try:
    srv = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
    main_part = srv.split('if __name__ == "__main__":')[-1]
    ok("rcs_rets" not in main_part,
       "启动段（__main__）不得出现 rcs_rets：背景/区域名只在 syncMaps 里拉，"
       "否则每次开机都白登一次 CMS")
    ok("pull_all" not in main_part, "启动段不得调用 pull_all")
    # 唯一允许的调用点就是 syncMaps 分支里那一处
    ok(srv.count("rcs_rets.pull_all()") == 1,
       "pull_all 全项目只能有一处调用（在 /api/syncMaps 内），实际 %d 处"
       % srv.count("rcs_rets.pull_all()"))
    ok("rcs_rets.pull_all()" in srv, "syncMaps 里必须保留 pull_all（同步地图要顺手刷新）")
    idx_sync = srv.find("rcs_rets.pull_all()")
    idx_send = srv.find("return self._send(200, json.dumps(r", idx_sync)
    ok(idx_sync > 0 and 0 < idx_send and idx_sync < idx_send,
       "pull_all 必须在 syncMaps 回包之前同步跑完（否则前端 loadRets 会读到旧产物）")
except Exception as e:
    ok(False, "启动链检查读 server.py 失败：%r" % (e,))

# 前端：启动只读本机静态文件，不得在启动路径请求会联系 RCS 的端点
try:
    idx = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    ok('"maps/rets.json?_="+Date.now()' in idx or "maps/rets.json?_=" in idx,
       "loadRets 的 cache-buster 必须是真时间戳（固定 ?_=1 是死参数）")
    ok(idx.count("loadRets()") >= 2, "loadRets 应有启动与同步地图后两处调用")
except Exception as e:
    ok(False, "前端检查读 index.html 失败：%r" % (e,))

# ---- 5) 可选：真实数据对账 ----
if "--live" in sys.argv:
    print("[5] 现场对账（重拉并比对现有产物）")
    old = json.loads(bak)["maps"] if bak else {}
    try:
        new = R.pull_all()
    except Exception as e:
        print("  SKIP 无法连接现场（%s）" % e)
    else:
        same = json.dumps({k: old.get(k) for k in new}, sort_keys=True, ensure_ascii=False) == \
            json.dumps({k: new[k] for k in new}, sort_keys=True, ensure_ascii=False)
        ok(same, "重拉结果应与现有产物等价（否则解析逻辑有变动）")

print("")
if fails:
    print("RETS FAIL：%d 项未通过" % len(fails))
    sys.exit(1)
print("RETS PASS：地图背景解析与落盘保护全部通过")
