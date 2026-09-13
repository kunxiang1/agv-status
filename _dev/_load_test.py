# -*- coding: utf-8 -*-
"""SSE 并发压测：N 个客户端同时订阅 /api/events，量单机到底能扛多少。

量三件事（都是现场真实数据流，不造数据）：
  · 单客户端吞吐（KB/s）与总吞吐 → 换算成网卡 Mbps（千兆网 = 954 Mbps 可用）
  · 每客户端「事件到达间隔」的分位与最大间隔 → 反映推送是否被调度拖出抖动
  · 客户端侧异常（连接被拒/半途断开）——服务端线程/句柄是否吃紧

用法：python _dev/_load_test.py [客户端数=200] [秒数=10] [地址=127.0.0.1:8899] [订阅方式=mix|all|图码]
  · mix（默认）= 客户端按 config.MAP_CODES 均匀分散到四张图（对应"按图分组订阅"打开后的真实负载）
  · all        = 全部不带 ?map=，即订阅全部事件（等价于不分组；用来对比前后）
  · BB         = 全部订阅同一张图
注意：需要在 server 运行时跑；纯本机回环，不受网卡限制（网卡约束靠换算）。
"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as C  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
SEC = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
HOST, _, PORT = (sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1:8899").partition(":")
MODE = (sys.argv[4] if len(sys.argv) > 4 else "mix").strip()


def subscribe(i):
    if MODE == "all":
        return ""
    if MODE == "mix":
        return "?map=" + C.MAP_CODES[i % len(C.MAP_CODES)]
    return "?map=" + MODE


def req_for(i):
    qs = subscribe(i)
    return ("GET /api/events%s HTTP/1.1\r\nHost: %s:%s\r\nAccept: text/event-stream\r\n"
            "Connection: keep-alive\r\n\r\n" % (qs, HOST, PORT)).encode()

res = []                     # (bytes, events, gaps_ms:list, err)
lock = threading.Lock()
stop = threading.Event()


def worker(i):
    dt, ev, gaps, err = 0, 0, [], None
    gap_max = 0.0
    try:
        s = socket.create_connection((HOST, int(PORT)), timeout=8)
        s.sendall(req_for(i))
        s.settimeout(SEC + 5)
        t_start = time.time()
        last = None
        while not stop.is_set():
            chunk = s.recv(65536)
            if not chunk:
                break
            now = time.time()
            dt += len(chunk)
            n = chunk.count(b"data: ")
            if n:
                ev += n
                if last is not None:
                    g = (now - last) * 1000
                    if g > gap_max:
                        gap_max = g
                    if len(gaps) < 400:
                        gaps.append(g)
                last = now
            if now - t_start > SEC:
                break
        s.close()
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
    with lock:
        res.append((dt, ev, gaps, gap_max, err, time.time()))


print("启动 %d 个 SSE 客户端（订阅方式 %s），压测 %.0fs ..." % (N, MODE, SEC))
t0 = time.time()
ths = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(N)]
for t in ths:
    t.start()
time.sleep(SEC)
stop.set()
for t in ths:
    t.join(timeout=8)
wall = time.time() - t0

okc = [r for r in res if not r[4]]
errs = [r[4] for r in res if r[4]]
tot_bytes = sum(r[0] for r in okc)
tot_ev = sum(r[1] for r in okc)
gaps = sorted(g for r in okc for g in r[2])
gapmax = max((r[3] for r in okc), default=0)


def q(f):
    return gaps[int((len(gaps) - 1) * f)] if gaps else float("nan")


print("\n客户端 %d 个（成功 %d / 失败 %d）" % (N, len(okc), len(errs)))
if errs:
    print("  失败样例:", errs[:3])
if okc:
    print("  单客户端: %6.1f KB/s   %5.1f 事件/s   （平均 %.0f B/事件）"
          % (tot_bytes / wall / 1024 / len(okc), tot_ev / wall / len(okc), tot_bytes / max(tot_ev, 1)))
print("  合计吞吐: %8.2f MB/s = %6.0f Mbps  ← 本机回环实测" % (tot_bytes / wall / 1e6, tot_bytes / wall * 8 / 1e6))
print("  事件间隔: p50 %.0fms  p95 %.0fms  p99 %.0fms  最大 %.0fms" % (q(.5), q(.95), q(.99), gapmax))
if okc:
    per = tot_bytes / wall / len(okc)
    print("\n按本机实测速率换算（网卡上限 954 Mbps，留 20%% 余量＝763 Mbps 可用）:")
    for n in (100, 200, 300, 500, 1000, 2000):
        mb = per * n * 8 / 1e6
        note = "✅ 带宽有余" if mb < 763 else "❌ 撞带宽墙"
        print("  %5d 客户端 → %7.1f Mbps（占千兆 %3.0f%%） %s" % (n, mb, mb / 954 * 100, note))
    if len(okc) > 5:
        print("\n  本次 %d 客户端合计 %.0f Mbps ⇒ 千兆网可容纳约 **%d** 个客户端（按 763 Mbps 计）"
              % (len(okc), tot_bytes / wall * 8 / 1e6, int(763 * 1e6 / (per * 8))))
