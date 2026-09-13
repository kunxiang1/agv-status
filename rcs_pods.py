# -*- coding: utf-8 -*-
"""货架↔储位表：REST 全量基线 + ZMTP 推送增量维护 + JSON 落盘。

全量基线（服务启动 / 前端「同步货架」按钮 / 同步地图顺手一次）：
  POST {WEB_BASE}/rcms/services/rest/hikRpcService/queryPodBerthAndMat
  body {"reqCode":唯一,"reqTime":"","clientCode":..,"tokenCode":"","mapShortName":"<地图简称>",
        "podCode":"","materialLot":"","positionCode":"","areaCode":""}
  -> {"code":"0","data":[{"podCode":"L00278","mapDataCode":"0004771BB0052930",
        "positionCode":"004771BB052930","areaCode":"zfh","materialLot":""}, ...]}
每条 = 一个「此刻停在储位上的货架」。mapDataCode 前 7 位 = x 毫米、第 8-9 位 = 图码、
后 7 位 = y 毫米，译出的坐标与地图节点完全重合（实测四图 400+ 个全部 0 偏差）。

增量维护（车取/放货，不重拉——6990 推送本身已带全部事实）：
  推送里某车 podCode 变化 = 一次取或放；变化须连续 _POD_STABLE_FRAMES 帧稳定才认（防推送抖动），
  且落点用「变化前一帧」的坐标（那帧车还扛着货架、正停在放货位；变化帧的车可能已经在路上）：
    · 出现 podCode（取货）→ 把该货架从表里摘掉（此刻它在车上，前端画在车身上）；
    · podCode 消失（放货）→ 认可半径内取最近候选节点（路径点不算候选），
      并要求它比「下一个不同位置」近出决断余量；定不下来就不记（宁缺勿错，靠「同步货架」兜底）。
  全量 REST 只在三处发生：服务启动、前端「同步货架」按钮、同步地图时顺手一次——不做周期校准
  （用户口径：人多时周期请求会平白加重 RCS 负担，而增量维护理论上不会错）。

落盘（用户口径：表很小，直接存 JSON）：
  pods_store.json 与源码同目录，每次变化原子改写。启动先读它（秒出画面），
  随后第一次全量重拉立即用 RCS 真值覆盖——盘上副本只是缓存，RCS 才是数据源。
  注意：这是运行时文件，不进发布仓（sanitize_for_publish 的同步清单里不要带上它）。

地图简称不写死（现场每张图随手填，可能是汉字也可能是编码）：
  启动时从 CMS「地图配置」接口动态发现 —— POST /web/elcMap/findElcMapListByOrgCode.action，
  每行带 shortName；orgCode 从 "1" 起步，接口对错误值会回 nextOrgCode 提示，跟着走即可。

架构约定：
  - HTTP 通道仅本模块与 rcs_push 的 CMS 登录两处；socket 仍只有 rcs_push 建。
  - reqCode 是防重键（重复返回「请求编号已存在」），每次请求必须唯一。
  - 本机 IP 必须在 RCS「系统配置→服务配置→允许配置IPs」里，否则 code=5。
"""
import http.cookiejar
import json
import math
import os
import re
import sys
import threading
import time
import urllib.request

import config as C

# mapDataCode：7位x毫米 + 2位图码 + 7位y毫米（例 0004771BB0052930）
_MDC = re.compile(r"^(\d{7})([A-Z]{2})(\d{7})$")

_BASE = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(_BASE, "pods_store.json")   # 落盘位置（测试可替换）

# 放货落点判据（实测四图 420 个在库货架得出，改之前先重跑 _dev/_pods_test.py 的量测）：
#   · 储位最近邻间距中位 0.96m（最密 0.08m，成对节点视为同一位置）；
#   · 车停准后坐标偏差约 0.03m，取 0.5m 认可半径＝十几倍余量；
#   · 间距不到两倍半径时，位置数据分不清是哪一格 → 弃权不记（人工「同步货架」兜底），
#     宁可不显示也不能显示错位置。
_PLACE_MAX_D = 0.5               # 落点认可半径(m)：车坐标到候选节点的最大距离
_PLACE_MIN_MARGIN = 0.25         # 决断余量(m)：最近点须比"下一个不同位置"至少近这么多
_PLACE_SAME_SPOT = 0.3           # 相距 <该值(m) 的节点视为同一位置（实测有成对 0.08m 的节点）
_PATH_TYPE = 16                  # 路径点=路网上的点，货架不会停在这（实测 420 条落点无一在 16 类）
_POD_STABLE_FRAMES = 2           # podCode 变化须连续这么多帧稳定才认（防推送瞬时抖动被当成一次取放）
_SKIP_LOG_MIN_S = 60             # 同一个货架「落点定不下来」告警的最小间隔秒（防刷屏）

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 内网直连，绕 HTTP_PROXY

_cache_lock = threading.Lock()
_cache = {"ts": 0.0, "pods": {}, "err": {}}       # pods: mapCode -> [row]; err: mapCode -> 消息
_last_err_sig = ""                                # 只在错误集合变化时打日志，避免刷屏

_pull_lock = threading.Lock()                     # 串行化全量拉取：定时校准与手动同步并发时排队
_on_updated = None                                # 表变化后的回调（server 用来 SSE 广播）
_store_lock = threading.Lock()

_discover_lock = threading.Lock()
_short_names = {"map": {}, "at": 0.0}             # 地图code -> 对外简称（动态发现，不写盘）
_DISCOVER_MIN_S = 600                             # 简称重查限频：简称几乎不变，无需频繁打扰 CMS
_DISCOVER_MAX_HOP = 6                             # nextOrgCode 提示最多走 6 跳（防环）

_map_nodes = {}                                   # qr -> (mtime, [(x,y)...]) 地图节点缓存（按 mtime 失效）
_car_state = {}                                   # rid -> {pod, prev, pend}：见 note_car_frame（帧稳定判定）
_skip_log = {}                                    # pod -> 上次「落点定不下来」告警时刻（限频，防刷屏）
_seq = [0]


def decode_berth_xy(map_data_code):
    """mapDataCode -> (x, y) 米；格式不符返回 None。纯函数，供测试。"""
    m = _MDC.match(map_data_code or "")
    if not m:
        return None
    return int(m.group(1)) / 1000.0, int(m.group(3)) / 1000.0


def parse_short_names(rows):
    """CMS 地图列表行 -> {code: shortName}（缺简称回退 code）。纯函数，供测试。"""
    out = {}
    for r in rows or []:
        code = (r.get("code") or "").strip()
        if code:
            out[code] = (r.get("shortName") or "").strip() or code
    return out


def _reqcode(tag):
    _seq[0] += 1
    return "agvweb-%s-%d-%d" % (tag, int(time.time() * 1000), _seq[0])


def discover_short_names(force=False):
    """从 CMS 查「地图简称」。启动查一次；之后仅在简称失效时限频重查。失败回退用图码。"""
    with _discover_lock:
        if not force and _short_names["map"] and \
                time.time() - _short_names["at"] < _DISCOVER_MIN_S:
            return _short_names["map"]
        try:
            cj = http.cookiejar.CookieJar()
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                             urllib.request.HTTPCookieProcessor(cj))
            op.addheaders = [("User-Agent", "Mozilla/5.0 Chrome/120")]
            login = ("ecsUserName=%s&ecsPassword=%s&pwdSafeLevelLogin=0" %
                     (C.RCS_USER, C.sha256_pwd())).encode()
            r = urllib.request.Request(C.WEB_BASE + C.LOGIN_PATH, data=login, method="POST")
            r.add_header("Content-Type", "application/x-www-form-urlencoded")
            resp = json.loads(op.open(r, timeout=10).read().decode("utf-8", "replace"))
            if not resp.get("success"):
                raise RuntimeError("CMS 登录失败")
            rows, cand, hops = [], "1", 0            # 组织树根从 "1" 起；接口对错误值回 nextOrgCode 提示
            while cand and hops < _DISCOVER_MAX_HOP:
                hops += 1
                r2 = urllib.request.Request(
                    C.WEB_BASE + "/rcms/web/elcMap/findElcMapListByOrgCode.action",
                    data=("orgCode=%s" % cand).encode(), method="POST")
                r2.add_header("Content-Type", "application/x-www-form-urlencoded")
                js = json.loads(op.open(r2, timeout=10).read().decode("utf-8", "replace"))
                if js.get("success") and js.get("rows"):
                    rows = js["rows"]
                    break
                cand = js.get("nextOrgCode")         # 服务器提示的下一个候选组织码
            if not rows:
                raise RuntimeError("组织树 %d 跳内未找到地图列表" % hops)
            names = parse_short_names(rows)
            _short_names["map"] = names
            _short_names["at"] = time.time()
            print("rcs_pods: 地图简称已发现 %s" % names, flush=True)
            return names
        except Exception as e:
            print("rcs_pods: 简称发现失败(%s)，暂用图码充当简称" % e, file=sys.stderr, flush=True)
            _short_names["at"] = time.time()         # 失败也记时刻，按限频重试，不打爆 CMS
            return _short_names["map"]


def _short_name(map_code):
    """图码 -> 对外简称；未发现/未收录时回退图码（BB/DD 这类简称本就是图码的图不受影响）。"""
    return _short_names["map"].get(map_code, map_code)


def fetch_pods_for_map(map_code, timeout=15):
    """按图拉货架清单（原始行）。抛异常/非 0 code 都向上抛，由 refresh 统一记错。"""
    body = {"reqCode": _reqcode("pod"), "reqTime": "", "clientCode": "agvweb",
            "tokenCode": "", "mapShortName": _short_name(map_code),
            "podCode": "", "materialLot": "", "positionCode": "", "areaCode": ""}
    req = urllib.request.Request(
        "%s/rcms/services/rest/hikRpcService/queryPodBerthAndMat" % C.WEB_BASE,
        data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    with opener.open(req, timeout=timeout) as r:
        js = json.loads(r.read().decode("utf-8"))
    if str(js.get("code")) != "0":
        raise RuntimeError("code=%s %s" % (js.get("code"), str(js.get("message", ""))[:80]))
    return js.get("data") or []


def assemble(map_code, rows):
    """原始行 -> 前端用的紧凑列表 [{code,x,y,pal,area,pos}]。译不出坐标的行丢弃并计数。纯函数，供测试。"""
    out, bad = [], 0
    for r in rows:
        xy = decode_berth_xy(r.get("mapDataCode"))
        if not xy:
            bad += 1
            continue
        code = r.get("podCode") or ""
        out.append({"code": code,
                    "x": round(xy[0], 3), "y": round(xy[1], 3),
                    "pal": code.startswith("P"),          # P 前缀=托盘（提示里区分，画法与货架一致）
                    "area": r.get("areaCode") or "",
                    "pos": r.get("positionCode") or ""})
    if bad:
        print("rcs_pods: %s 有 %d 行译不出坐标(已跳过)" % (map_code, bad), file=sys.stderr, flush=True)
    return out


# ---------------- 落盘 ----------------

def _save_store():
    """当前缓存快照原子写盘（体积 ~40KB，取/放货频率低，直接同步写）。"""
    with _cache_lock:
        snap = {"ts": _cache["ts"], "pods": {k: list(v) for k, v in _cache["pods"].items()}}
    try:
        with _store_lock:
            tmp = STORE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False)
            os.replace(tmp, STORE_PATH)
    except OSError as e:
        print("rcs_pods: 落盘失败(%s): %s" % (STORE_PATH, e), file=sys.stderr, flush=True)


def load_store():
    """启动先读盘上副本秒出画面；随后的第一次全量重拉会用 RCS 真值覆盖。坏文件直接弃用。"""
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        pods = snap.get("pods") or {}
        if not isinstance(pods, dict):
            raise ValueError("pods 不是字典")
        with _cache_lock:
            _cache["ts"] = float(snap.get("ts") or 0)
            _cache["pods"] = pods
        total = sum(len(v) for v in pods.values())
        if total:
            print("rcs_pods: 已从盘上副本恢复 %d 条（将随即被全量重拉校准）" % total, flush=True)
    except FileNotFoundError:
        pass
    except Exception as e:
        print("rcs_pods: 盘上副本不可用(%s)，等全量重拉" % e, file=sys.stderr, flush=True)


# ---------------- 全量基线 ----------------

def refresh():
    """逐张配置图拉全量并原子替换缓存（启动 / 周期校准 / 手动同步地图）。简称失效时自动重发现一次。"""
    global _last_err_sig
    with _pull_lock:                                 # 并发触发时排队串行，不多打 RCS
        pods, errs = {}, {}
        for mp in C.MAP_CODES:
            try:
                pods[mp] = assemble(mp, fetch_pods_for_map(mp))
            except Exception as e:
                msg = str(e)
                if "不存在该地图简称" in msg:         # 现场改了简称 → 限频重发现后本图重试一次
                    discover_short_names(force=True)
                    try:
                        pods[mp] = assemble(mp, fetch_pods_for_map(mp))
                        continue
                    except Exception as e2:
                        msg = str(e2)
                errs[mp] = msg
        sig = json.dumps(errs, ensure_ascii=False, sort_keys=True)
        if sig != _last_err_sig:
            for mp, msg in errs.items():
                print("rcs_pods: %s 拉取失败: %s" % (mp, msg), file=sys.stderr, flush=True)
            if not errs and _last_err_sig:
                print("rcs_pods: 之前失败的地图已恢复", flush=True)
            _last_err_sig = sig
        with _cache_lock:
            _cache["ts"] = time.time()
            _cache["pods"] = pods
            _cache["err"] = errs
        total = sum(len(v) for v in pods.values())
        print("rcs_pods: 全量校准完成 货架 %d 个（%s）" % (
            total, " ".join("%s:%d" % (k, len(v)) for k, v in pods.items())), flush=True)
    _save_store()
    cb = _on_updated
    if cb:
        try:
            cb()
        except Exception as e:
            print("rcs_pods: on_updated 回调异常: %s" % e, file=sys.stderr, flush=True)


# ---------------- 增量维护（车取/放货，不重拉） ----------------

def _berth_nodes(qr):
    """放货落点候选节点 [(x,y)]：地图全部节点去掉路径点（路网上的点，货架不会停在这）。
    maps/<qr>.json 由地图同步产出；缺文件/坏文件返回空表（落点放弃，等人工校准）。"""
    fp = os.path.join(_BASE, "maps", qr + ".json")
    try:
        mt = os.path.getmtime(fp)
    except OSError:
        return []
    hit = _map_nodes.get(qr)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with open(fp, encoding="utf-8") as f:
            nodes = [(n[1], n[2]) for n in json.load(f).get("nodes", []) if n[3] != _PATH_TYPE]
    except Exception:
        nodes = []
    _map_nodes[qr] = (mt, nodes)
    return nodes


def _nearest_berth(qr, x_m, y_m):
    """车坐标 -> 落点节点 (x,y)。返回 None = 不记这一格（宁可暂时空白，也不显示错位置）：
      · 附近没有候选节点（最近点 > _PLACE_MAX_D）；
      · 最近点与「下一个不同位置」差不到 _PLACE_MIN_MARGIN —— 位置数据分不清是哪一格；
        彼此相距 <_PLACE_SAME_SPOT 的节点算同一处（地图里存在成对节点），不计入歧义。
    """
    cands = [(nx, ny, math.hypot(nx - x_m, ny - y_m)) for nx, ny in _berth_nodes(qr)]
    if not cands:
        return None
    near = min(cands, key=lambda t: t[2])
    if near[2] > _PLACE_MAX_D:
        return None
    other = min((d for nx, ny, d in cands
                 if math.hypot(nx - near[0], ny - near[1]) > _PLACE_SAME_SPOT), default=1e9)
    if other - near[2] < _PLACE_MIN_MARGIN:
        return None
    return near[0], near[1]


def _mm(v):
    try:
        return float(v) / 1000.0
    except (TypeError, ValueError):
        return None


def _why_none(qr, x_m, y_m):
    """落点失败的可诊断原因（只在失败时调用，多扫一遍节点无所谓）。"""
    cands = [(nx, ny, math.hypot(nx - x_m, ny - y_m)) for nx, ny in _berth_nodes(qr)]
    if not cands:
        return "%s 图没有候选节点（maps/%s.json 缺失或全是路径点）" % (qr, qr)
    near = min(cands, key=lambda t: t[2])
    other = min((d for nx, ny, d in cands
                 if math.hypot(nx - near[0], ny - near[1]) > _PLACE_SAME_SPOT), default=1e9)
    if near[2] > _PLACE_MAX_D:
        return "最近候选 %.2fm > 认可半径 %.2fm" % (near[2], _PLACE_MAX_D)
    return ("最近候选 %.2fm、下一个不同位置 %.2fm（只近 %.2fm < 决断余量 %.2fm，分不清是哪一格）"
            % (near[2], other, other - near[2], _PLACE_MIN_MARGIN))


def note_car_frame(rid, pod, x_mm, y_mm, map_code):
    """每帧喂一台车的 podCode 与坐标，返回「稳定变化」事件 (prev_pod, new_pod, x_mm, y_mm, map_code)，否则 None。

    两件事一起做（都是修 L00342 那种"老是报落点定不下来"的根因）：
      1) 抖动过滤：podCode 变化须连续 _POD_STABLE_FRAMES 帧稳定才认——推送偶发瞬时置空
         （空↔有）不再被当成一次"取货+放货"；
      2) 落点锚点：事件里的坐标取「变化前一帧」（那时车还扛着货架、正停在放/取货位），
         而不是变化那一帧——RCS 清 podCode 常常晚于物理放货，变化帧的车可能已经在路上，
         用它定落点当然定不准（这也正是弃权告警反复出现的原因）。
    纯状态机，可在测试里离线驱动。
    """
    st = _car_state.get(rid)
    if st is None:                                       # 首帧只登记（此刻不知道它是否刚取放货）
        _car_state[rid] = {"pod": pod, "prev": (x_mm, y_mm, map_code), "pend": None}
        return None
    ev = None
    if pod != st["pod"]:
        p = st["pend"]
        if p and p["pod"] == pod:
            p["n"] += 1
            if p["n"] >= _POD_STABLE_FRAMES:              # 连续稳定 → 认这次变化
                ev = (st["pod"], pod, p["x"], p["y"], p["map"])
                st["pod"], st["pend"] = pod, None
        else:                                            # 新变化：锚点=变化前一帧的坐标/图
            px, py, pm = st["prev"]
            st["pend"] = {"pod": pod, "n": 1, "x": px, "y": py, "map": pm}
    else:
        st["pend"] = None                                # 回到已确认值：抖动，丢弃待定
    st["prev"] = (x_mm, y_mm, map_code)
    return ev


def apply_car_event(prev_pod, new_pod, x_mm, y_mm, map_code):
    """推送里某车 podCode 变化 → 增量维护表并广播。返回表是否变化。事件由 note_car_frame 产出
    （已做帧稳定过滤；坐标是「变化前一帧」＝车还扛着货架、停在放/取货位那一帧）。

    · new_pod 非空（取货）：把它从任何图的储位上摘掉——此刻它被车扛着，前端画在车身上；
    · prev_pod 非空且 ≠ new_pod（放货）：先摘掉可能的残留记录，再按车坐标定落点——
      认可半径 _PLACE_MAX_D 内取最近节点，且该点必须比「下一个不同位置」近出 _PLACE_MIN_MARGIN
      （储位间距实测中位 0.96m；差得不多说明位置数据分不清是哪一格）；定不下来就不记，
      由人工「同步货架」给真值——宁可暂时空白，也不能把货架画到隔壁格去。
    """
    prev_pod, new_pod = (prev_pod or "").strip(), (new_pod or "").strip()
    if prev_pod == new_pod:
        return False
    changed = False
    with _cache_lock:
        if new_pod:                                  # 取货（或换货时新扛上的）：从表里摘掉
            for rows in _cache["pods"].values():
                n = len(rows)
                rows[:] = [p for p in rows if p["code"] != new_pod]
                changed = changed or len(rows) != n
        if prev_pod and prev_pod != new_pod:         # 放货：摘残留 → 落到最近储位节点
            for rows in _cache["pods"].values():
                n = len(rows)
                rows[:] = [p for p in rows if p["code"] != prev_pod]
                changed = changed or len(rows) != n
            x_m, y_m = _mm(x_mm), _mm(y_mm)
            spot = _nearest_berth(map_code, x_m, y_m) if (map_code and x_m is not None and y_m is not None) else None
            if spot:
                bx, by = round(spot[0], 3), round(spot[1], 3)
                rows = _cache["pods"].setdefault(map_code, [])
                n0 = len(rows)
                rows[:] = [p for p in rows if (p["x"], p["y"]) != (bx, by)]   # 一格一货架（实测无重复坐标）
                changed = changed or len(rows) != n0
                # 储位号：现场 415/420 条＝坐标按地码规则合成（6位x毫米+图码+6位y毫米），与地图节点逐位对上；
                # 余下 5 条是具名岗位（PL_000_05_QFC / 20_3 这类），无法由坐标推出——此处按坐标写法给，
                # 与前端 hover 的兜底算法一致；真值由全量校准覆盖（它自带 positionCode）。
                rows.append({"code": prev_pod, "x": bx, "y": by,
                             "pal": prev_pod.startswith("P"), "area": "",
                             "pos": "%06d%s%06d" % (round(bx * 1000), map_code, round(by * 1000))})
                changed = True
            else:
                now = time.time()
                if now - _skip_log.get(prev_pod, 0) >= _SKIP_LOG_MIN_S:   # 同一货架限频，别刷屏
                    _skip_log[prev_pod] = now
                    print("rcs_pods: %s 放货落点定不下来——%s；位置 (%.2f, %.2f) 取自变化前一帧；"
                          "先不记，需要时点「同步货架」校准"
                          % (prev_pod, _why_none(map_code, x_m, y_m) if (map_code and x_m is not None)
                             else "缺少坐标或地图码", x_m or 0, y_m or 0),
                          file=sys.stderr, flush=True)
    if changed:
        _save_store()
        cb = _on_updated
        if cb:
            try:
                cb()
            except Exception as e:
                print("rcs_pods: on_updated 回调异常: %s" % e, file=sys.stderr, flush=True)
    return changed


def set_on_updated(cb):
    """注册表变化回调（server 用它把新表 SSE 广播给所有浏览器）。"""
    global _on_updated
    _on_updated = cb


def payload():
    """/api/pods 与 SSE 广播的应答体（缓存快照，绝不包含任何凭据——本来也没有）。"""
    with _cache_lock:
        return {"ts": _cache["ts"], "pods": _cache["pods"], "err": _cache["err"]}


def pods_loop():
    """启动一次性：读盘上副本秒出画面 → 发现简称 → 全量重拉校准一次。
    之后不再周期打扰 RCS：取/放货靠推送增量维护（理论上不会错），人工校准走「同步货架」按钮。"""
    load_store()
    discover_short_names()
    try:
        refresh()
    except Exception as e:
        print("rcs_pods: 启动校准异常: %s" % e, file=sys.stderr, flush=True)
