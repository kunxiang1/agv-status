# AGV 实时监控（海康 RCS-2000）

纯 Python 标准库 + 原生 HTML/JS：**无第三方依赖、无框架、无构建步骤**。

本机作为 **ZMTP 代理**直连 RCS 私有推送通道（官方 MonitorClient 同款数据源），
现场 12 台在网车 × 4 张图，每车约 208ms 一帧（≈5Hz），与官方客户端并行共存、互不影响。
**其它主机只需访问本机端口，不需要也不能连 RCS。**

```bash
# 启动（会打印本机与「其它主机可用的」访问地址）
python server.py                 # 默认 8899，也可 python server.py <端口>

# 访问
http://127.0.0.1:8899/?map=BB              # 本机
http://<本机IP>:8899/?map=BB               # 其它主机（EE/BB/CC/DD 四图可切换）

# 回归（不连 RCS，随时可跑）
python _dev/_run_all.py
node   _dev/_live_test.js                  # 现场真实数据端到端，需 server 在跑
```

> ⚠️ `config.py` 含**明文** RCS 账号密码。仅限内网使用；外发前请先清空 `RCS_PWD`。
> 静态服务已按后缀白名单放行，`config.py` / `server.py` / `*.bak-*` / `*.md` 一律 404（见 §6.0）。

---

## 1. 为什么不用轮询

项目最初用公开接口 `POST :8083/rcms-dps/rest/queryAgvStatus`（5 秒轮询）。
抓包分析（`_dev/协议分析报告_monitor-rcs.md`、`_dev/RCS-2000_未公开接口补充.md`）证实：官方
MonitorClient **根本不调用这个 REST 接口**，它走的是 6990/8790 端口的 ZMTP 推送通道；
REST 的 5s 粒度是本项目动画"不如官方跟手"的唯一原因——两端返回的坐标数值逐帧比对完全相等。
因此改为直连推送通道，精度与官方对齐（208ms），REST 通道已彻底移除
（`config.PORT_REST` 只留给 `_dev/_verify.py` 取证脚本）。

## 2. 架构

```
海康 RCS-2000 (rcs-ip)
 ├─ :8181  Web/Struts     ←── login()：sha256(密码) 表单登录，登记本机 IP 进推送白名单
 ├─ :6990  TCP (engine-ip 调度引擎)  ┐
 ├─ :8790  TCP (rcs-ip 门户机)    ┴─ ZMTP 3.0 SUB 订阅 ← rcs_push.py（推送原始帧）
 └─ :8181  地图 XML（base64+gzip）      ← syncMaps()：⟳同步地图按钮触发

config.py (唯一配置源：IP/端口/账号/地图清单/部署开关)
 └── 被 rcs_push / server / parse_map / _dev 脚本 import；浏览器所需子集经 /api/config 下发

server.py (本机，唯一后端)
 ├─ 2 个订阅线程 sub_loop：RCS 帧 → 解析成事件对象 → 广播
 ├─ GET  /api/events   SSE：所有事件实时推给浏览器（多浏览器标签共享同一订阅）
 ├─ GET  /api/config   站点配置下发（地图清单/默认图/推送节奏猜测，不含凭据）
 ├─ GET  /api/snapshot 当前车辆快照（调试/测试用，不产生对 RCS 的任何请求）
 ├─ POST /api/syncMaps 手动拉取 4 张地图 → maps/*.json（有 30s 节流）
 └─ GET  /*            静态文件（仅放行 .html/.js/.json/.css/图片；源码/备份/文档一律 404）

index.html (前端)
 EventSource → handle(事件) → ingest(单帧采纳) → 动画引擎(frame→stepAgv/resyncAgv + rAF) → canvas
```

要点：**RCS→server 只有一条订阅连接**（不是每浏览器一条）；浏览器↔server 用 SSE；
推送断了 server 自动重登录重连（`rcs_push.iter_msgs` 内建），浏览器再自动重连 SSE。

### 2.1 配置只有一处

环境值（RCS 两个 IP、三个端口、账号密码）与站点参数（地图编号与中文名、默认图）
**只在项目根 `config.py` 出现一次**：

| 要改的东西 | 改哪里 |
|---|---|
| RCS 服务器 IP / 端口 | `config.py` 的 `RCS_WEB_IP` / `RCS_ENGINE_IP` / `PORT_*` |
| 登录账号密码 | `config.py` 的 `RCS_USER` / `RCS_PWD`（推送白名单登记与 CMS 登录共用） |
| 地图清单、中文名、默认图 | `config.py` 的 `MAP_CODES` / `MAP_NAMES` / `DEFAULT_MAP` |
| 本机监听端口 | `config.py` 的 `WEB_PORT`（仍可被 `python server.py <端口>` 覆盖） |
| 对外访问控制 | `config.py` 的 `ACCESS_KEY` / `ALLOW_IPS`（见 §2.2） |

前端不再内置地图清单：`index.html` 启动时先取 `GET /api/config`，站点值全部来自
`config.py` 的 `client_config()`（该接口**不下发任何凭据**）。
`python _dev/_config_test.py` 会扫描全部可执行源码，断言这些字面量除 `config.py`
外不再出现——即「改一处全局生效」是可自动验证的。

### 2.2 对外提供服务：本机是唯一与 RCS 通讯的代理

```
         ┌──────────── 本机（唯一需要能连 RCS 的机器）────────────┐
RCS ──ZMTP──> server.py  （全厂一条 6990 订阅 + 一条 8790 订阅）
 │             │  解析成事件 → 广播
 │             └──HTTP/SSE:8899──> 其它主机的浏览器（若干台，各看各的图）
 └ 8181 CMS（仅「同步地图」时被本机访问）
```

- **其它主机只访问本机 8899**：`index.html` 里没有任何 RCS 地址或端口，四次后端调用
  （`api/config`、`maps/*.json`、`/api/events`、`/api/syncMaps`）全是相对路径，
  因此在任意主机上打开 `http://<本机IP>:8899/?map=BB` 即可，无需改代码、无需装东西。
- **其它主机不需要被 RCS 登记白名单**：推送准入按"发起 Web 登录的那台机器 IP"记录，
  只有本机需要能连 RCS。本机以外的机器即使装不了 RCS 客户端也能看监控。
- **订阅只有一条**：RCS 侧永远只看到 1 个订阅者，N 个浏览器标签共享它（SSE 广播），
  不会因为人多而给 RCS 加负载。
- 启动横幅直接打印**可分发给他人的地址**（自动探测非 127 网卡，优先选通往 RCS 的那块）：

```
==========================================================================
AGV 监控代理已启动 —— 本机是全厂唯一与 RCS 通讯的节点
  ZMTP 订阅   engine-ip:6990
  ZMTP 订阅   rcs-ip:8790
  本机访问    http://127.0.0.1:8899/?map=BB
  其它主机    http://本机ip:8899/?map=BB
  访问控制    开放（未配 config.ACCESS_KEY）
  地图同步    两次间隔不小于 30s；推送断了本进程自动重登重连
==========================================================================
```

#### 部署开关（都在 `config.py`，也可用环境变量覆盖）

| 配置项 | 默认 | 作用 |
|---|---|---|
| `WEB_BIND` | `0.0.0.0` | 监听所有网卡＝允许其它主机访问；只想本机用改 `127.0.0.1` |
| `WEB_PORT` | `8899` | 对外端口（`AGV_WEB_PORT` 或 `python server.py <端口>` 可覆盖） |
| `ACCESS_KEY` | `""`（开放） | 非空＝需密钥。首次用 `http://本机:8899/?k=密钥` 打开一次，服务端下发一年期 cookie，之后刷新/收藏都不用再带；`/api/*` 与 SSE 一并受控，未带密钥返回中文提示页 |
| `ALLOW_IPS` | `[]`（全放行） | 来源限制。**写法即语义**：写完整地址＝精确、以点结尾＝匹配整段（如 `"client-ip."`）、不完整且无结尾点＝宽容前缀 |
| `SYNC_MIN_INTERVAL_S` | `30` | 两次「同步地图」最小间隔。每次同步要登录 CMS **两次**，多人同时连点是常态，太密会把 admin 账号刷到锁定 |
| `CLIENT_QUEUE_MAX` | `2000` | 每个浏览器连接的服务端待发队列上限，满则丢帧，慢客户端不拖垮推送 |

环境变量覆盖：`AGV_WEB_BIND / AGV_WEB_PORT / AGV_ACCESS_KEY / AGV_ALLOW_IPS`
（用于容器、临时实例、自动化测试；**默认值仍只定义在 `config.py` 一处**）。

#### 容量与运维口径

- **规模**：12 车 × 5Hz ≈ 每秒 140 条事件（实测 8 秒 1135 条），每条只编码一次后广播给所有连接。
  单进程在**十来个浏览器连接**下很轻松；再往上（几十连接）建议按图过滤或起多进程——
  注意**只能有一个实例连 RCS**，否则 RCS 侧会出现多个订阅者。
- **排查顺序**：其它主机打不开 → ①本机 8899 是否在听（`netstat -ano | findstr 8899`）
  → ②本机 Windows 防火墙是否放行 8899 入站 → ③`WEB_BIND` 是否被设成了 `127.0.0.1`
  → ④若配了 `ALLOW_IPS`/`ACCESS_KEY`，确认来源 IP 与密钥。
- 服务端日志会打印每次「浏览器接入/断开 + 当前连接数」，可直接确认有几个客户端在看。

## 3. 协议实现（rcs_push.py）

### 3.1 接入前提：IP 登记
推送引擎的准入是 **IP 级**：任何一次成功的 Web 登录
（`POST http://rcs-ip:8181/rcms/web/login/login.action`，参数
`ecsUserName=admin&ecsPassword=<sha256(密码)>&pwdSafeLevelLogin=0`）
就把本机 IP 登记进引擎白名单（实测：登录后随机 GUID、随机 UDP 端口甚至完全不发
保活均可订阅；GUID 不是凭证、密码不进 6990）。`rcs_push.login()` 即此一步。

**凭据错会锁账号**（官方应答明说"连续错会锁定"），所以 `login()` 区分两类失败：
凭据类抛 `LoginError` → 按 `config.LOGIN_BACKOFF_S`（默认 60s）长退避，绝不快速重试；
网络类仍 3s 重连。成功判定改为解析 JSON 的 `success`，不再用子串匹配。

### 3.2 ZMTP 3.0 订阅握手（照抄 MonitorClient 逐字节）
```
TCP connect engine-ip:6990
recv 10B  ff 00×7 01 7f                  # 服务器 greeting：NULL|ZMTP3.0|127 octets
send 10B  ff 00×7 01 7f                  # 客户端 greeting（同值）
send 54B  03 00 "NULL" + 48×00           # NULL 命令帧——必须整 54 字节，长度差服务器直接 FIN
send 30B  04 19 05 "READY" 0b "Socket-Type" 00 00 00 03 "SUB" 00 01 01   # 订阅者身份
→ 服务器立即开始全量推送（不按 topic 过滤，所有地图所有车一起推）
```
8790（告警通道，rcs-ip）握手完全相同，连上后先回放活动告警再收新告警。
greeting 用 `_recv_exact()` 循环读满 10 字节——TCP 是字节流，单次 `recv` 可能短读。

### 3.3 帧格式（无加密，载荷就是可读 XML）
```
偏移  长度  含义
0     1     魔数 0x02
1     6     恒 0x00
7     1     类别字节（优先级，未深究；语义以 XML <Type> 为准）
8     5     （序号/窗口，未深究）
13    3     u24le = XML 字节数+1（含尾 NUL）
16    65    0x00 填充                     # 帧头共 81 字节
81    N     UTF-8 XML，\0 结尾
```
解析器用 `<?xml` 起点 + NUL 终点的宽松扫帧（`iter_msgs`），不依赖帧头长度字段。
单帧解析异常只跳过该帧并打印，**不会打死订阅线程**（否则全站静默无数据）。

### 3.4 消息类型 → 事件（`parse_frame`）
| RCS Type | 事件 | 内容 |
|---|---|---|
| ROBOT_STATUS | `status` | 每车：Id/Pos(x,y mm)/Direction/Battery/Speed/Status/Stop/Remove/**Pod(货架)**… 另有 `online`（推送在推＝在线） |
| ROBOT_PATH | `path` | 每车剩余轨迹 `<Path x y th/>` 序列 |
| ROBOT_OFFLINE | `offline` | 掉线车号列表 |
| AlarmMessage | `alarm` | MainType/SubType/AlarmStatus(1=告警 0=恢复)/Level/Source/Time/Map/Guid |

货架配对是本项目踩过的坑：推送 XML 里 `<Pod>` 是 `<Robot>` 的**兄弟节点**（REST 把它
拍平进了每车 JSON），必须用组合正则 `<Robot>(.*?)</Robot>\s*(?:<Pod>(.*?)</Pod>)?`
按顺序配对，全局只取第一个 Pod 会把货架串给错误的车。

`ROBOT_OFFLINE` 会持续把**所有**离线车号重报一遍（实测 4 个编号，每个约 5 次/秒）。这些车不在
本图在册列表里，前端按车号查不到即忽略，不会造成失联标记误闪。

## 4. 显示与动画算法（index.html）

官方客户端观感好的本质＝帧密，不是算法强。本项目动画骨架在轮询时代定型，在 5Hz 下依然
适用，**不做重构**：这一轮只加了节奏自适应（§4.2）与「页面挂起后的收敛」（§4.3）。

### 4.1 运动模型：导航式弧长进度（map matching + dead reckoning）
参照高德/腾讯导航小车的做法（前两版"自由坐标+事后校斜"已被否决）：
- 车辆不是自由坐标点，而是轨迹折线 `a.poly` 上的**弧长进度 s**；位置=`posAt(s)`，
  车头=段切向量。上报坐标只做两件事：初始落位投影 + 推进目标 `sEnd`。
- **速度绝不用上报 speed 字段**（粒度粗、常报 0，不可信）：每帧
  `vCyc=(sEnd-s)/(实测帧间隔 EMA×0.8)`——起步/到点/转弯真实减速→两帧位移小→vCyc 自动小。
- 折线净化：斜段沿地图路网 BFS 最短路贴路（`graphRoute`），反折消除、去重——**线绝不
  斜穿空白**是用户底线；断流时 RCS 推过期起点会斜穿，靠这层拦。
  这条底线成立的前提是「路网是直角边」：四图 2779 条边实测**无对角边**
  （仅有 ≤1cm 的坐标取整/对齐噪声），而判定斜段的阈值是 `0.12m`，正好容忍该噪声。
- `sEnd=max(sEnd,s)` 只增不减：结构上永不倒走；上报位落在车头之后→原地停等（防迟到帧拽车）。
- 转弯动画：拐向>45° 的顶点，s 冻结 0.35s、车头以 9rad/s 原地转向后再走下一段。
- 进弯/到终点前 0.5m 线性收油到 cap（90°=0.25m/s、掉头=0.1）。

### 4.2 为 5Hz 推送做的节奏自适应
引擎里两处原按 5000ms 周期设计的常数改从**实测帧间隔 EMA**（`a._gap`，ingest 里更新）
取值：`vCyc` 分母、迟到追帧窗口。逻辑不变，只是时间预算随推送节奏缩放——退回 5s 轮询
旧行为仍正确（`_dev/_live_test.js` 用 5s 间隔快照跑通旧断言即为证明）。

### 4.3 页面挂起后的恢复：就地收敛，绝不补帧冲刺
浏览器会把后台标签的 `requestAnimationFrame` 挂起/节流，但 SSE 仍在投递、`ingest` 仍
在把 `sEnd` 推向最新上报位置。挂起 40s、车走 40m，`s` 与 `sEnd` 之间就积压 40m；回前台
若照常推进，动画会拿这 40m 的「残留进度」在几十帧内快速连续渲染——AGV 沿轨迹加速冲刺
（≈2m/s），观感就是「飞」。修复分三层：

| 位置 | 措施 |
|---|---|
| `stepAgv` / `resyncAgv` | 逐车推进与渲染解耦成纯函数。`frame` 检测到帧间隔 `>RESUME_GAP_S(0.5s)`（或积压 `>RESYNC_DIST(8m)`）即判定「页面曾挂起」，本帧对每车调用 `resyncAgv`：位置落到最新上报点、清空 `s`/`sEnd` 积压、按最新 path 重锚折线——一次就地收敛，不补跑走过的路 |
| `ingest` | 上报间隔 `>SUSPEND_GAP_MS(1.5s)` 视为推送长断：该间隔**不进** `_gap` EMA，按标称节奏计 `vCyc`。否则周期被记成几十秒 → 动画先僵住、恢复瞬间拿错误预算冲刺 |
| 稳态 | 无挂起时行为与修改前逐位一致（`_dev/_resume_test.js` 断言 6s 位移与滞后在修复前后完全相同、60s 长跑滞后不发散） |

代价是恢复瞬间车标会直接出现在当前真实位置（约一帧的位移）——挂起期间车确实在走，
这是唯一诚实的画法；关键是它不再被渲染成一段持续数秒的追赶动画。

### 4.4 事件流与过滤
- `handle()` 入口先按**当前地图**过滤：跨图车辆、跨图告警直接丢弃（非本图内容＝噪声）。
- 状态帧带空 path 时沿用上拍 ROBOT_PATH（推送里两型分开到达）。
- 失联判定：ROBOT_OFFLINE 事件置离线，或 8s 无 status；车标灰显呼吸 + 徽标「·失联」，
  位置冻结在最后可信点。SSE 断开→顶栏显示「断线」，由浏览器 `EventSource` 自行重连（2s 后重试）。
- 告警四道闸门（`_dev/_alarm_test.js` 断言），过不了＝噪声直接丢弃：
  ① 必须在当前地图 ② 归属必须是纯数字车号（AlarmSource/AlarmParam1；SN 序列码如
  `1A04…_ECS`、平台级告警不配占面板——**没有现成 SN↔编号映射接口**，dps/CMS 均未找到）
  ③ 字典必须能译出中文 ④ 超过 24h 的未恢复告警不显示（8790 每次重连回放 RCS 全部 active
  告警，几个月的僵尸告警会混进来）。
  显示规则：**每台车最近 2 条**，恢复(AlarmStatus=0)即移除；恢复消息按 guid 清对应活动项。

### 4.5 交互与渲染细节
- 视角：**不设边界锁**——滚轮缩放无上下限、拖拽可以把地图移出屏幕，方便把任意角落拖到视野中央。
  （曾加过"缩放限位 + `clampView()` 视角回收"来防丢图，实测反而挡住看边角：想看清某个角落时必须
  能把地图拖过去，加了限位就移不动了，已整体撤销。要回到全图视图，切一次地图或缩放一次窗口即可。）
- 拖拽挂在 `window` 上并用 clientX/Y，指针移出画布再回来不会跳变；拖动位移 >5px 不算点击；
  点列表里的车会把它居中（同样不做边界回收，边角的车也能居中）。
- 画布 DPR 每次 `resize()` 重取（浏览器缩放/换高分屏不会变糊）；车形圆角有
  `roundRect` 缺失时的降级实现。
- 车形参照 Q2-400D 实车 780×545mm，随地图缩放。

## 5. 文件清单

```
config.py          唯一配置源
server.py          ZMTP 代理后端（订阅/SSE/配置下发/地图同步/静态服务/访问门禁）
rcs_push.py        RCS 私有通道客户端与解析器（也可当 CLI 用）
index.html         前端全部（事件处理 + 动画引擎 + canvas 渲染）
parse_map.py       地图拓扑 XML → maps/<图名>.json
status.js          机器人状态值字典（168 条）
alarm.js           机器人告警码字典（17 MainType / 643 SubType）
maps/              EE/BB/CC/DD 四图 JSON：节点 576/389/661/1065，边 602/428/677/1072
snap_tmp.json      现场推送快照样本（_dev/_boot_test.js 拿它当真实帧喂给页面脚本）
_dev/              开发期脚本、取证文档与审核报告，见 §5.1
*.bak-0912         本轮改动前的原件备份（回滚直接覆盖回去）
```

| 文件 | 职责 |
|---|---|
| `config.py` | **唯一配置源**：RCS IP/端口、账号密码、地图清单与中文名、默认图、部署开关；`client_config()` 供 `/api/config` 下发 |
| `server.py` | 订阅线程 + SSE 广播 + 配置下发 + 手动地图同步（带节流）+ 静态服务（后缀白名单）+ 访问门禁 + 快照端点 |
| `rcs_push.py` | 登录（失败长退避防锁号）/ZMTP 握手/扫帧/解析；CLI：`python rcs_push.py CC` 输出 JSONL |
| `index.html` | 配置拉取 + 事件处理 + 弧长动画引擎（含挂起收敛）+ 渲染 + 交互 |
| `parse_map.py` | 地图拓扑 XML → `maps/<图名>.json`；CLI 读 `config.MAP_XML_DIR` 下的 `地图-*.xml` |
| `status.js` / `alarm.js` | 状态值与告警码中文字典（出自海康告警信息表 xlsx） |
| `maps/` | 四图 JSON（⟳同步地图按钮从 CMS 拉取更新，原子替换写入） |

### 5.1 测试与开发脚本（全部可随时重跑）

| 脚本 | 覆盖 |
|---|---|
| `python _dev/_run_all.py` | **一键跑完下面全部离线用例**（不连 RCS） |
| `python _dev/_config_test.py` | 配置集中化（环境字面量只允许出现在 `config.py`）、各模块与 `/api/config` 同源、前端不再内置地图清单、访问控制规则 |
| `node _dev/_nav_test.js` | 动画引擎 12 组纯逻辑用例（反折消除/贴路/离路钳制/起步/追帧/滞后停等/限速/连续性/DR 延展/车头仲裁/清线/多周期单调） |
| `node _dev/_resume_test.js` | 后台切回前台不补帧冲刺：修复前后对照 + 稳态回归 + 节奏 EMA 防污染 + 60s 长跑不发散 |
| `node _dev/_boot_test.js` | 启动链路无头集成：最小 DOM 桩真跑页面脚本，配置→地图→SSE→真实 status 帧→列表→渲染（含挂起恢复帧）不抛异常 |
| `node _dev/_alarm_test.js` | 告警四道闸门去留断言 |
| `node _dev/_live_test.js` | 现场真实数据端到端（需 server 在跑）：贴路/无瞬移/单调追帧 |
| `python _dev/gate_check.py [地图] [端口]` | 从运行中的 SSE 采样告警，按四道闸门模拟去留（需 server 在跑，默认取 `config.DEFAULT_MAP` / `WEB_PORT`） |
| `python _dev/_verify.py` | RCS 未公开接口取证（**会真的连 CMS**，仅排障时跑） |
| `_dev/*.md` / `_dev/*.html` | 未公开接口补充文档、动画引擎审核报告、本轮全面审核报告（协议分析报告在 `_dev/`） |

动画相关测试一律**从 `index.html` 实时抽取函数**再 eval，常量也从源码读出，禁止另抄一份
——改实现即改测试，杜绝测试与实现脱钩。

UI 只有 ⟳同步地图 一个按钮（"暂停查询"在推送架构下无意义，地图更新本就该人工控制）。

## 6. 已知边界 / 后续方向

0. **`config.py` 含明文账号密码**：静态服务只放行白名单后缀
   （`.html/.htm/.js/.json/.css/.png/.svg/.ico/.jpg/.woff2`），`server.py`/`rcs_push.py`/
   `*.bak-*`/`*.md`/`config.py` 一律 404，避免源码与备份被同网段直接下载。
   新增前端资源类型时记得同步 `server.py` 的 `STATIC_EXT`。
1. **白名单 TTL 未测定**：IP 登记一次能用多久未知；`iter_msgs` 的兜底是 15s 收不到数据
   → 重登录重连，实测够用。
2. SN 序列号→车号映射接口未找到；若 CMS 设备档案页有，`/api/events` 加一个查表即可。
3. 8790 每次重连回放全部活动告警：guid 去重已处理，量大时首屏略重。
4. 任务通道（AMQP 5672 `exchangeMsg`、6989 DEALER）未接入——当前页面不需要任务回执。
5. **官方"区域信息"面板未复刻**：区域数据不在 elcMap 地图 XML、不在 6990 推送流、不在
   8083 dps；客户端二进制显示其经 8182 hikRpcService 类通道下发，但该端口 IP 白名单卡死
   本机（"IP不在允许名单中"）→ 拿不到。已决定不显示。
6. **视觉验证待人工**：本项目未配置浏览器自动化（需额外下载约 500MB Chromium），
   逻辑正确性由 §5.1 的 5 组离线用例 + 真实数据端到端覆盖，页面观感请在本地重编后截图复核。
7. 静态资源未做 gzip/合并：四图 JSON 合计约 100KB，局域网首屏无感；若将来走广域网再说。
