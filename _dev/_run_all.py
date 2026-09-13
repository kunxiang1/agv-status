# -*- coding: utf-8 -*-
"""一次性跑完全部「离线」回归用例（不连 RCS，随时可跑）。

用法: python _dev/_run_all.py

不含 `_verify.py`（会真的登录现场 CMS 取证）与 `_live_test.js`（需要 server 正在运行）——
这两者请在有现场环境时单独执行。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV = os.path.join(ROOT, "_dev")

PY_CASES = [("配置集中化", "_config_test.py"), ("货架清单译码与落点", "_pods_test.py")]
JS_CASES = [("动画引擎 12 组纯逻辑", "_nav_test.js"),
            ("后台恢复不补帧冲刺", "_resume_test.js"),
            ("启动链路无头集成", "_boot_test.js"),
            ("告警闸门", "_alarm_test.js")]

fails = []
for title, script in PY_CASES:
    print("\n" + "=" * 62 + "\n[%s] %s\n" % ("py", title) + "=" * 62)
    if subprocess.call([sys.executable, os.path.join(DEV, script)], cwd=ROOT):
        fails.append(title)

for title, script in JS_CASES:
    print("\n" + "=" * 62 + "\n[%s] %s\n" % ("node", title) + "=" * 62)
    if subprocess.call(["node", os.path.join(DEV, script)], cwd=ROOT):
        fails.append(title)

print("\n" + "=" * 62)
if fails:
    print("回归失败: " + "；".join(fails))
    sys.exit(1)
print("全部离线回归通过 ✔  （现场联调另跑 _dev/_live_test.js）")
