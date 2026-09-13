# AGV web monitor — stdlib only. SSE push from RCS private channels (6990 数据/8790 告警)
# + static files + map sync (8181 CMS)。所有环境值取自 config.py（唯一配置源）。
#
# 本进程的角色是「ZMTP 代理」：
#   RCS ──ZMTP(6990/8790)──> 本进程(全厂唯一一条订阅) ──HTTP/SSE(8899)──> 其它主机的浏览器
# 其它主机只访问本进程的端口即可，既不接触 RCS 的 6990/8790，也不需要被 RCS 登记白名单
# （白名单是按"发起登录的那台机器 IP"记录的，所以只有本机需要能连 RCS）。
import base64, gzip, hmac, json, os, queue, socket, sys, threading, time
import urllib.request, urllib.parse
import rcs_push
import rcs_pods
import config as C
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))


class _AsyncWriter:
    """把 stdout/stderr 变成非阻塞写：只入有界队列，由后台线程真正写出去。

    为什么需要：Windows 控制台开着「快速编辑模式」时，**用鼠标选中文字会让进程挂起**
    （不是 Python 的问题）。本服务每帧都会打印（浏览器接入/断开、货架表变化），
    一旦挂起，订阅线程与 SSE 线程就被 print 拖住 → 前端集体卡在"连接中"、日志停住，
    按一下回车才恢复。换成异步写之后，控制台冻结最多丢几行日志，服务照常推送。
    """

    def __init__(self, raw, name, maxlen=4000):
        self.raw, self.name = raw, name
        self.q = queue.Queue(maxlen)
        self.dropped = 0
        self.lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True, name="log-" + name).start()

    def _run(self):
        while True:
            s = self.q.get()
            try:
                self.raw.write(s)
                self.raw.flush()
            except Exception:
                pass                                  # 控制台真没了（窗口被关）也不许崩

    def write(self, s):
        try:
            self.q.put_nowait(s)
        except queue.Full:
            with self.lock:
                self.dropped += 1                         # 冻结期间丢弃：宁可少日志，也不能停服务

    def flush(self):
        pass


def install_async_log():
    """把全局 stdout/stderr 换成非阻塞写（只在真正启动服务时安装，不影响 import 本模块的测试）。"""
    sys.stdout = _AsyncWriter(sys.stdout, "out")
    sys.stderr = _AsyncWriter(sys.stderr, "err")


CMS = C.WEB_BASE                                   # Web CMS，拓扑地图源（base64+gzip XML）
CMS_USER, CMS_PWD = C.RCS_USER, C.RCS_PWD
MAP_CODES = C.MAP_CODES
try:
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else C.WEB_PORT
except ValueError:                                 # 端口参数写错不该让服务起不来
    print("端口参数无效，回退默认 %d" % C.WEB_PORT, file=sys.stderr, flush=True)
    PORT = C.WEB_PORT

# 静态文件白名单后缀：源码(.py)、备份(.bak-*)、文档(.md)一律不外发
# —— config.py 含明文账号密码，绝不能通过 HTTP 被下载。
STATIC_EXT = {".html", ".htm", ".js", ".json", ".css", ".png", ".svg", ".ico", ".jpg", ".woff2"}
CTYPES = {".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
          ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
          ".png": "image/png", ".ico": "image/x-icon", ".jpg": "image/jpeg"}

# 需要密钥时给出的提示页（str 模板，发送时再编码；用 %d/%s 占位端口与默认图）
ACCESS_HINT = ("<!DOCTYPE html><meta charset=utf-8><title>需要访问密钥</title>"
               "<body style=\"font:15px/1.9 'Microsoft YaHei',sans-serif;padding:48px;color:#1c2130\">"
               "<h2 style=\"color:#b45309\">需要访问密钥</h2>"
               "<p>本服务已开启访问控制。请在地址后追加 <code>?k=密钥</code> 打开一次，"
               "浏览器会记住，之后正常刷新/收藏即可。</p>"
               "<p style=\"color:#6b7280\">例如：http://&lt;本机IP&gt;:%d/?map=%s&amp;k=密钥</p>"
               "</body>")

_sync_lock = threading.Lock()
_sync_at = 0.0                           # 上次成功/失败发起同步的时刻（节流用）
_pods_lock = threading.Lock()
_pods_at = 0.0                           # 上次「同步货架」发起时刻（按钮节流用）

_latest = {}                             # robotCode -> REST 形状行（ROBOT_PATH 合并 path；snapshot 与"接上就出车"用）
_latest_lock = threading.Lock()
_clients = {}                            # 订阅分组：图码 -> {queue,...}；键 "" = 订阅全部（缺省/无地图事件）
_clients_lock = threading.Lock()


def route_keys(ev_map):
    """事件该投给哪些分组：返回键集合，None = 全部（不分组 / 事件与地图无关）。纯函数，供测试。"""
    if not C.SSE_GROUP_BY_MAP or not ev_map:
        return None                      # 不分组，或货架表等系统级事件 → 发给所有人
    return ("", ev_map)                  # 订阅"全部"的 + 订阅该图的


def bcast(obj):
    s = json.dumps(obj, ensure_ascii=False)
    keys = route_keys(rcs_push.event_map(obj)) if isinstance(obj, dict) else None
    with _clients_lock:
        if keys is None:
            qs = [q for st in _clients.values() for q in st]
        else:
            qs = [q for k in keys for q in _clients.get(k, ())]
    for q in qs:
        try: q.put_nowait(s)
        except queue.Full: pass           # 慢客户端丢帧，宁丢勿堵

def sub_loop(ip, port):
    for body in rcs_push.iter_msgs(ip, port):
        try:                              # 单帧异常不许打死订阅线程（否则全站静默失联）
            for o in rcs_push.parse_frame(body):
                ev = None                 # 本帧若发生「稳定的取/放货」= 待增量维护的货架事件
                with _latest_lock:
                    if o["e"] == "status":
                        a = o["a"]
                        # 帧稳定过滤 + 落点锚点（用变化前一帧的坐标）都在 rcs_pods 里，纯状态机可离线测
                        ev = rcs_pods.note_car_frame(a["robotCode"], a.get("podCode") or "",
                                                     a.get("posX"), a.get("posY"),
                                                     a.get("mapCode") or "")
                        _latest[a["robotCode"]] = dict(a, timestamp=int(time.time()*1000))
                    elif o["e"] == "path" and o["id"] in _latest:
                        _latest[o["id"]]["path"] = o["path"]
                    elif o["e"] == "offline":
                        for rid in o["ids"]:
                            if rid in _latest: _latest[rid]["online"] = False
                if ev:
                    # 取/放货增量维护货架↔储位表：推送本身已带全部事实（谁扛着什么、车在哪），
                    # 不重拉 REST；全量校准由 rcs_pods 的启动/手动同步负责。
                    try:
                        rcs_pods.apply_car_event(*ev)
                    except Exception as e:
                        print("货架表增量更新异常: %s" % e, file=sys.stderr, flush=True)
                bcast(o)
        except Exception as e:
            print("sub_loop(%s:%s) 单帧解析异常(已跳过): %s" % (ip, port, e), file=sys.stderr, flush=True)

def fetch(url, data=None, ctype=None):
    h = {"User-Agent": "Mozilla/5.0"}
    if ctype: h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, headers=h)
    return urllib.request.urlopen(req, timeout=20).read()

def sync_maps():
    """登录 CMS -> 拉取每张地图的 XML -> 复用 parse_map 生成 maps/<QR>.json。返回 {'ok':{qr:name},'err':msg}"""
    from parse_map import parse_map           # 复用已验证的解析逻辑
    login = urllib.parse.urlencode({
        "ecsUserName": CMS_USER,
        "ecsPassword": C.sha256_pwd(),
        "pwdSafeLevelLogin": "0"}).encode()
    raw = fetch(CMS + C.LOGIN_PATH, login, "application/x-www-form-urlencoded")
    j = json.loads(raw)
    if not j.get("success"): return {"ok": {}, "err": "登录失败: " + str(j)[:100]}
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0"), ("Content-Type", "application/x-www-form-urlencoded")]
    # 重新登录一次拿 cookie（上面那次只是校验凭据，响应未带会话复用）
    op.open(urllib.request.Request(CMS + C.LOGIN_PATH, data=login))
    ok, errs = {}, []
    os.makedirs(os.path.join(BASE, "maps"), exist_ok=True)
    for code in MAP_CODES:
        try:
            req = urllib.request.Request(CMS + C.MAP_XML_PATH,
                                         data=urllib.parse.urlencode({"elcMapCode": code}).encode())
            d = json.loads(op.open(req, timeout=30).read())
            xml = gzip.decompress(base64.b64decode(d["elcMapContent"])).decode("utf-8", "replace")
            m = parse_map(None, txt=xml)
            qr = m["qr"] or code
            fp = os.path.join(BASE, "maps", qr + ".json")
            tmp = fp + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:   # 显式关闭句柄（原子替换前必须落盘）
                json.dump(m, f, ensure_ascii=False)
            os.replace(tmp, fp)
            ok[qr] = m["name"]
        except Exception as e:
            errs.append("%s:%s" % (code, e))
    return {"ok": ok, "err": ";".join(errs) or None}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    # ---- 访问门禁：IP 白名单（config.ALLOW_IPS） + 密钥（config.ACCESS_KEY） ----
    def _gate(self):
        """拦截返回 True（此时已回包）；放行返回 False。"""
        ip = self.client_address[0]
        if not C.ip_allowed(ip):
            print("拒绝访问（IP 不在白名单）: %s" % ip, file=sys.stderr, flush=True)
            self._send(403, b'{"error":"ip not allowed"}')
            return True
        if not C.access_required() or self._key_ok():
            return False                                 # 放行
        self._send(401, (ACCESS_HINT % (PORT, C.DEFAULT_MAP)).encode("utf-8"),
                   "text/html; charset=utf-8")
        return True

    def _key_ok(self):
        """密钥校验：?k= 命中则下发 cookie，之后浏览器凭 cookie 免带。"""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if hmac.compare_digest((query.get("k") or [""])[0], C.ACCESS_KEY):
            self._setkey = True
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "agv_key" and hmac.compare_digest(v, C.ACCESS_KEY):
                return True
        return False

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "_setkey", False):              # 首次凭 ?k= 进入：记住一年
            self.send_header("Set-Cookie", "agv_key=%s; Path=/; Max-Age=31536000; SameSite=Lax"
                             % C.ACCESS_KEY)
        self.end_headers()
        self.wfile.write(body)

    def _sse(self):
        ip = self.client_address[0]
        # 订阅分组：?map=BB 只推 BB 的事件；不带、或不是配置里的图 → 订阅全部（兼容默认）
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        want = (query.get("map") or [""])[0].strip()
        key = want if (want in MAP_CODES) else ""
        q = queue.Queue(C.CLIENT_QUEUE_MAX)
        with _clients_lock:
            _clients.setdefault(key, set()).add(q)
            n = sum(len(v) for v in _clients.values())
        print("浏览器接入 %s（订阅 %s，当前 %d 个连接）" % (ip, key or "全部", n), flush=True)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")   # 经 nginx 等反代时禁用缓冲，保证逐帧送达
            self.end_headers()
            self.wfile.write(b":ok\n\n")
            for s in self._snapshot(key):                 # 接上就先把当前画面发过去，不用等下一帧
                self.wfile.write(b"data: " + s.encode("utf-8") + b"\n\n")
            while True:
                try:
                    d = q.get(timeout=15)
                    self.wfile.write(b"data: " + d.encode("utf-8") + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b":ping\n\n")        # 注释行防代理空闲断连
        except OSError:
            pass                                          # 浏览器断开
        finally:
            with _clients_lock:
                st = _clients.get(key)
                if st:
                    st.discard(q)
                    if not st: _clients.pop(key, None)
                n = sum(len(v) for v in _clients.values())
            print("浏览器断开 %s（订阅 %s，当前 %d 个连接）" % (ip, key or "全部", n), flush=True)

    @staticmethod
    def _snapshot(key):
        """刚接上的订阅者先补一份"当前画面"：该图每台车的最新帧（货架表本来就有全量，不用补）。"""
        out = []
        with _latest_lock:
            rows = list(_latest.values())
        for a in rows:
            if not key or a.get("mapCode") == key:
                out.append(json.dumps({"e": "status", "a": a}, ensure_ascii=False))
        return out

    def do_GET(self):
        self._setkey = False
        if self._gate(): return
        path = self.path.split("?")[0]
        if path == "/api/events":
            return self._sse()
        if path == "/api/snapshot":                      # 当前推送快照（REST 形状，测试/调试用；零主动通讯）
            with _latest_lock:
                data = list(_latest.values())
            return self._send(200, json.dumps({"code": "0", "data": data}, ensure_ascii=False).encode())
        if path == "/api/config":                        # 前端唯一配置来源（不含任何凭据）
            return self._send(200, json.dumps(C.client_config(), ensure_ascii=False).encode())
        if path == "/api/pods":                          # 货架↔储位表（rcs_pods 缓存快照，启动校准+增量维护）
            return self._send(200, json.dumps(rcs_pods.payload(), ensure_ascii=False).encode())
        if path == "/": path = "/index.html"
        fp = os.path.normpath(os.path.join(BASE, path.lstrip("/")))
        if (not fp.startswith(BASE + os.sep) or not os.path.isfile(fp)
                or os.path.splitext(fp)[1].lower() not in STATIC_EXT):
            return self._send(404, b'{"error":"not found"}')   # 源码/备份/文档不对外
        ctype = CTYPES.get(os.path.splitext(fp)[1].lower(), "application/octet-stream")
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_POST(self):
        global _sync_at, _pods_at
        self._setkey = False
        if self._gate(): return
        if self.path == "/api/syncPods":               # 人工校准货架↔储位表（前端「同步货架」按钮）
            wait = C.PODS_SYNC_MIN_INTERVAL_S - (time.time() - _pods_at)
            if wait > 0:                               # 接口无登录、不锁账号，但也没必要连点打 RCS
                return self._send(200, json.dumps(
                    {"err": "同步过于频繁，%d 秒后再试" % int(wait + 0.999)},
                    ensure_ascii=False).encode())
            with _pods_lock:
                try:
                    rcs_pods.refresh()                 # 全量逐图校准（增量维护理论上不会错，这是人工兜底）
                    errs = rcs_pods.payload()["err"] or {}
                except Exception as e:
                    errs = {"*": str(e)}
            _pods_at = time.time()
            return self._send(200, json.dumps(
                {"err": ";".join("%s %s" % (k, v) for k, v in errs.items()) or None},
                ensure_ascii=False).encode())
        if self.path == "/api/syncMaps":
            wait = C.SYNC_MIN_INTERVAL_S - (time.time() - _sync_at)
            if wait > 0:                                 # 节流：每次同步要登录 CMS 两次，连点会锁账号
                return self._send(200, json.dumps(
                    {"ok": {}, "err": "同步过于频繁，%d 秒后再试" % int(wait + 0.999)},
                    ensure_ascii=False).encode())
            with _sync_lock:
                try:
                    r = sync_maps()
                except Exception as e:
                    r = {"ok": {}, "err": str(e)}
            _sync_at = time.time()
            try:                                         # 顺手刷新货架清单（几帧 REST 请求，失败不影响同步结果）
                rcs_pods.refresh()
            except Exception as e:
                print("syncMaps 后刷新货架失败: %s" % e, file=sys.stderr, flush=True)
            return self._send(200, json.dumps(r, ensure_ascii=False).encode())
        return self._send(404, b'{"error":"unknown"}')


class Srv(ThreadingHTTPServer):
    """注意：process_request_thread 调的是 **server 实例** 的 handle_error，
    挂在 handler 上是死代码——降噪必须挂在这里。"""
    daemon_threads = True
    # 监听握手队列：socketserver 默认只有 5！多人同时打开页面（或推送断线后的重连风暴）
    # 会直接把多出来的连接拒掉（实测 1000 并发被拒 191 个 ConnectionRefusedError）。
    request_queue_size = 256

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        # 浏览器关标签页 / curl 超时 / SSE 重连都会在读写中途断连，这是常态，
        # 不该让 socketserver 打印整段堆栈（会淹没真正的异常）。
        if isinstance(e, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        ThreadingHTTPServer.handle_error(self, request, client_address)


def lan_urls(port):
    """列出其它主机可用来访问本服务的地址（非 127.*）。"""
    ips = set()
    for probe in (C.RCS_ENGINE_IP, C.RCS_WEB_IP):        # UDP connect 不发包，只让内核选出站网卡
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe, 9)); ips.add(s.getsockname()[0])
        except OSError:
            pass
        finally:
            if s: s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def startup_banner():
    L = ["=" * 74,
         "AGV 监控代理已启动 —— 本机是全厂唯一与 RCS 通讯的节点"]
    for ip, port in C.push_targets():
        L.append("  ZMTP 订阅   %s:%d" % (ip, port))
    L.append("  本机访问    http://127.0.0.1:%d/?map=%s" % (PORT, C.DEFAULT_MAP))
    for ip in lan_urls(PORT):
        L.append("  其它主机    http://%s:%d/?map=%s" % (ip, PORT, C.DEFAULT_MAP))
    if C.WEB_BIND == "127.0.0.1":
        L.append("  注意        当前只监听 127.0.0.1，其它主机访问不到（改 config.WEB_BIND 为 0.0.0.0）")
    L.append("  访问控制    %s" % ("开放（未配 config.ACCESS_KEY）" if not C.access_required()
                                  else "需密钥：首次用 ?k=<密钥> 打开，之后由 cookie 记住"))
    if C.ALLOW_IPS:
        L.append("  来源限制    %s" % ", ".join(C.ALLOW_IPS))
    L.append("  地图同步    两次间隔不小于 %ds；推送断了本进程自动重登重连" % C.SYNC_MIN_INTERVAL_S)
    L.append("  控制台      Windows 选中控制台文字会暂停进程（日志停住 + 前端卡在「连接中」）；"
             "本服务已改异步日志，冻结时最多丢几行日志、不影响推送")
    L.append("  货架↔储位  启动校准一次；取/放货按推送增量维护；「同步货架」按钮人工校准"
             "（需本机在 RCS 允许配置IPs 内）")
    L.append("  推送订阅    %s" % ("按图分组（浏览器只收本图事件；不带 ?map= 或非配置图＝订阅全部）"
                                  if C.SSE_GROUP_BY_MAP else
                                  "全量广播（每台客户端都收四张图的事件；config.SSE_GROUP_BY_MAP=False）"))
    L.append("=" * 74)
    L.append("提示：其它主机无需访问 RCS 的 6990/8790，也无需被 RCS 登记白名单"
             "（白名单按发起登录的机器 IP 记录，只有本机需要能连 RCS）。")
    print("\n".join(L), flush=True)


if __name__ == "__main__":
    install_async_log()          # 先装非阻塞日志：控制台被「快速编辑」冻结时也不会拖住推送
    for ip, port in C.push_targets():
        t = threading.Thread(target=sub_loop, args=(ip, port), daemon=True, name="sub%d" % port)
        t.start()
    threading.Thread(target=rcs_pods.pods_loop, daemon=True, name="pods").start()   # 启动校准一次即退出
    # 货架表任何变化（校准 / 取放货增量）→ 立即广播给所有浏览器
    rcs_pods.set_on_updated(lambda: bcast(dict(rcs_pods.payload(), e="pods")))
    startup_banner()
    Srv((C.WEB_BIND, PORT), H).serve_forever()
