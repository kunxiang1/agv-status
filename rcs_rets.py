# -*- coding: utf-8 -*-
"""地图背景（区域多边形+文字标注，官方客户端 MapBK/Background_ 的数据源）。

来源：POST {WEB_BASE}/rcms/services/rest/clientService/getShareMapInfoByMapCode
      {"mapCode":"BB"} -> shareInfos:[{type:"1", content:<MapRetCfg XML>}]
      XML 单位毫米；<MapRet>=多边形+<Area>填充色，<RetName>=文字标注（矩形区域+字号+颜色）。
      登录 Cookie 即可，无 IP 白名单（2026-09-13 实测四图全有数据）。
只在「同步地图」时拉（背景随布局改版才动），产物 maps/rets.json 给前端叠底图。
"""
import html, json, os, re, sys, time
import urllib.request, http.cookiejar

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as C

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, "maps", "rets.json")
API = C.WEB_BASE + "/rcms/services/rest/clientService/getShareMapInfoByMapCode"

_PT = re.compile(r'<Point xpos="(-?[\d.]+)" ypos="(-?[\d.]+)"/>')
# 颜色属性一律按「属性字典」解析而非固定顺序正则：官方若调换 color_r/g/b/a 次序，
# 固定顺序正则会静默匹配失败并退回默认灰（恰是同一个灰，坏了也看不出来）。
_CARP = re.compile(r'<Area([^/>]*)/>')
_ARN = re.compile(r'(\w+)="([^"]*)"')
_NM = re.compile(r'<RetName ([^>]*)>(.*?)</RetName>', re.S)


def _rgb(a, r, g, b):
    return "#%02x%02x%02x" % (int(r), int(g), int(b))


def _attrs(s):
    return dict(_ARN.findall(s))


def _color_of(ad, prefix=""):
    """从属性字典取 (r,g,b) 转 #rrggbb；缺失返回 None 让调用方用兜底色。"""
    try:
        return _rgb(ad.get(prefix + "a", 255),
                    ad.get(prefix + "r"), ad.get(prefix + "g"), ad.get(prefix + "b"))
    except (TypeError, ValueError):
        return None


def parse_content(xml):
    """MapRetCfg XML -> {polys:[{pts:[[x,y]..米],color:"#rrggbb"},..],
                        labels:[{text,x,y,size,color}]（x,y=文字区中心，米）}。纯函数。"""
    polys, labels = [], []
    for m in re.finditer(r"<MapRet>(.*?)</MapRet>", xml, re.S):
        b = m.group(1)
        pts = [[round(float(x) / 1000, 3), round(float(y) / 1000, 3)]
               for x, y in _PT.findall(b)]
        am = _CARP.search(b)
        col = _color_of(_attrs(am.group(1)), "color_") if am else None
        if len(pts) >= 3:
            polys.append({"pts": pts, "color": col or "#dadcdf"})
    for am, txt in _NM.findall(xml):
        ad = _attrs(am)
        try:
            sx, sy = float(ad["start_x"]), float(ad["start_y"])
            ex, ey = float(ad["end_x"]), float(ad["end_y"])
        except (KeyError, ValueError):
            continue                              # 属性改名/缺失：跳过该条而不是整层崩掉
        # 官方 XML 里 & 写作 &amp;（如"开料&内层前处理"），必须反转义否则原样漏字
        labels.append({"text": html.unescape(txt).strip(),
                       "x": round((sx + ex) / 2000, 2),
                       "y": round((sy + ey) / 2000, 2),
                       "bw": abs(ex - sx) / 1000,            # 文字框宽/高（米）：
                       "h": abs(ey - sy) / 1000,             # 高>>宽 = 竖排（官方逐字换行）
                       "size": float(ad.get("size") or 12),
                       "font": ad.get("font") or "Microsoft YaHei",
                       "color": _color_of(ad, "font_color_") or "#000000"})
    return {"polys": polys, "labels": labels}
    return {"polys": polys, "labels": labels}


def _login_opener(timeout=10):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                     urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0 Chrome/120")]
    lg = ("ecsUserName=%s&ecsPassword=%s&pwdSafeLevelLogin=0" % (C.RCS_USER, C.sha256_pwd())).encode()
    r = urllib.request.Request(C.WEB_BASE + C.LOGIN_PATH, data=lg, method="POST")
    r.add_header("Content-Type", "application/x-www-form-urlencoded")
    if not json.loads(op.open(r, timeout=timeout).read().decode("utf-8")).get("success"):
        raise RuntimeError("CMS 登录失败")
    return op


def pull_all(maps=None, timeout=15):
    """拉全部图的背景并写 maps/rets.json。返回 {qr: {polys,labels}}；单图失败跳过该图。"""
    op = _login_opener()
    out = {}
    for mc in maps or C.MAP_CODES:
        try:
            req = urllib.request.Request(API, data=json.dumps({"mapCode": mc}).encode(),
                                         method="POST")
            req.add_header("Content-Type", "application/json")
            js = json.loads(op.open(req, timeout=timeout).read().decode("utf-8"))
            infos = js.get("shareInfos") or []
            merged = {"polys": [], "labels": []}
            for si in infos:
                if str(si.get("type")) != "1":     # 1=区域背景；其它类型未见于现场
                    continue
                one = parse_content(si.get("content") or "")
                merged["polys"] += one["polys"]
                merged["labels"] += one["labels"]
            out[mc] = merged
        except Exception as e:
            print("rcs_rets: %s 拉取失败(%s)" % (mc, str(e)[:70]), file=sys.stderr, flush=True)
    try:                                          # 旧产物：部分失败时保留旧图数据，别让背景整层消失
        old = (json.load(open(OUT_PATH, encoding="utf-8")).get("maps") or {}) if os.path.exists(OUT_PATH) else {}
    except Exception:
        old = {}
    if not out:
        # 一张都没拉到（8181 瞬时抽风/接口 500）：绝不能落盘空表——那会把好背景整层抹掉，
        # 且要到下次「同步地图」才可能恢复。直接沿用旧文件，只在真的没有旧数据时才写空。
        if old:
            print("rcs_rets: 本轮全部图拉取失败，保留旧背景（%s）" % sorted(old), file=sys.stderr, flush=True)
            return old
        out = {}
    else:
        old.update(out)                           # 成功图覆盖旧值；失败图沿用旧值（不删已有背景）
        out = old
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "maps": out}, f, ensure_ascii=False)
    os.replace(tmp, OUT_PATH)
    print("rcs_rets: 背景已更新 %s" % {k: (len(v["polys"]), len(v["labels"])) for k, v in out.items()},
          flush=True)
    return out


if __name__ == "__main__":
    pull_all()
