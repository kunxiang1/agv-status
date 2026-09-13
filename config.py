# -*- coding: utf-8 -*-
"""全局配置 —— 本项目的**唯一配置改动点**。

约定：任何 IP / 端口 / 账号 / 密码 / 地图清单 / 运行参数只在本文件出现一次，
其余模块（rcs_push.py / server.py / parse_map.py / _dev/*）一律 `import config` 取值，
前端页面所需的子集由 server.py 的 `GET /api/config` 下发（见 client_config()）。
换句话说：**改这里一处，全链路生效**，不要再往业务代码里写常量。

敏感信息提示：本文件含明文账号密码，仅限内网使用；如需外发请先清空 PWD/CMS_PWD。
"""

# --------------------------------------------------------------------------
# 1. RCS-2000 服务器（海康移动机器人调度系统）
# --------------------------------------------------------------------------
RCS_WEB_IP = "rcs-ip"           # Web CMS（8181）与告警推送（8790）门户机
RCS_ENGINE_IP = "engine-ip"     # 数据推送调度引擎（6990）
PORT_WEB = 8181                 # Web CMS / Struts 登录、地图 XML
PORT_PUSH = 6990                # ZMTP 状态/路径推送
PORT_ALARM = 8790               # ZMTP 告警推送
PORT_REST = 8083                # 遗留 REST（应用已不用，仅 _dev/_verify.py 取证脚本用）

RCS_USER = "admin"              # 推送白名单登记 & CMS 登录共用同一账号
RCS_PWD = "password"            # 明文，登录时取 SHA-256（连续错误会锁账号，勿循环重试）

# --------------------------------------------------------------------------
# 2. 本机 Web 服务 —— 本机是唯一与 RCS 通讯的节点（ZMTP 代理）
#    拓扑：RCS ──ZMTP──> 本机(server.py，单条订阅) ──SSE/HTTP──> 其它主机浏览器
#    其它主机不需要、也不能直连 6990/8790；它们只要访问本机的 WEB_PORT 即可。
#    下面的部署开关都可用同名环境变量覆盖（默认值仍只在这里定义一处）：
#      AGV_WEB_BIND / AGV_WEB_PORT / AGV_ACCESS_KEY / AGV_ALLOW_IPS
# --------------------------------------------------------------------------
WEB_PORT = 8899                 # python server.py 默认监听端口（可用 argv[1] 或 AGV_WEB_PORT 覆盖）
WEB_BIND = "0.0.0.0"            # 监听所有网卡＝允许其它主机访问；只想本机用就改 "127.0.0.1"
ACCESS_KEY = ""                 # 非空＝对外访问需密钥：首次用 http://本机:8899/?k=密钥 打开，
                                # 之后由 cookie 记住，正常收藏/刷新都不用再带
ALLOW_IPS = []                  # 非空＝只接受这些来源 IP。写法即语义，避免误放行：
                                #   写完整地址（四段） → **精确**匹配这一个 IP
                                #   以点结尾写网段     → **前缀**匹配整段，如 "client-ip."
                                #   不完整且无结尾点   → 宽容写法，等价于在上面补一个点
                                # 例：["client-ip.", "client-ip"]
SYNC_MIN_INTERVAL_S = 30        # 两次「同步地图」最小间隔：多人同时连点是常态，而每次同步
                                # 要登录 CMS 两次，刷太快会把 admin 账号刷到锁定
CLIENT_QUEUE_MAX = 2000         # 每个浏览器连接的服务端待发队列上限（满则丢帧，慢客户端不拖垮推送）
SSE_GROUP_BY_MAP = True         # SSE 按图分组订阅：前端连 /api/events?map=BB，只推该图事件
                                # （看不到的图推过去就是白费流量与 CPU；四图全推＝4 倍浪费）
                                # 无地图字段的事件（货架表 pods/系统级）照旧发给所有人；
                                # 不带 ?map= 或 map 不在 MAP_CODES 时＝订阅全部（兼容默认与"全厂总览"用）
                                # 关掉此项＝行为与不分组完全一致（回退开关）

# 货架↔储位表（REST 全量基线 hikRpcService/queryPodBerthAndMat，按地图逐张拉）：
# 只在启动时全量校准一次；取/放货不重拉——6990 推送的 podCode 变化就是全部事实，服务端
# 增量维护该表（落到最近储位节点）并 SSE 广播。人工校准走前端「同步货架」按钮（/api/syncPods），
# 不做周期校准：人多时周期请求会平白加重 RCS 负担。
PODS_SYNC_MIN_INTERVAL_S = 10   # 「同步货架」按钮的最小间隔秒：该接口无需登录、不锁账号，但也别连点打 RCS

# --------------------------------------------------------------------------
# 3. 地图
# --------------------------------------------------------------------------
MAP_CODES = ["EE", "BB", "CC", "DD"]  # 同步/展示顺序
MAP_NAMES = {"EE": "二厂1楼", "BB": "二厂3楼", "CC": "二厂2楼", "DD": "钻房1楼"}
DEFAULT_MAP = "BB"              # 打开页面时的默认图
MAP_XML_DIR = r"D:\Documents\hermes"  # parse_map.py CLI 的离线 XML 目录
MAP_XML_GLOB = "地图-*.xml"

# --------------------------------------------------------------------------
# 4. 运行参数
# --------------------------------------------------------------------------
POLL_GUESS_MS = 250             # 首帧前对推送间隔的猜测（现场实测 ~208ms）
LOGIN_BACKOFF_S = 60            # 登录失败后的退避秒数，避免刷登录触发账号锁定
RECV_TIMEOUT_S = 15             # 推送通道无数据超时（视为白名单过期）→ 重登重连
RECONN_ALARM_HOST = RCS_WEB_IP  # 8790 告警通道所在主机

# --------------------------------------------------------------------------
# 5. 派生值 / 供前端使用的子集
# --------------------------------------------------------------------------
import os

# 部署期覆盖：值仍以本文件为准，环境变量只用于「不改文件就换部署参数」（容器/临时实例/自动化测试）
WEB_BIND = os.environ.get("AGV_WEB_BIND", WEB_BIND)
ACCESS_KEY = os.environ.get("AGV_ACCESS_KEY", ACCESS_KEY)
if os.environ.get("AGV_ALLOW_IPS"):                      # 逗号分隔，忽略空白
    ALLOW_IPS = [s.strip() for s in os.environ["AGV_ALLOW_IPS"].split(",") if s.strip()]
if os.environ.get("AGV_WEB_PORT"):
    WEB_PORT = int(os.environ["AGV_WEB_PORT"])

WEB_BASE = "http://%s:%d" % (RCS_WEB_IP, PORT_WEB)  # http://rcs-ip:8181
LOGIN_PATH = "/rcms/web/login/login.action"
MAP_XML_PATH = "/rcms/web/elcMap/findByElcMapCode.action"


def push_targets():
    """推送订阅目标：(主机, 端口) 列表 —— 数据通道 + 告警通道。**只有本进程会连它们**。"""
    return [(RCS_ENGINE_IP, PORT_PUSH), (RECONN_ALARM_HOST, PORT_ALARM)]


def sha256_pwd():
    """登录表单用的密码摘要（SHA-256 十六进制小写）。"""
    import hashlib
    return hashlib.sha256(RCS_PWD.encode()).hexdigest()


def ip_allowed(ip):
    """来源 IP 是否放行（ALLOW_IPS 为空＝全部放行）。

    按写法区分语义，避免「填了个精确 IP 却顺带放行了邻居」这类误判：
      "rcs-ip" → 只放行这一个 IP
      "rcs-ip."   → 放行整个前缀（/24）
      "rcs-ip"    → 不完整且无结尾点，宽容当作前缀 "rcs-ip."
    """
    if not ALLOW_IPS:
        return True
    for p in ALLOW_IPS:
        if p.endswith("."):
            if ip.startswith(p):
                return True
        elif p.count(".") == 3:                      # 完整四段＝精确
            if ip == p:
                return True
        elif ip.startswith(p + "."):                 # 宽容前缀写法
            return True
    return False


def access_required():
    """是否需要密钥访问（未配密钥＝开放，便于内网默认零门槛使用）。"""
    return bool(ACCESS_KEY)


def client_config():
    """下发给浏览器的配置子集（不含任何凭据）：/api/config 的应答体。

    注意：这里**永远不能**包含 RCS 账号密码、ACCESS_KEY —— 它会被任意一台
    访问本服务的浏览器明文取走。新增字段前先确认这一点。
    """
    return {"maps": list(MAP_CODES), "mapNames": dict(MAP_NAMES),
            "defaultMap": DEFAULT_MAP, "pollGuessMs": POLL_GUESS_MS}

