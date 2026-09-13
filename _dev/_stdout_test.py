# -*- coding: utf-8 -*-
"""非阻塞日志用例：控制台被「快速编辑」冻住时，print 不许拖住业务线程。

现场症状：在 Windows 控制台里用鼠标选中文字 → 进程被系统挂起 → 日志停住、前端全卡在
「连接中」，按一下回车才恢复。服务每帧都在打印（浏览器接入/断开、货架表变化），
所以这个冻结会直接拖死订阅与 SSE 线程，必须由日志层兜住。

做法：把 _AsyncWriter 的底层流换成一个会阻塞的假流，狂写 5000 行，断言调用方立刻返回、
队列满时丢弃而不是阻塞，解冻后后台线程继续真正写出去。
"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print("%s %s %s" % ("✔" if cond else "✘", name, detail))
    if not cond:
        fails.append(name)


class Frozen:
    """模拟被冻结的控制台：write 一直阻塞，直到放行。"""

    def __init__(self):
        self.gate = threading.Event()
        self.bytes_written = 0

    def write(self, s):
        self.gate.wait(10)
        self.bytes_written += len(s)

    def flush(self):
        pass


raw = Frozen()
w = server._AsyncWriter(raw, "test")

t0 = time.time()
for _ in range(5000):                      # 远超队列容量：必须立刻返回
    w.write("x" * 200)
    w.flush()
dt = time.time() - t0
check("控制台冻结时写入不阻塞调用方", dt < 1.0, "5000 行用时 %.3fs" % dt)
check("队列写满后丢弃而非阻塞/抛错", w.dropped > 0, "dropped=%d" % w.dropped)

raw.gate.set()                             # 解冻
time.sleep(1.0)
check("解冻后后台线程继续真正写出去", raw.bytes_written > 0, "已写 %d 字节" % raw.bytes_written)

check("import 本模块不会替换 sys.stdout（只在启动服务时安装）",
      not isinstance(sys.stdout, server._AsyncWriter))

print()
if fails:
    print("失败: " + "；".join(fails))
    sys.exit(1)
print("非阻塞日志用例通过 ✔")
