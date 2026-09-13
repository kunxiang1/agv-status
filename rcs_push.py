# rcs_push.py — 海康 RCS-2000 私有推送通道 (ZMTP/0MQ over TCP) 纯标准库客户端/解析器
# login():  Web 登录 = 把本机 IP 登记进 RCS 推送引擎白名单（IP 级，实测）
# iter_msgs(ip, port): 阻塞生成器，逐条产出 XML 帧 bytes；断线自动重登重连
# 解析: parse_frame(body) → REST 形状 dict 列表
# CLI:  python rcs_push.py [地图简称] → JSONL 实时流
#
# 所有 IP / 端口 / 账号 一律取自 config.py（唯一配置源），本文件不再写死任何环境值。
import socket, time, re, sys, json, binascii
import urllib.request

import config as C

RCS_ENGINE = C.RCS_ENGINE_IP
WEB = C.WEB_BASE
USER, PWD = C.RCS_USER, C.RCS_PWD
GREET_C = b"\xff\x00\x00\x00\x00\x00\x00\x00\x01\x7f"
NULL = binascii.unhexlify("03004e554c4c" + "00" * 48)      # ZMTP NULL 命令帧 54B（照抄官方逐字节）
READY_SUB = binascii.unhexlify("04190552454144590b536f636b65742d5479706500000003535542000101")


class LoginError(RuntimeError):
    """凭据/账号层失败（与网络故障区分）：退避长等，绝不快速重试（连错会锁账号）。"""


def login():
    """Web 登录，把本机 IP 登记进推送白名单。成功返回 True，凭据错误抛 LoginError。"""
    body = ("ecsUserName=%s&ecsPassword=%s&pwdSafeLevelLogin=0" %
            (USER, C.sha256_pwd())).encode()
    req = urllib.request.Request(WEB + C.LOGIN_PATH, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "Mozilla/5.0 Chrome/120"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
    try:                                        # 不再用子串匹配：解析 JSON 判别 success
        ok = bool(json.loads(raw.decode("utf-8", "replace")).get("success"))
    except ValueError:
        ok = b'"success":true' in raw           # 兜底：非 JSON 应答仍按旧规则判定
    if not ok:
        raise LoginError("RCS web login failed: %s" % raw[:120])
    return True


def _recv_exact(s, n):
    """TCP 是字节流：recv 可能短读，粘包/分片下必须循环读满 n 字节。"""
    buf = b""
    while len(buf) < n:
        b = s.recv(n - len(buf))
        if not b:
            raise ConnectionError("server closed during greeting")
        buf += b
    return buf


def _conn(ip, port):
    s = socket.create_connection((ip, port), timeout=5)
    _recv_exact(s, 10)                                     # 服务器 greeting（可能分片到达）
    s.sendall(GREET_C); time.sleep(0.05)
    s.sendall(NULL); time.sleep(0.05)
    s.sendall(READY_SUB)                                   # Socket-Type=SUB → 立即全量推
    return s


def iter_msgs(ip, port):
    """产出完整 XML 帧 bytes。无数据超时即视为白名单过期→重登重连；
    登录失败按 config.LOGIN_BACKOFF_S 退避（避免刷登录锁账号），网络故障 3s 重试。"""
    while True:
        s = None
        try:
            login(); s = _conn(ip, port); s.settimeout(C.RECV_TIMEOUT_S)
            buf = b""; pos = 0
            while True:
                b = s.recv(65536)
                if not b: raise ConnectionError("server closed")
                buf += b
                while True:
                    i = buf.find(b"<?xml", pos)
                    if i < 0:
                        buf = buf[-16:]; pos = 0; break
                    j = buf.find(b"\x00", i)
                    if j < 0:
                        buf = buf[i:]; pos = 5; break
                    yield buf[i:j]
                    pos = j + 1
        except GeneratorExit:
            if s: s.close(); return
            raise
        except LoginError as e:                             # 凭据类失败：长退避，绝不快速重试
            print("rcs_push(%s:%s): %s — %ds 后退避重试（勿缩短，连错会锁账号）"
                  % (ip, port, e, C.LOGIN_BACKOFF_S), file=sys.stderr, flush=True)
            if s:
                try: s.close()
                except OSError: pass
            time.sleep(C.LOGIN_BACKOFF_S)
        except Exception as e:
            print("rcs_push(%s:%s): %s — 3s 后重登重连" % (ip, port, e), file=sys.stderr, flush=True)
            if s:
                try: s.close()
                except OSError: pass
            time.sleep(3)


def _t(b, tag):
    if isinstance(tag, str): tag = tag.encode()
    m = re.search(tag + rb">([^<]*)", b)
    return m.group(1).decode() if m else None


def parse_frame(body):
    """XML 帧 → [{'e':kind, ...REST形状}]。ROBOT_STATUS→status, ROBOT_PATH→path, ROBOT_OFFLINE→offline, AlarmMessage→alarm"""
    tp = re.search(rb"<Type>([A-Z_]+)</Type>", body)
    tp = tp.group(1).decode() if tp else ("alarm" if b"<AlarmMessage>" in body else None)
    mm = re.search(rb"<MapCode>([A-Z]+)", body)
    mp = mm.group(1).decode() if mm else ""
    if tp == "ROBOT_STATUS":
        out = []
        # 每帧 1..N 个 Robot，Pod 是其后的兄弟节点（车载货架时才有）：用组合正则按顺序配对
        for rb, pb in re.findall(rb"<Robot>(.*?)</Robot>\s*(?:<Pod>(.*?)</Pod>)?", body, re.S):
            b = rb
            p = re.search(rb'<Pos x="(-?\d+)" y="(-?\d+)"', b)
            if not p: continue
            g = lambda k: _t(b, k)
            pb = pb or b""
            out.append({"e": "status", "a": {
                "mapCode": mp, "robotCode": g("<Id"), "robotIp": g("<IP"),
                "online": True,                       # 推送通道在推=在线（与 REST 的 online 字段同义）
                "posX": p.group(1).decode(), "posY": p.group(2).decode(),
                "robotDir": g("<Direction"), "speed": g("<Speed"), "battery": g("<Battery"),
                "status": g("<Status"), "stop": g("<Stop"), "exclType": g("<Remove"),
                "alarmMain": g("<AlarmMain"), "alarmSub": g("<AlarmSub"),
                "podCode": _t(pb, "<Id") or "", "podDir": _t(pb, "<Direction") or ""}})
        return out
    if tp == "ROBOT_PATH":
        rid = _t(body, "<RobotId")
        pts = ["[%s,%s,%s]" % (a.decode(), b.decode(), c.decode()) for a, b, c in
               re.findall(rb'<Path x="(-?\d+)" y="(-?\d+)" th="(-?\d+)"', body)]
        return [{"e": "path", "map": mp, "id": rid, "path": pts}]
    if tp == "ROBOT_OFFLINE":
        ids = re.findall(rb"<Robot>\s*<Id>([^<]*)</Id>", body)
        return [{"e": "offline", "map": mp, "ids": [x.decode() for x in ids]}]
    if tp == "alarm":
        g = lambda k: _t(body, "<" + k)
        return [{"e": "alarm", "mod": g("AlarmModule"), "mt": g("MainType"), "st": g("SubType"),
                 "src": g("AlarmSource"), "stat": g("AlarmStatus"), "guid": g("AlarmGuid"),
                 "lvl": g("AlarmLevel"), "time": g("AlarmTime"), "p1": g("AlarmParam1"),
                 "x": g("AlarmX"), "y": g("AlarmY"), "map": g("AlarmMap")}]
    return []


def event_map(o):
    """事件属于哪张图：status 在 a.mapCode，path/offline/alarm 在 map。取不到返回 ""（＝与地图无关，
    如系统级事件；货架表 pods 事件也走这条）——调用方对 "" 一律当"发给所有订阅者"。纯函数，供测试。"""
    return o.get("map") or (o.get("a") or {}).get("mapCode") or ""


if __name__ == "__main__":
    want = sys.argv[1] if len(sys.argv) > 1 else None
    for body in iter_msgs(RCS_ENGINE, C.PORT_PUSH):
        for o in parse_frame(body):
            if want and event_map(o) != want: continue
            print(json.dumps(o, ensure_ascii=False), flush=True)
