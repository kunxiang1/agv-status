# AGV web monitor — stdlib only. SSE push from RCS private channels (6990 数据/8790 告警)
# + static files + legacy REST proxy (供 _dev 测试) + auto map sync (8181 CMS)。
import base64, gzip, hashlib, json, os, queue, sys, threading, time, urllib.request, urllib.parse
import rcs_push
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
CMS = rcs_push.WEB              # Web CMS，拓扑地图源（base64+gzip XML），地址随 RCS_WEB 环境变量
CMS_USER, CMS_PWD = rcs_push.USER, rcs_push.PWD
MAP_CODES = [c for c in os.environ.get("MAP_CODES", "EE,BB,CC,DD").split(",") if c]
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
_sync_lock = threading.Lock()

_latest = {}                             # robotCode -> REST 形状行（ROBOT_PATH 合并 path；仅 /api/snapshot 调试用）
_latest_lock = threading.Lock()
_clients = set()
_clients_lock = threading.Lock()
def bcast(obj):
    s = json.dumps(obj, ensure_ascii=False)
    with _clients_lock:
        qs = list(_clients)
    for q in qs:
        try: q.put_nowait(s)
        except queue.Full: pass           # 慢客户端丢帧，宁丢勿堵

def sub_loop(ip, port):
    for body in rcs_push.iter_msgs(ip, port):
        for o in rcs_push.parse_frame(body):
            with _latest_lock:
                if o["e"] == "status":
                    _latest[o["a"]["robotCode"]] = dict(o["a"], online=True, timestamp=int(time.time()*1000))
                elif o["e"] == "path" and o["id"] in _latest:
                    _latest[o["id"]]["path"] = o["path"]
                elif o["e"] == "offline":
                    for rid in o["ids"]:
                        if rid in _latest: _latest[rid]["online"] = False
            bcast(o)

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
        "ecsPassword": hashlib.sha256(CMS_PWD.encode()).hexdigest(),
        "pwdSafeLevelLogin": "0"}).encode()
    raw = fetch(CMS + "/rcms/web/login/login.action", login, "application/x-www-form-urlencoded")
    j = json.loads(raw)
    if not j.get("success"): return {"ok": {}, "err": "登录失败: " + str(j)[:100]}
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0"), ("Content-Type", "application/x-www-form-urlencoded")]
    # 重新登录一次拿 cookie（上面那次只是校验凭据，响应未带会话复用）
    op.open(urllib.request.Request(CMS + "/rcms/web/login/login.action", data=login))
    ok, errs = {}, []
    os.makedirs(os.path.join(BASE, "maps"), exist_ok=True)
    for code in MAP_CODES:
        try:
            req = urllib.request.Request(CMS + "/rcms/web/elcMap/findByElcMapCode.action",
                                         data=urllib.parse.urlencode({"elcMapCode": code}).encode())
            d = json.loads(op.open(req, timeout=30).read())
            xml = gzip.decompress(base64.b64decode(d["elcMapContent"])).decode("utf-8", "replace")
            m = parse_map(None, txt=xml)
            qr = m["qr"] or code
            fp = os.path.join(BASE, "maps", qr + ".json")
            tmp = fp + ".tmp"
            json.dump(m, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(tmp, fp)
            ok[qr] = m["name"]
        except Exception as e:
            errs.append("%s:%s" % (code, e))
    return {"ok": ok, "err": ";".join(errs) or None}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/events":
            q = queue.Queue(2000)
            with _clients_lock: _clients.add(q)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.wfile.write(b":ok\n\n")
                while True:
                    try:
                        d = q.get(timeout=15)
                        self.wfile.write(b"data: " + d.encode("utf-8") + b"\n\n")
                    except queue.Empty:
                        self.wfile.write(b":ping\n\n")   # 注释行防代理空闲断连
            except OSError:
                pass                                     # 浏览器断开
            finally:
                with _clients_lock: _clients.discard(q)
            return
        if path == "/api/snapshot":                      # 当前推送快照（REST 形状，测试/调试用；零主动通讯）
            with _latest_lock:
                data = list(_latest.values())
            return self._send(200, json.dumps({"code": "0", "data": data}, ensure_ascii=False).encode())
        if path == "/": path = "/index.html"
        fp = os.path.normpath(os.path.join(BASE, path.lstrip("/")))
        if (not fp.startswith(BASE + os.sep) and fp != BASE) or not os.path.isfile(fp):   # 修：纯前缀比较可被同名前缀目录绕过
            return self._send(404, b'{"error":"not found"}')
        ctype = ("text/html; charset=utf-8" if fp.endswith(".html") else
                 "application/javascript; charset=utf-8" if fp.endswith(".js") else
                 "image/png" if fp.endswith(".png") else
                 "application/json; charset=utf-8")
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_POST(self):
        if self.path == "/api/syncMaps":
            with _sync_lock:
                try:
                    r = sync_maps()
                except Exception as e:
                    r = {"ok": {}, "err": str(e)}
            return self._send(200, json.dumps(r, ensure_ascii=False).encode())
        return self._send(404, b'{"error":"unknown"}')

if __name__ == "__main__":
    for ip, port in ((rcs_push.RCS_ENGINE, 6990), (urllib.parse.urlparse(CMS).hostname, 8790)):
        t = threading.Thread(target=sub_loop, args=(ip, port), daemon=True, name="sub%d" % port)
        t.start()
    print("AGV monitor on http://127.0.0.1:%d  (SSE /api/events <- RCS 6990+8790 push; maps sync via /api/syncMaps)" % PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
