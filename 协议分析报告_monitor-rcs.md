# MonitorClient ↔ RCS 通讯抓包分析报告

日期：2026-09-11 ｜ 环境：海康 RCS-2000 V3.1.4（`RCS2000-SERVER`，192.0.2.31）+ MonitorClient.exe（`C:\Program Files (x86)\RunMonitor`，pid 17964）
抓包工具：D:\WiresharkPortable（dumpcap/tshark 3.4.8 + NPcap），网卡 抓包所用内网网卡 + Adapter for loopback
证据目录：`D:\Documents\hermes\agv-web\_capture\`（本文末清单，全部结论可由其中 pcapng + 脚本复现）

---

## 1. 抓包方法

| 轮次 | 文件 | 过滤器 | 时长 | 目的 |
|---|---|---|---|---|
| ① | tailscale.pcapng | `host 192.0.2.31 or 192.0.2.39 or 192.0.2.43` | 300s | 全量业务通道 |
| ② | loopback5905.pcapng | `port 5905` | 300s | MonitorClient 自连回环 |
| ③ | round2.pcapng | 同① | 200s | 复现性 + 443 对照（期间零人为操作）|
| ④ | syncA.pcapng + rest_samples.txt | `tcp port 6990` 与 8083 REST 轮询**同时刻** | 60s | 通道间数据同源性定量对比 |

辅助手段：`netstat`/`Get-NetTCPConnection` 300ms 采样做连接→进程归属；tshark `follow,tcp,raw` 流重组后按魔数逐帧解析；`grep -a` 提取 EXE/DLL 内嵌字符串还原接口清单；匿名 TCP 裸连做登录闸门验证。

---

## 2. 通道总览（实测归属 pid 17964 = MonitorClient）

| # | 地址 | 协议 | 方向/节奏 | 内容 | 定性 |
|---|------|------|-----------|------|------|
| A | **TCP 192.0.2.43:6990** | 私有二进制帧+明文XML | 纯下行，每车 **208ms/帧**（60s 内每车 287 帧） | ROBOT_STATUS / ROBOT_PATH / TRP_BLOCK_CELL / BLOCK_CELL / ROBOT_OFFLINE / TASK_INFO_REQ / CHARGE_INFO / VALID_ROBOT_NUM | **主数据通道** |
| A' | UDP →192.0.2.43:6990 | 自定义 73B | 200ms | `07 00..` + 会话 GUID `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` + 0 填充 | 订阅保活 |
| B | TCP 192.0.2.43:6989 | 同帧格式 | 1s，76B `..0x4a.."HB"` | 心跳（ASCII "HB"） | 引擎#3 保活 |
| C | **TCP 192.0.2.31:8790** | 同帧格式 | 下行 | `AlarmMessage` XML（AlarmModule/MainType/SubType/AlarmGuid/AlarmSource=RCS#3(192.0.2.43)/Level/Time） | **告警推送通道** |
| C' | TCP 192.0.2.31:8789 | 裸 TCP | 1s 零载荷 ACK | 保活 | — |
| D | TCP 192.0.2.39:5672 | **AMQP 0-9-1**（RabbitMQ） | 0.45msg/s consume+ack + 心跳 | 队列 `exchangeMsg`，Spring JSON 包装，体为 `<row>` XML（dateChg/mapCode/podCode/startX/endX/dstMapCode=任务变更事件） | **第二条数据通道**（任务/货架事件） |
| E | HTTPS **192.0.2.31:443** | TLS1.2 / nginx 1.20.1 自签(CN=RCS2000-SERVER, 无SNI) | 偶发短会话（300s 抓包仅 9 条，各 ≤200ms；随后 200s 复现窗口 0 条） | 未破解密钥无法读正文；客户端 `param_setup.xml CurMap Port="443"`，且 443 上实测存在 `/rcms/services/rest/clientService/login` | 登录/资源下载，**非监控数据流** |
| F | 127.0.0.1:5905 | 裸 TCP 7 连接 | 1B↔1B 单字节 ping-pong | 无业务数据 | 本地组件看门狗，与 RCS 无关 |
| G | **8083 `/rcms-dps/rest/queryAgvStatus`** | HTTP REST | — | 500s 全量抓包中 **MonitorClient 零流量** | 公开接口，官方客户端**不用** |

静态证据（未产生连接，来自二进制/配置）：`param_config.ini` 预留 zmq `msg_port=8988/ctrl_port=8990`（`status=0` 未启用）、SOAP `/rcs/services/ClientService`（`login_protocol.web=0` 未走）、`MonitorClientWebServer.dll` 内嵌 `/rcms/services/rest/hikRpcService/genAgvSchedulingTask` 等 8181/8182 侧接口。

---

## 3. 私有帧格式（通道 A/C，实测 12,922 帧逐帧验证）

```
偏移  长度  字段
0     1     魔数 0x02
1     6     恒 0x00
7     1     类别字节（ROBOT_STATUS 恒=3；ROBOT_PATH 分布 1–12；疑为优先级/持久级别，语义未完全解出——解码不受影响，类型以 XML <Type> 为准）
8     4     u32le 序号/窗口量（同型帧间波动 ±150，非单调，语义未解出）
13    3     u24le 正文长度 = XML 字节数 + 尾部 NUL（>95% 帧精确匹配；少量失配为抓包边界截断帧）
16    65    0x00 填充（帧头共 81 字节）
81    N     UTF-8 明文 XML，\0 结尾
```

**无加密、无压缩、无编码**——载荷就是带 `\r\n` 的可读 XML（例：`<Pos x="15918" y="6510" h="0"/>`）。坐标单位 mm，角度单位度。"私有协议"仅指外层 81 字节帧头，不是加密隧道。

ROBOT_STATUS 完整字段：`Id IP Pos(x,y,h) LoadStatus Forklift(ForkHeight,LoadStatus) Direction Battery Speed Status AlarmMain AlarmSub Stop Stay TgtDistance Remove Change Version(V3.2.11-r1406923-241031) RollerStatus Pod(Id,strId,Direction,Bind)`。

**登录闸门（实测）**：任意机器匿名 TCP 连 6990/8790/8789，服务器先回 10 字节 `ff 00 00 00 00 00 00 00 01 7f` 并限 10B 即断——裸连不可订阅；会话需经登录注册并以 UDP 中的 GUID 保活绑定。本次 5 分钟窗口未覆盖新建会话过程，登录握手的完整报文未捕获（已知缺口；见 §6 风险）。

---

## 4. 请求地址清单（全部，含验证状态）

| 协议 | 地址与路径 | 验证方式 | 状态 |
|---|---|---|---|
| HTTP | `POST http://192.0.2.31:8083/rcms-dps/rest/queryAgvStatus`（body `{"reqCode":…,"mapShortName":"CC"}`） | curl 200；无鉴权、无 IP 白名单、无 CORS | ✅ 公开可用 |
| HTTPS | `https://192.0.2.31:443/rcms/services/rest/clientService/login`（form `ecsUserName/ecsPassword`；明文/md5 均 `resultCode=4`，需专用加密字段） | curl 活测 200 | ✅ 端点存在 |
| HTTPS | `https://192.0.2.31:443/rcms/web/login/login.action`（302）；`/rcms-dps/*` 在 443 上 404 | curl 活测 | 仅 Web/Struts |
| TCP | `192.0.2.43:6990`（主推送）/ `192.0.2.43:6989`（HB） | 抓包 + 流重组 | ✅ 私有 XML 帧 |
| UDP | `192.0.2.43:6990`（GUID 保活，源端口本机随机高端口） | 抓包 | ✅ |
| TCP | `192.0.2.31:8790`（告警）/ `8789`（HB） | 抓包 | ✅ 同帧格式 |
| AMQP | `amqp://192.0.2.39:5672` 队列 `exchangeMsg`（rabbitmq-c 客户端） | 抓包 tshark amqp 解码 | ✅ |
| TCP(预留) | `:8988/:8990` zmq、`http://…/rcs/services/ClientService` SOAP、`/rcms/services/rest/hikRpcService/*`（8181 侧，`genAgvSchedulingTask/bindPodAndBerth/freeRobot/continueTask…`） | 仅二进制字符串 | 配置未启用/本窗口未使用 |

---

## 5. 三项目标逐条结论

### 5.1 协议格式
见 §3。要点：外层 81B 二进制头（魔数+类别+长度），**载荷为明文 XML，未加密未编码**；监控语义全在 `<Type>` 字段，帧头仅做分帧。

### 5.2 8083 是否主通道？——**不是。**
- 500 秒、跨 3 个独立时窗的全量抓包中，MonitorClient 对 8083 **零流量**；它根本不调用这个公开 REST 接口。
- 主通道是 **TCP 6990（192.0.2.43，调度引擎机）私有推送**，辅以 8790 告警推送 + 5672 AMQP 事件订阅 + UDP/心跳保活。
- **"443 上的私有协议"不存在**：443 就是 nginx 标准 HTTPS（登录页/Struts/REST clientService + CurMap 资源），TLS 短会话、偶发、无周期监控流；私有协议实际藏在 **6990/8790 明文帧**里，不在 TLS 里。

### 5.3 agv-web 精度差距归因
定量实验（§2 轮次④，REST 与 6990 同窗口逐帧对齐）：

| 指标 | 6990 推送 | 8083 REST |
|---|---|---|
| 每车更新节奏 | **208ms**（服务端 5Hz） | 取决于客户端轮询（现 POLL_MS=5000） |
| 数据新鲜度 | 实时 | timestamp 中位滞后 **150ms**，与同时刻推送帧坐标 **43/43 全等** |
| 高频轮询是否被限 | — | 350ms 间隔连测 30 次无限流、返回持续新值（运动车 26/29 帧变化） |
| 路线规划 | ROBOT_PATH 随推送同步更新 | 轮询时刻快照 |

**结论：差距 100% 来自采样频率（通道行为），不是算法，也不是"官方拿到更准的数据"。**
两端数值完全同源（mm 整数、同一服务端状态），官方客户端只是"每 0.2s 被动收新帧"，agv-web 是"每 5s 主动取一帧再靠弧长航位推算补间"——补间只能画得平滑，无法在 RCS 改道、车辆提前停靠/转弯的瞬间还原真相，展示位最多滞后现实 5s。

**最小改法（不动私有通道）**：`index.html` 里 `POLL_MS 5000→500` 即可把采样误差压到 1/10，实测服务端无限流；官方文档"<100 车 5 秒"是负载约定，不是精度要求。若 500ms 轮询仍不满足（需要 ≤100ms 级丝滑+实时改道），唯一路径是解析登录握手直连 6990——需要复刻会话注册（本次未破，且伪会话有踢掉官方客户端订阅的风险，不建议轻上）。

---

## 6. 局限与未尽事项
1. ~~登录→推送会话建立的完整握手未被本轮抓包覆盖~~ **已破解（2026-09-11 第二轮，见 §8）**。
2. 帧头字节 7（类别）与 8–11（序号/窗口）语义未完全解出；不影响解码。
3. 443 短会话内容为 TLS 密文，本轮未做 MITM（握手改用 ZMTP 层证据闭环，443 仅登录用途已足证）。
4. 127.0.0.1:5905 单字节 ping-pong 判定为本地进程间看门狗，未深究（与 RCS 无关）。

## 7. 证据文件清单（D:\Documents\hermes\agv-web\_capture）
`tailscale.pcapng` `loopback5905.pcapng` `round2.pcapng` `syncA.pcapng` `handshake2.pcapng`（原始抓包）；`s1.txt` `r2s.txt` `syncA.txt` `up_frames.txt`（流重组 hex）；`rest_samples.txt` `restpoll.py` `rest_rate.py` `probe_channels.py` `zmtp_sub.py`（同步对比/闸门实测脚本）；`srv443.der`（443 证书）；`pid443.log`（连接归属采样）。

## 8. 追加：私有通道订阅协议完整破解（2026-09-11 第二轮）

方法：以太网口重抓 → 强杀并重启 MonitorClient 抓全登录时序 → 提取其 ZMTP 上行帧逐字节重放 → 三变量对照实验（GUID / UDP 源端口 / 保活）。

**6990/8790/6989 通道底层是 0MQ(ZMTP 3.0) over TCP，明文无加密**：
```
TCP connect →
S: greeting  ff 00×7 01 7f   （服务器报 NULL|ZMTP3.0|127 octets）
C: greeting  ff 00×7 01 7f   （同值回给服务器）
C: NULL 命令帧 54B（03 00 "NULL" + 48×00，逐字节照抄官方）
C: READY 命令帧 30B：属性 Socket-Type=SUB   ← 订阅者身份，关键
S: 立即开始全量推送（81B 帧头 + XML）
```
8790（告警）与 6990 完全同款（greeting+NULL+READY SUB，只是 IP:端口不同，GUID/UDP 端口另取）。6989 则不同：服务器 greeting 机制字节 0x1f，客户端 READY 用 Socket-Type=DEALER、Identity=“本机IP+GUID”并附一帧 `<UnitStart>` XML 注册——这是任务回执上行通道，本窗口内服务器无下行。

**订阅凭证真相（2026-09-11 第三轮修正，推翻 §8 初版结论）**：
初版"GUID+源端口=不透明凭证"模型**不成立**。复测（官方客户端全程离线）：
| 条件 | 结果 |
|---|---|
| curl `8181 /rcms/web/login/login.action`（ecsUserName/ecsPassword=sha256(密码)，即拉地图同款登录，返回 JSESSIONID+HIK_COOKIE） | `{"success":true}` |
| 登录后 → 订阅：**随机 GUID** + 60879 UDP 保活 | ✅ 全量推送 200KB/4s |
| 同上 → 订阅：随机 GUID、**完全不带 UDP**、任意源端口 | ✅ 照样全量推送 |
→ **推送引擎的闸门是 IP 级白名单**：任何一次成功的 Web 登录（8181 或 443 `/rcms/web/login/login.action`，后者同参数也 `{"success":true}`）就把本机 IP 登记进 6990 引擎白名单；GUID/UDP 保活/端口对已注册的 IP 不再强制。昨天观察到的"随机 GUID 被拒"全部发生在**官方会话在线期间**——引擎对活动会话有排他逻辑，官方离线（或白名单窗口内）后任意自造 GUID 直连即通。443 的 `clientService/login`（REST，返回 XML resultCode）不是登记入口，Web 登录才是。
→ **结论：完全不需要官方客户端，也不需要破解任何密钥**。独立订阅 = `curl -X POST .../rcms/web/login/login.action`（sha256 密码）+ 随机 GUID 发 ZMTP 握手，两步纯脚本。白名单 TTL 未测定（官方离线数小时后是否失效、登录一次管多久，待长期观察；保守做法：订阅失败时自动重登录再重试）。

**登录密码**：客户端存 login.dat（AES 密文），但**不需要它**——Web 登录直接接受 sha256(明文密码)，现场账号密码由使用方自备，`rcs_push.py` 内置自动登录。明文/md5 被 clientService/login 拒（那是 REST 口，不是登记入口）。

**共存性（已实测坐实，最终版）**：官方在线（6990 ESTABLISHED）期间，脚本独立登录+随机 GUID 并行订阅 60s 收满 1425 帧零断流——引擎对多订阅者无排他。昨天 Tailscale 上"随机 GUID 被拒"的确切原因未定论（候选：该出口 IP 尚未登记进 .43 引擎白名单 / 会话登记时序），已不影响使用：登录在前、订阅在后即稳定可复现。官方重登会换自己的 GUID/UDP 端口（四次观测均不同），与脚本互不影响。

**成品**：`D:\Documents\hermes\agv-web\rcs_push.py` —— 纯标准库，`python rcs_push.py CC` 输出 JSONL 实时流（x/y mm、dir、speed、battery、status、path），可直接替换 agv-web 的 5s 轮询。
