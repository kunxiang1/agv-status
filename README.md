# AGV 实时监控（海康 RCS-2000）

纯 Python 标准库 + 原生 HTML/JS，无任何第三方依赖、无框架、无构建步骤。
直连 RCS **私有推送通道**（官方 MonitorClient 同款数据源），实测全厂 12 车 × 4 图，
每车约 208ms 一帧（≈5Hz），与官方客户端并行共存、互不影响。

## 快速开始

```bash
# 1. 配置现场 RCS 环境（必填，全部走环境变量，代码里零凭据）
export RCS_WEB=http://<CMS主机>:8181    # Web CMS 地址（登录/拉地图）
export RCS_ENGINE=<调度引擎IP>          # 6990 推送引擎主机
export RCS_USER=<用户名>
export RCS_PWD=<密码明文>               # 程序内只用于 sha256，不落盘

# 可选
export MAP_CODES=EE,BB,CC,DD           # 同步地图按钮拉取的地图编号，逗号分隔

# 2. 启动（默认端口 8899）
python server.py

# 3. 打开 http://127.0.0.1:8899/?map=<地图简称>
```

Windows 下用 `set RCS_WEB=...` 即可。页面左上角 ⟳同步地图 按钮从 CMS 拉取
`maps/<简称>.json`；也可以直接把官方 CMS 导出的拓扑 XML 用
`python parse_map.py 地图-xxx.xml` 离线转成 JSON 放进 `maps/`。

> **安全说明**：本项目仅为只读监控。密码只在登录请求内做 sha256 后发送
> （RCS Web 登录要求），不出现在 6990/8790 推送通道。请给监控专用账号，
> 别用 admin。连续错密码会锁定 RCS 账号（实测），凭据只配一次、勿循环重试。

## 前提条件（RCS 侧要满足什么）

1. 运行 server.py 的机器与 RCS 引擎网络可达：`CMS:8181`（HTTP）、
   `引擎IP:6990`、`门户机IP:8790`（TCP）。
2. 一个能登录 Web CMS（8181）的账号——登录动作本身就是推送订阅的准入：
   引擎白名单是 **IP 级**，登录一次即把本机 IP 登记进去（详见 §3.1）。
3. `maps/` 里有地图 JSON（现场 4 图示例已随仓库提供，换成你自己的图即可）。

## 1. 为什么不是轮询

项目最初用公开接口 `POST :8083/rcms-dps/rest/queryAgvStatus`（5 秒轮询）。
抓包分析（见根目录《协议分析报告_monitor-rcs.md》）证实：官方 MonitorClient
**根本不调用这个 REST 接口**，它走的是 6990/8790 端口的 ZMTP 推送通道；REST 的 5s 粒度
是本项目动画"不如官方跟手"的唯一原因——两端返回的坐标数值逐帧比对完全相等。
因此本项目改为直连推送通道，精度与官方对齐（208ms），REST 通道已彻底移除。

## 2. 架构

```
海康 RCS-2000（现场环境）
 ├─ :8181  Web/Struts     ←── login()：sha256(密码) 表单登录，登记本机 IP 进推送白名单
 ├─ :6990  TCP (调度引擎)  ┐
 ├─ :8790  TCP (门户机)    ┴─ ZMTP 3.0 SUB 订阅 ← rcs_push.py（推送原始帧）
 └─ :8181  地图 XML（base64+gzip）      ← syncMaps()：手动按钮触发

server.py (本机，唯一后端)
 ├─ 2 个订阅线程 sub_loop：RCS 帧 → 解析成事件对象 → 广播
 ├─ GET /api/events   SSE：所有事件实时推给浏览器（多浏览器标签共享同一订阅）
 ├─ POST /api/syncMaps 手动拉取地图 → maps/*.json
 ├─ GET /api/snapshot  当前车辆快照（调试/测试用，不产生对 RCS 的任何请求）
 └─ GET /*             静态文件（index.html / maps / status.js / alarm.js）

index.html (前端)
 EventSource → handle(事件) → ingest(单帧采纳) → 动画引擎(frame/requestAnimationFrame) → canvas
```

要点：**RCS→server 只有一条订阅连接**（不是每浏览器一条）；浏览器↔server 用 SSE；
推送断了 server 自动重登录重连（`rcs_push.iter_msgs` 内建），浏览器再自动重连 SSE。

## 3. 协议实现（rcs_push.py）

### 3.1 接入前提：IP 登记
推送引擎的准入是 **IP 级**：任何一次成功的 Web 登录
（`POST http://<CMS>:8181/rcms/web/login/login.action`，参数
`ecsUserName=<用户>&ecsPassword=<sha256(密码)>&pwdSafeLevelLogin=0`）
就把本机 IP 登记进引擎白名单（实测：登录后随机 GUID、随机 UDP 端口甚至完全不发
保活均可订阅；GUID 不是凭证、密码不进 6990）。`rcs_push.login()` 即此一步。

### 3.2 ZMTP 3.0 订阅握手（照抄 MonitorClient 逐字节）
```
TCP connect <引擎IP>:6990
recv 10B  ff 00×7 01 7f                  # 服务器 greeting：NULL|ZMTP3.0|127 octets
send 10B  ff 00×7 01 7f                  # 客户端 greeting（同值）
send 54B  03 00 "NULL" + 48×00           # NULL 命令帧——必须整 54 字节，长度差服务器直接 FIN
send 30B  04 19 05 "READY" 0b "Socket-Type" 00 00 00 03 "SUB" 00 01 01   # 订阅者身份
→ 服务器立即开始全量推送（不按 topic 过滤，所有地图所有车一起推）
```
8790（告警通道，门户机）握手完全相同，连上后先回放活动告警再收新告警。

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

### 3.4 消息类型 → 事件（`parse_frame`）
| RCS Type | 事件 | 内容 |
|---|---|---|
| ROBOT_STATUS | `status` | 每车：Id/Pos(x,y mm)/Direction/Battery/Speed/Status/Stop/Remove/**Pod(货架)**…（REST 同构字段，前端零改动兼容） |
| ROBOT_PATH | `path` | 每车剩余规划线 `<Path x y th/>` 序列 |
| ROBOT_OFFLINE | `offline` | 掉线车号列表 |
| AlarmMessage | `alarm` | MainType/SubType/AlarmStatus(1=告警 0=恢复)/Level/Source/Time/Map |

货架配对是本项目踩过的坑：推送 XML 里 `<Pod>` 是 `<Robot>` 的**兄弟节点**（REST 把它
拍平进了每车 JSON），必须用组合正则 `<Robot>(.*?)</Robot>\s*(?:<Pod>(.*?)</Pod>)?`
按顺序配对，全局只取第一个 Pod 会把货架串给错误的车。

## 4. 显示与动画算法（index.html）

官方客户端观感好的本质=帧密，不是算法强；本项目动画是轮询时代做的，被证明在 5Hz
推送下依然是上限，**不重构**，只做了节奏自适应（§4.2）。

### 4.1 运动模型：导航式弧长进度（map matching + dead reckoning）
参照高德/腾讯导航小车的做法，第三版（前两版"自由坐标+事后校斜"被用户否决）：
- 车辆不是自由坐标点，而是剩余路线折线 `a.poly` 上的**弧长进度 s**；位置=`posAt(s)`，
  车头=段切向量。上报坐标只做两件事：初始落位投影 + 推进目标 `sEnd`。
- **速度绝不用上报 speed 字段**（粒度粗、常报 0，不可信）：每帧
  `vCyc=(sEnd-s)/(实测帧间隔 EMA×0.8)` —— 起步/到点/转弯真实减速→两帧位移小→vCyc 自动小。
- 折线净化：斜段沿地图路网 BFS 最短路贴路（`graphRoute`），反折消除、去重——**线绝不
  斜穿空白**是底线；断流时 RCS 推过期起点会斜穿，靠这层拦。
- `sEnd=max(sEnd,s)` 只增不减：结构上永不倒走；上报位落在车头之后→原地停等（防迟到帧拽车）。
- 转弯动画：拐向>45° 的顶点，s 冻结 0.35s、车头以 9rad/s 原地转向后再走下一段。
- 进弯/到终点前 0.5m 线性收油到 cap（90°=0.25m/s、掉头=0.1）。

### 4.2 为 5Hz 推送做的唯一改动：节奏自适应
引擎里两处原按 5000ms 周期设计的常数改从**实测帧间隔 EMA**（`a._gap`，ingest 里更新）
取值：`vCyc` 分母、迟到追帧窗口。逻辑不变，只是时间预算随推送节奏缩放——5s 轮询退
回旧行为仍正确（`_dev/_live_test.js` 用 5s 间隔快照跑通旧断言即为证明）。

### 4.3 事件流与过滤
- `handle()` 入口先按 **当前地图** 过滤：跨图车辆、跨图告警直接丢弃（非本图内容=噪声）。
- 状态帧带空 path 时沿用上拍 ROBOT_PATH（推送里两型分开到达）。
- ROBOT_OFFLINE / 8s 无 status → 车标失联；SSE 断开 → 顶栏"断线"，重连由浏览器 EventSource 失败回退 2s 轮询触发。
- 告警：`AlarmModule/MainType/SubType` 查 alarm.js 字典译为中文（如"地码补光不足"）；
  三道闸门（`_dev/_alarm_test.js` 断言），过不了=噪声直接丢弃：① 归属必须是纯数字车号
  （AlarmSource/AlarmParam1；SN 序列码如 `1A04…_ECS`、平台级告警不配占面板——**没有现成
  SN↔编号映射接口**，dps/CMS 均未找到）② 字典必须能译出中文 ③ 超过 24h 的未恢复告警不显示
  （8790 每次重连回放 RCS 全部 active 告警，几个月的僵尸告警会混进来）。
  显示规则：**每台车最近 2 条**，恢复(AlarmStatus=0)即移除；恢复消息按 guid 清对应活动项。

## 5. 集成到自己的系统

三个口子，按需选：

| 方式 | 说明 |
|---|---|
| **SSE `/api/events`** | 推荐。`EventSource("/api/events")`，每事件一行 JSON：`{"e":"status"|"path"|"offline"|"alarm", ...}`（字段见 §3.4）。server 已把 RCS→server 收敛为单条订阅，多消费方共享。 |
| **`GET /api/snapshot`** | 拉平式：当前全部车辆最新状态（REST 同构字段+`online`+`path`），零主动通讯，适合轮询型集成/测试。 |
| **`import rcs_push`** | 不要 server：`rcs_push.iter_msgs(RCS_ENGINE, 6990)` + `parse_frame()` 直接拿事件 dict；CLI `python rcs_push.py <地图简称>` 输出 JSONL 实时流，可管进任何数据管道。 |

地图数据 `maps/*.json`：`{name, qr, nodes:[[id,x米,y米,类型值]], edges:[[a,b]]}`，
前端 `index.html` 里的节点类型字典（`TYPE_COLORS`，17 种）对照现场 CMS 地码类型配置，
换现场时按你的图改字典与 `MAP_CODES` 即可。

## 6. 文件清单

| 文件 | 职责 |
|---|---|
| `server.py` | 订阅线程 + SSE 广播 + 手动地图同步 + 静态服务 + 快照端点（唯一后端入口） |
| `rcs_push.py` | RCS 私有通道客户端：登录/ZMTP 握手/扫帧/解析（可独立当 CLI 用：`python rcs_push.py CC` 输出 JSONL） |
| `index.html` | 前端全部：事件处理 + 弧长动画引擎 + canvas 渲染（车形参照现场车型 780×545mm） |
| `parse_map.py` | 地图拓扑 XML → `maps/<图名>.json`（nodes/edges，坐标米制） |
| `status.js` | 机器人状态值 168 条中文字典（出自海康告警信息表 xlsx） |
| `alarm.js` | 机器人告警码字典：MainType→SubType→名称/等级/含义，643 条（同上表生成） |
| `maps/` | 4 张示例地图 JSON（⟳同步地图按钮可从你的 CMS 拉取覆盖） |
| `_dev/` | 开发期断言与文档：`_nav_test.js` 动画引擎 12 组纯逻辑用例（node 直跑）；`_live_test.js` 真实数据端到端（需 server 在跑）；`_verify.py` CMS 未公开接口验证（需 env，可重跑）；`RCS-2000_未公开接口补充.md` 实测接口文档 |
| `协议分析报告_monitor-rcs.md` | MonitorClient 抓包分析全文（私有通道发现的完整证据链） |

UI 只有 ⟳同步地图 一个按钮（推送架构下"暂停查询"无意义，地图更新本就人工控制）。

## 7. 已知边界 / 后续方向

1. **白名单 TTL 未测定**：IP 登记一次能用多久未知；`iter_msgs` 的兜底是 15s 收不到数据→重登录重连，实测够用。
2. SN 序列号→车号映射接口未找到；若 CMS 设备档案页有，`/api/events` 加一个查表即可。
3. 8790 每次重连回放全部活动告警：guid 去重已处理，量大时首屏略重。
4. 任务通道（AMQP 5672 exchangeMsg、6989 DEALER）未接入——当前页面不需要任务回执。
5. 多浏览器标签共享 server 单订阅；server 单实例即可服务全车间。
6. **官方"区域信息"面板未复刻**：区域数据不在 elcMap 地图 XML、不在 6990 推送流、不在 8083 dps；
   客户端二进制显示其经 8182 hikRpcService 类通道下发，但该端口 IP 白名单卡死本机（"IP不在允许名单中"）
   → 拿不到。本项目决定：不显示，放弃。

## 8. 免责

逆向自现场抓包，接口/帧格式无官方文档背书，海康固件升级可能随时失效；仅用于内部只读监控。
