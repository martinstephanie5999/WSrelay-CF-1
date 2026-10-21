# PROTOCOL.md — 纯中继文件传输协议 v1.5（冻结）

> 状态：**v1.5 冻结**，当前实现的依据。v1.5 在 v1.4 上加入批量/文件夹的可选字段、Origin 校验与密码速率限制。2026-10-21 的安全加固见 §0.1，不改变线协议版本 `1`。
> 房间模型：**一对一双向**。同一房间最多两个稳定客户端；双方都可以发送和接收文件。
> 一对多、多人房间：**不在项目范围内**。
> 传输层：WebSocket（Cloudflare Worker + 单个 Durable Object）。
> 服务端：**不持久化任何文件字节**；只允许保存房间元数据和连接元数据。
> 变更规则：v1.5 冻结后，任何字段/语义变更必须升版本（v2）并写迁移说明。
> 说明：文中“阶段 N”指实现里程碑，用来标记某项能力从哪一版起具备，不代表当前分支的状态。

**v1.5 变更记录**

1. **多文件/文件夹**：`file-offer` 增加可选字段 `path`、`batchId`、`batchIndex`、`batchTotal`。每个文件仍是一次**独立传输**（独立 `fileId`/offset/ACK/`.meta`/续传）；批量只是发送端按路径排序、串行发起 offer 的约定，不是服务端清单。`path` 仅用于展示与下载命名，落盘仍按 `fileId` 命名，杜绝路径穿越。
2. **Origin 校验**：WebSocket 升级请求带 `Origin` 时必须与站点同源；不匹配或 opaque origin（`null`）直接 403。无 `Origin` 的非浏览器客户端放行。
3. **密码速率限制**：10 分钟窗口内失败 8 次后，房间封禁 10 分钟；封禁期间任何尝试都 close `4005`（reason `too_many_attempts`）；正确密码会清零计数。计数存 `room:{roomId}:meta`。
4. `resume` 字段降级为提示：接收端发现本地存在同 `fileId`、同 `expectedSize` 的有效 `.meta` 时**始终**按 `receivedBytes` 续传（阶段 3 起的实现语义）。

**0.1 v1.5 实现加固（2026-10-21，不升线协议版本）**

> 线协议版本仍为 `1`。以下是对 v1.5 的收紧与可选字段补充；旧客户端不带 `relay.v1.<nonce>` 时服务端 `presence.nonce` 为 `null`，旧客户端忽略额外字段即可。新客户端要求服务端回显该子协议，因此客户端与服务端需同批部署。

1. **认证 gate**：每条连接的首帧必须是 `hello`；服务端在密码校验通过前不发送 `presence`、不转发任何帧。非 `hello` 首帧 close `4002`；密码错误/封禁 close `4005`。attachment 增加 `authed`。
2. **auth 不转发**：客户端只在每条连接的首个 `hello` 携带 `auth`；服务端校验后从转发的 hello 中删除 `auth`，对端永远看不到哈希。
3. **服务端帧真实性**：客户端在 WebSocket 子协议中提供 `relay.v1.<nonce>`；服务端在 101 回显该 token，并在发给该连接的每个 `presence` 顶层带 `nonce`。客户端只接受 `nonce` 匹配的 presence。服务端丢弃客户端发来的 `from:"server"` 帧；`peer` 对象不包含对方的 nonce。
4. **认证后再 announce**：`peer-joined` 只在该连接 hello/auth 通过后广播；未认证、4002/4003 被拒连接不参与 presence、不接收转发。接收端只接受 `peerReady` 且 `senderClientId` 匹配当前 peer 的 `file-offer`。
5. **房间 TTL**：密码房间 meta 增加 `expiresAt`；到期连接 close `4004` 并删除 meta；`alarm()` 关闭在线连接并标记 `expired`，7 天后删除。公开房间不写 meta。
6. **密码失败计数串行化**：同一 DO 实例内密码校验串行执行，避免并发 hello 的 read-modify-write 丢失计数。
7. **断点/暂停恢复**：暂停/恢复与对端 pause/resume 唤醒 ACK 等待者；`stale_offset`/`offset_gap` 重新协商时退出旧 pump；新 `epoch` 的 `file-accept` 允许 offset 回退；对端离线超过 3s 的发送任务转为重新协商状态，保留 `fileId`/offset，重连后重发 offer。
8. **接收确认（S12）**：新 offer 先进入本地 `offered` 状态，默认不写 OPFS；用户点“接收”后才准备接收并回 `file-accept`，点“拒绝”回 `file-reject { reason:"user_rejected" }`。等待确认期间重复 offer 回 `file-reject { reason:"receiver_not_ready", retryAfterMs }` 保活，发送端不失败；批次可一次性授权后续同 `batchId`。
9. **本地清理（S13）**：未完成接收从 `.meta` 恢复为 `partial` 行；删除会移除 `.bin` 与两个 meta 槽。清理发送完成行只影响本地 UI，不发送控制帧。

**v1.4 变更记录**

1. **密码房间**：`hello` 增加可选字段 `auth`（密码的 SHA-256 小写十六进制；只在本机计算，不传明文、不进 URL）。
2. **密码只用于进门校验**：DO 在每条连接的**第一条** `hello` 上校验一次；房间已有密码时 `auth` 不匹配 → close `4005`，不转发该帧给对端。房间没有密码时，**只有房间里第一个连接**可以设置密码（避免加入者把公开房间锁死）；设置后写入 `room:{roomId}:meta { passwordHash, createdAt }`（房间元数据，符合不存储边界）。
3. **`4005` 是终止关闭码**：收到后客户端停止自动重连，展示密码输入；只有用户提交新密码才允许新建连接。
4. **二维码只承载房间链接**：密码不进入链接、不进二维码。

**v1.3 变更记录**

1. **自适应窗口**：`windowSize` 允许在传输中由发送端调整，范围 8–16，必须 ≥ `persistEveryChunks`（8）。仅在连续 8 次干净的 ACK 推进后上调（+4，封顶 16）；出现 `offset_gap`、`incomplete` 或 ACK 停滞回退时立即降回 8。
2. **突发上限口径修正**：v1.2 的“>2–4 MiB 突发丢帧”是在一条经过本地代理的路径上测得的，不能当作 Cloudflare 定律。v1.3 把 2 MiB 保留为默认值，把 **4 MiB 定为硬上限**：自适应窗口 16 × 256 KiB = 4 MiB 只允许出现在连续干净 ACK 之后，任何异常立即回退到 2 MiB。平台层仍按“WebSocket 非无损”处理。
3. **`pause` / `resume` 落地**（格式见 §4.7，v1.2 已定义）：双方均可暂停；发送端暂停后停止读取文件，已发出帧仍可能到达；`resume.startOffset` 必须等于接收端 `receivedBytes`，不匹配回 `error { code: "stale_offset" }`，发送端重新走 `file-offer`。
4. **重复 ACK 节流**：接收端对重复帧的应答 ACK 限制为每 250 ms 最多一次；累计 ACK 语义不变。
5. **持久化实现**：接收端在 Worker 中用 `FileSystemSyncAccessHandle` 写入并在持久化边界 `flush()`；`receivedBytes` 仍只表示已 flush 的字节数。

**v1.2 变更记录**

1. **突发上限**：实测 Cloudflare Workers/DO 在客户端一次突发 >2–4 MiB 的 WebSocket 消息时会丢帧；5×1 MiB 只到达 1–2 帧，5×1 MiB 间隔 200ms 才全部到达。冻结 `windowSize * chunkSize <= 2 MiB`，默认 `chunkSize = 256 KiB`、`windowSize = 8`（2 MiB 突发，连续 4 次实测 8/8 到达）。
2. **WebSocket 不是无损传输**：发送端必须在 `offset_gap`、ACK 停滞、`incomplete` 时回退到接收端的 `receivedBytes` 并重传；不能假设一次 `send()` 一定到达。
3. 默认 `chunkSize` 从 1 MiB 改为 256 KiB；`windowSize` 和 `persistEveryChunks` 仍为 8。
4. 接收端遇到未持久化尾部的重复帧时，先丢弃尾部（truncate 到 receivedBytes）再按 offset 处理，避免重传死锁。

**v1.1 变更记录**

1. 新增 `persistEveryChunks <= windowSize` 不变量，消除“窗口满但持久化边界未到”的互等死锁。
2. `.meta` 改为 A/B 双槽 + 版本 + checksum；任何时刻只信任最高版本且 checksum 有效的槽。
3. 写死重复 `file-offer` / 二进制帧 / `ack-query` / `complete` 的幂等处理。
4. 写死 close `4001` 后停止自动重连，避免两个标签页无限互踢。
5. 明确 `receivedBytes` 只表示已持久化字节数；`writtenBytes` 不得用于 ACK 或重连（阶段 3 验收）。

---

## 0. 术语与不变量

| 术语 | 定义 |
|---|---|
| `roomId` | 128 位随机值，base64url 编码，22 字符；不可猜测，不复用。 |
| `clientId` | 客户端稳定标识，随机 UUID；浏览器本地持久化，同一房间内唯一。 |
| `connId` | 单次 WebSocket 连接的随机 UUID；每次连接/重连都重新生成。 |
| `transferId` | 发送端生成的 32 位随机无符号整数；在同一发送端的所有活跃传输中唯一。 |
| `fileId` | 发送端生成的随机 UUID；一次传输内稳定，接收端写入 OPFS `.meta`。**不是内容哈希**。 |
| `senderClientId` | 该传输的发送方稳定标识。 |
| `receiverClientId` | 该传输的接收方稳定标识。 |
| `chunkSize` | 单个二进制帧的负载大小；默认 **256 KiB**，允许 256 KiB–4 MiB。 |
| `windowSize` | 发送端允许的在途分片数；默认 8。 |
| `persistEveryChunks` | 接收端每多少个分片强制建立一次持久化边界并 ACK；默认 8，必须 ≤ `windowSize`。 |
| **突发上限** | 默认 `windowSize * chunkSize <= 2 MiB`；自适应窗口（8–16 片）仅在连续干净 ACK 后允许到 4 MiB；硬上限 4 MiB。 |
| `receivedBytes` | 接收端**已持久化**的累计字节数；**ACK 和断点续传的唯一权威值**。 |
| `writtenBytes` | 接收端已写入 writable stream、但尚未确认持久化的字节数；**不得用于 ACK 或重连**。 |
| `ackedOffset` | 发送端从 ACK 得知的累计值；只是视图，不是权威。 |
| `metaVersion` | 接收端 OPFS `.meta` 的单调递增版本号；每次持久化更新 +1。 |
| `epoch` | 接收端 OPFS `.meta` 所属的会话纪元；用于识别“本地状态已被清空/重建”。 |

**协议不变量**

1. 一个 `transferId` 只对应一个方向：`senderClientId -> receiverClientId`。
2. 只有发送端可以发起 `file-offer`；接收端只能 `file-accept` / `file-reject`。
3. 发送端在收到 `file-accept` 之前，**不得发送任何该传输的二进制帧**。
4. 接收端的 `receivedBytes` 是唯一权威；发送端的 `ackedOffset` 永远不能超过它。
5. 二进制帧只在 WebSocket 连接内有序；控制帧可能和二进制帧交错，但同一连接内保持发送顺序。
6. 每个控制帧都带 `clientId` 和 `connId`；接收方只接受当前对端 `connId` 的帧。
7. 服务端不解析二进制帧；只按连接对端转发。
8. 服务端不持久化文件字节、文件名、文件大小、offset、ACK、哈希。
9. **`persistEveryChunks <= windowSize`**。发送端窗口填满时，接收端必须已经在同一窗口内建立过持久化边界并发出 ACK；不得出现“窗口满了但持久化边界还没到”的互等。
10. **`receivedBytes` 只表示已确认持久化的字节数**。`writtenBytes` 只能存在内存中，不得写入 `.meta`、不得用于 ACK、不得用于重连。
11. **重复控制帧必须幂等**：重复 `file-offer` 回当前 `file-accept`；重复 `ack-query` 回当前 `ack`；重复 `complete` 回 `complete-ack`；重复 `ack`/`complete-ack` 只做单调推进或忽略。
12. **close 4001 是终止状态**：收到后停止自动重连，清理连接状态；只有用户主动刷新/重新加入才允许新建连接。
13. **`.meta` 必须原子更新**：使用 A/B 双槽 + `metaVersion` + checksum；只接受更高版本且 checksum 有效的槽；写入失败回退到上一个有效槽。
14. **突发上限**：默认 `windowSize * chunkSize <= 2 MiB`；自适应窗口 8–16 时允许到 4 MiB（硬上限），任何 gap/stall/回退后立即降回 2 MiB。v1.2 的 2 MiB 证据来自代理路径（`RESEARCH.md` §7.2），不作为平台定律。
15. **WebSocket 不是无损传输**：发送端必须在 `offset_gap`、ACK 停滞（`ack-query` 后仍无进展）、`incomplete` 时，把 `nextOffset` 回退到接收端的 `receivedBytes`/`ackedOffset` 并重传；不得假设 `send()` 一定送达。
16. **接收端未持久化尾部的处理**：收到 `offset < writtenBytes` 且 `offset >= receivedBytes` 的重传帧时，先 `truncate(receivedBytes)` 丢弃未持久化尾部，再按 offset 规则处理；否则重传会被当成重复帧丢弃，形成死锁。

---

## 1. 房间模型

### 1.1 一对一双向

- 一个房间最多两个稳定客户端：`A` 和 `B`。
- `A` 可以给 `B` 发文件，`B` 也可以给 `A` 发文件；两个方向独立。
- 每个方向有自己的 `transferId`、窗口、ACK、offset。
- 同一时刻允许每个方向有多个传输（不同 `fileId`），但阶段 1 只实现每个方向一个活动传输。
- 第三个连接的行为：`room-full`，以 close code `4003` 关闭；不踢掉已有连接。

### 1.2 房间创建与加入

- 房间由第一个 WebSocket 连接隐式创建：`GET /ws/{roomId}?...`。
- `roomId` 由创建方生成（128 位随机），分享链接只包含 `roomId`。
- 阶段 1 不做密码；阶段 5 的密码哈希属于**房间元数据**，允许存 DO storage，但不得与文件数据混用。
- 房间过期时间写入 DO storage 的 `room:{roomId}:meta`；到期连接 close `4004` 并删除元数据。默认 24 小时（`ROOM_TTL_MS`），实现见 §0.1 第 5 条。

### 1.3 连接 URL

```text
wss://{host}/ws/{roomId}?clientId={uuid}&connId={uuid}&v=1
```

- `clientId`：稳定 UUID，浏览器 `localStorage` 持久化。
- `connId`：每次连接新生成。
- `v`：协议版本；不匹配时 close `4002`。
- DO 在 upgrade 时读取这三个参数，写入 `serializeAttachment({ clientId, connId, joinedAt, earlyData })`，不解析应用层消息。
- `clientId`/`connId` 只用于连接身份与 presence，不参与文件路由之外的状态存储。

### 1.4 重复连接处理

DO 在 `fetch` 接受新连接时：

1. 遍历 `ctx.getWebSockets()`，找 `attachment.clientId === 新 clientId` 的旧连接。
2. 先向旧连接 `close(4001, "replaced")`。
3. 再接受新连接，写入新 attachment。
4. 向房间内另一个客户端发送 `presence(peer-offline, oldConnId)`，再发送 `presence(peer-joined, newConnId)`。

客户端收到 `4001` 后**停止重连**，显示“另一个标签页已接管”；不要两个标签页互相顶替。

---

## 2. 早期数据（`sec-websocket-protocol`）

阶段 1 不要求使用；阶段 3 用它减少重连握手的一次往返。v1.2 冻结其格式：

```text
Sec-WebSocket-Protocol: base64url(JSON.stringify({
  v: 1,
  type: "resume-hint",
  transfers: [
    { transferId: 123, fileId: "uuid", receivedBytes: 123456, metaVersion: 7 }
  ]
}))
```

规则：

1. **只能有一个 token**。客户端不得同时发送多个子协议。
2. token 必须只包含 RFC 6455 合法字符；base64url 的 `A-Z a-z 0-9 - _` 全部合法。**禁止冒号**。
3. 服务端读取 `request.headers.get("sec-websocket-protocol")`，在 101 响应中**原样回显**该 token：
   ```js
   return new Response(null, {
     status: 101,
     webSocket: client,
     headers: { "Sec-WebSocket-Protocol": token },
   });
   ```
4. 不回显会导致浏览器 1006 失败；这是硬规则。
5. 早期数据是**提示**，不是权威。真正的权威信息是 `file-accept` 里的 `startOffset` 和 `metaVersion`。
6. 早期数据不得包含密码、文件名、文件内容；只允许 `transferId`、`fileId`、`receivedBytes`、`metaVersion`。
7. 服务端只把早期数据放进当前连接的 attachment，并在 `presence(peer-joined)` 中透传给对端；不写入 storage、不写日志。
8. 早期数据 token 上限 4 KiB；超过时服务端忽略该提示（但仍回显 token 以完成握手），客户端走正常 offer/accept。
9. 阶段 5 多传输时，早期数据只带**第一条**传输的 hint；如果需要携带更多，必须省略早期数据，改由 `hello` 后的控制帧传递。阶段 1 单传输不受影响。
10. 早期数据解码失败/格式错误时，客户端忽略提示，走正常 `file-offer`/`file-accept` 流程；服务端不关闭连接。

---

## 3. 控制帧封装

所有控制帧是 WebSocket **文本帧**，UTF-8 JSON：

```ts
interface Envelope {
  v: 1;                    // 协议版本
  type: string;            // 控制帧类型
  clientId: string;        // 发送方稳定 ID
  connId: string;          // 发送方本次连接 ID
  msgId?: string;          // 可选，幂等/重试用
  replyTo?: string;        // 可选，回复某条 msgId
  sentAt?: number;         // 可选，Unix ms
  [key: string]: unknown;
}
```

规则：

- 未知 `type`：忽略（前向兼容），不报错。
- 未知字段：忽略。
- `v !== 1`：回复 `error { code: "protocol_version" }`，close `4002`。
- 任何控制帧的 `clientId`/`connId` 与当前对端不一致：**丢弃**，不回复。
- 控制帧大小上限 64 KiB；超过则 `error { code: "control_too_large" }`。
- 不允许在控制帧里放文件内容或 base64 分片。

服务端生成的控制帧（`from: "server"`）不需要 `clientId`/`connId`：

```ts
interface ServerEnvelope {
  v: 1;
  from: "server";
  type: "presence" | "server-error";
  ...
}
```

---

## 4. 控制帧定义

### 4.1 `hello`

```json
{
  "v": 1,
  "type": "hello",
  "clientId": "a-uuid",
  "connId": "conn-uuid",
  "protocol": 1,
  "auth": "sha256-hex-or-null",
  "capabilities": {
    "resume": true,
    "maxChunkSize": 4194304,
    "defaultWindowSize": 8
  },
  "sentAt": 1792424000000
}
```

- 发送时机：WebSocket `open` 后立即发送；收到 `presence(peer-joined)` 时再发；收到对端 `hello` 后如果自己还没发过，也发。
- 重试：2s、4s、8s、16s；30s 后 UI 显示“等待对端”，但保持连接，继续等 presence。
- 对端收到后不回专用 ACK；对端自己的 `hello` 就是应答。
- `auth`：密码的 SHA-256 小写十六进制（64 字符），没有密码时为 `null`/省略。客户端只在每条连接的首个 `hello` 携带；DO 在第一帧校验后从转发的 hello 中删除该字段。

### 4.2 `presence`（服务端 → 客户端）

```json
{
  "v": 1,
  "from": "server",
  "type": "presence",
  "event": "peer-joined",
  "nonce": "connection-nonce",
  "peer": {
    "clientId": "b-uuid",
    "connId": "conn-uuid",
    "earlyData": "base64url-or-null"
  },
  "at": 1792424000000
}
```

`event`：

- `peer-joined`：另一个客户端已连接/重连。
- `peer-offline`：另一个客户端断开。

规则：

- 服务端在该连接认证通过后的首个消息、以及连接关闭时发送；未认证/4002/4003 被拒连接不参与 presence。
- `nonce` 是连接级 token：客户端只接受 `nonce` 等于自己子协议 token 的 presence；`peer` 对象不包含对方的 nonce。
- 客户端不得发送 `from:"server"`；服务端丢弃这类伪造帧。
- 服务端不保存历史 presence；客户端断开期间的事件会丢失，客户端必须靠 `hello` 重试和重连恢复。
- `earlyData` 只是提示；客户端不得把它当作权威 offset。
- 客户端收到 `peer-joined` 后：
  1. 更新 `peerConnId` / `peerClientId`；
  2. 清空所有来自旧 `connId` 的未完成控制帧处理；
  3. 立即发送 `hello`；
  4. 等待对端 `hello`，然后进入 offer/accept。
- 客户端收到 `peer-offline` 后：暂停所有出站传输，保留本地状态，不重置 offset。
- **连接替换的例外**：如果 `peer-offline` 与随后的 `peer-joined` 针对同一个 `clientId`，且间隔 <3s，视为“同一客户端换了连接”，不是真正离线。客户端只更新 `peerConnId`，不暂停、不重置传输；旧连接上未完成的消息按 `connId` 丢弃。超过 3s 才按离线处理。

### 4.3 `file-offer`

发送端 → 接收端。用于新传输和续传；这两种情况使用同一个帧，靠 `resume` 区分。

```json
{
  "v": 1,
  "type": "file-offer",
  "clientId": "a-uuid",
  "connId": "conn-uuid",
  "transferId": 12345,
  "fileId": "file-uuid",
  "senderClientId": "a-uuid",
  "receiverClientId": "b-uuid",
  "name": "example.bin",
  "size": 1073741824,
  "mime": "application/octet-stream",
  "chunkSize": 1048576,
  "windowSize": 8,
  "resume": true,
  "path": "photos/2026/a.jpg",
  "batchId": "uuid-or-null",
  "batchIndex": 3,
  "batchTotal": 12,
  "sha256": null
}
```

规则：

- 只有 `senderClientId` 对应的客户端可以发送。
- `receiverClientId` 必须等于接收端的 `clientId`；否则忽略并记 `error { code: "wrong_receiver" }`（不关闭连接）。
- `transferId` 在同一发送端的所有活跃传输中唯一；冲突时接收端拒绝第二个，发送端重新生成。
- `fileId` 在一次传输内稳定；续传必须使用同一个 `fileId` 和 `transferId`。
- `size` 使用 JSON number；安全范围 `0 .. 2^53-1`。超过时发送端必须拒绝并报 `file_too_large`。
- `chunkSize` 必须 ≤ 4 MiB 且 ≥ 256 KiB；否则接收端 `file-reject { reason: "bad_chunk_size" }`。
- `windowSize` 必须 1–32；否则 `bad_window`。
- `path`/`batchId`/`batchIndex`/`batchTotal` 可选，仅用于展示、排序与下载命名；接收端不得用 `path` 决定落盘位置（必须按 `fileId` 命名），展示前需过滤 `..`、绝对路径与空段。
- `sha256` 可选；若提供，接收端在完成后校验。服务端不计算、不存储。
- `resume: false`：发送端没有本地状态；接收端若发现同 `fileId` 的有效 `.meta` 且 `expectedSize` 一致，仍按 `receivedBytes` 续传（v1.5 起）。
- `resume: true`：接收端读取本地 `.meta`，用 `receivedBytes` 回复。
- 重试：发送端在 10s 内没收到 `file-accept`/`file-reject`，用相同 `transferId`/`fileId` 重发，最多 3 次；接收端必须幂等处理重复 offer（回复相同的 accept）。
- **重复 `file-offer`（相同 `transferId` + `fileId`）不得拒绝**：
  - 传输未完成：回复当前 `file-accept { startOffset: 当前持久化 receivedBytes, metaVersion, epoch, completed: false }`；
  - 传输已完成：回复 `file-accept { startOffset: expectedSize, metaVersion, epoch, completed: true }`；发送端看到 `completed: true` 直接视为完成，不再发二进制帧，可再发一次 `complete` 拿 `complete-ack`。
  - 重复 offer 不重置 offset、不重新 truncate、不重新开始。
- `file-reject { reason: "peer_not_ready", retryAfterMs }` 触发的是**按提示重试**，不计入上面的 3 次失败；发送端等待 `retryAfterMs` 后重发，而不是按 10s 超时判失败。

### 4.4 `file-accept`

接收端 → 发送端。

```json
{
  "v": 1,
  "type": "file-accept",
  "clientId": "b-uuid",
  "connId": "conn-uuid",
  "transferId": 12345,
  "fileId": "file-uuid",
  "startOffset": 536870912,
  "metaVersion": 7,
  "epoch": "receiver-epoch-uuid",
  "completed": false
}
```

规则：

- `startOffset` 是接收端**已持久化**的字节数；发送端必须从这里开始。
- `completed: true` 表示该 `transferId`/`fileId` 已经完整接收；发送端不得再发二进制帧，只需按需补一次 `complete` 或直接标记完成。
- 接收端在回复前必须确保 `.meta` 已加载；若 `startOffset > size` 或 `fileId` 不匹配，接收端必须 truncate 到 0 并回复 `startOffset: 0, metaVersion: 0`。
- 若接收端无法在 5s 内准备好 OPFS 元数据，回复 `file-reject { reason: "receiver_not_ready", retryAfterMs: 2000 }`；发送端按提示重试。
- 重复的 `file-accept`：发送端如果已经在 `transferring`，忽略重复帧；如果还没开始发送，只接受第一个有效 accept，后续相同 `transferId` 的 accept 只有在 `startOffset` 更大且 `epoch` 相同时才更新起点（防御乱序），否则忽略。
- `metaVersion`/`epoch` 用于确认接收端的 OPFS 状态谱系：发送端只回显，不解释；接收端在 `complete` 时要求 `epoch` 与 accept 时一致，且最终 `metaVersion >= accept.metaVersion`。`metaVersion` 会随每次写入递增，所以不能要求它等于 accept 时的值。
- 发送端收到 `file-accept` 后：
  1. 校验 `transferId`/`fileId`；
  2. 设置 `ackedOffset = startOffset`；
  3. 把该传输标记为 `transferring`；
  4. 才能开始发送二进制帧。

### 4.5 `file-reject`

```json
{
  "v": 1,
  "type": "file-reject",
  "clientId": "b-uuid",
  "connId": "conn-uuid",
  "transferId": 12345,
  "fileId": "file-uuid",
  "reason": "no_state"
}
```

`reason` 枚举：

| reason | 含义 | 发送端处理 |
|---|---|---|
| `no_state` | 接收端没有该文件状态，且用户不接受从 0 开始 | 停止；UI 提示 |
| `size_mismatch` | 本地 `.meta.expectedSize` 与 offer.size 不一致 | 停止；要求用户选择新文件/清空 |
| `bad_chunk_size` | 分片大小非法 | 修正后重发 |
| `bad_window` | 窗口非法 | 修正后重发 |
| `duplicate_file` | 同一 `fileId` 已有活动传输 | 复用现有传输，不新建 |
| `transfer_id_conflict` | `transferId` 与另一方向冲突 | 重新生成 `transferId` |
| `receiver_not_ready` | OPFS 元数据未就绪，或接收端仍在等待用户确认 | 按 `retryAfterMs` 重试 |
| `user_rejected` | 接收端用户拒绝 | 停止；UI 提示；可重新选择发送 |
| `quota` | OPFS 配额不足 | 停止；UI 提示 |

### 4.6 `ack`

接收端 → 发送端。累计 ACK。

```json
{
  "v": 1,
  "type": "ack",
  "clientId": "b-uuid",
  "connId": "conn-uuid",
  "transferId": 12345,
  "fileId": "file-uuid",
  "receivedBytes": 537919488,
  "metaVersion": 8,
  "epoch": "receiver-epoch-uuid"
}
```

规则：

- `receivedBytes` 是**累计已持久化**字节数，单调不减；它不是 `writtenBytes`。
- 接收端只在持久化边界之后发送 ACK：每 `persistEveryChunks` 个分片、或发送端窗口即将填满时（二者取较早者）强制持久化、更新 `.meta`、再发 ACK。因为 `persistEveryChunks <= windowSize`，发送端不会等在一个超出窗口的边界上。
- 发送端只接受 `receivedBytes >= ackedOffset`；旧值丢弃。
- 发送端不得在 `ackedOffset + windowSize * chunkSize < sentOffset` 时继续读文件。
- 重复/旧二进制帧：
  - `offset < receivedBytes`：忽略，不写，回当前 `ack`（可合并）；
  - `offset == receivedBytes`：正常写入；
  - `offset > receivedBytes`：回 `error { code: "offset_gap", transferId, receivedBytes }`，不推进 offset；发送端把 `nextOffset` 重置到 `receivedBytes` 后继续。
- 重复 `ack`：接收端只做单调推进；发送端收到更小的 `receivedBytes` 直接忽略。
- **ACK 停滞与重传**：发送端在窗口内一段时间没有 ACK 进展时，先发 `ack-query`；若仍无进展，把 `nextOffset` 回退到 `ackedOffset`（即接收端最后一次累计确认的位置）并重发。接收端按 offset 规则处理重复帧；如果重复帧落在未持久化尾部，先丢弃尾部再处理（见 6.1/6.2）。
- 发送端不得因为“已经发送过”而跳过重传；`sentOffset` 只是本地视图，权威是接收端的 `receivedBytes`。

### 4.6a `ack-query`

发送端 → 接收端。用于 ACK 看门狗；接收端必须幂等回复。

```json
{
  "v": 1,
  "type": "ack-query",
  "clientId": "a-uuid",
  "connId": "conn-uuid",
  "transferId": 12345,
  "fileId": "file-uuid"
}
```

- 接收端收到后**始终**回复当前 `ack { receivedBytes, metaVersion, epoch }`，无论是否重复、是否已完成。
- 发送端收到后按 `ack` 规则推进 `ackedOffset`。
- `ack-query` 不重置传输、不改变 offset。

### 4.7 `pause` / `resume`

```json
{ "v": 1, "type": "pause", "clientId": "a-uuid", "connId": "c", "transferId": 12345, "reason": "user" }
{ "v": 1, "type": "resume", "clientId": "a-uuid", "connId": "c", "transferId": 12345, "startOffset": 537919488 }
```

- 任一方向都可以暂停；暂停后发送端停止读文件，已发出的帧仍可能到达，接收端按 offset 规则处理。
- `resume` 的 `startOffset` 必须等于接收端当前 `receivedBytes`；否则接收端回 `error { code: "stale_offset" }`，发送端重新走 `file-offer`。
- 阶段 1 只实现发送端暂停；阶段 2 起双方均可暂停（实现见 v1.3 变更记录第 3 条）。

### 4.8 `complete`

发送端 → 接收端，或接收端 → 发送端确认。

```json
{
  "v": 1,
  "type": "complete",
  "clientId": "a-uuid",
  "connId": "conn-uuid",
  "transferId": 12345,
  "fileId": "file-uuid",
  "size": 1073741824,
  "sha256": "optional-hex"
}
```

规则：

- 发送端在最后一帧（`FINAL` 标志）发出后发送 `complete`。
- 接收端只有在 `receivedBytes === size`、`epoch === accept.epoch`、且 `metaVersion >= accept.metaVersion` 时才回复 `complete-ack`；否则回 `error { code: "incomplete" }`。
- 若 offer 带了 `sha256`，接收端必须在回复 `complete-ack` 前本地计算并校验；不匹配回 `error { code: "hash_mismatch" }`。
- `complete-ack`：
  ```json
  { "v": 1, "type": "complete-ack", "clientId": "b-uuid", "connId": "c", "transferId": 12345, "fileId": "file-uuid", "receivedBytes": 1073741824, "metaVersion": 42, "epoch": "receiver-epoch-uuid" }
  ```
- 发送端收到 `complete-ack` 后，校验 `receivedBytes === size`、`epoch === accept.epoch`、`metaVersion >= accept.metaVersion`；不匹配视为旧状态，重新 `file-offer`。匹配后才删除本地传输状态（或标记完成）。
- **重复 `complete` 必须幂等**：接收端无论收到多少次相同 `transferId`/`fileId` 的 `complete`，都回复当前 `complete-ack`；如果已完成，直接回已保存的 `complete-ack`，不重新计算哈希（除非显式要求）。
- **重复 `complete-ack`**：发送端只做确认，不重复删除/不重复回调；已经完成则忽略。
- 若 `file-accept` 返回 `completed: true`，发送端可以直接把该传输标记完成；随后发送一次 `complete` 即可拿到 `complete-ack`（幂等）。
- **`incomplete` 的重传**：接收端回 `error { code: "incomplete", receivedBytes }` 时，发送端必须把 `nextOffset` 回退到 `receivedBytes`、状态改回 `sending`，从该位置重传，而不是只重发 `complete`。
- **`offset_gap` 的重传**：接收端回 `error { code: "offset_gap", receivedBytes }` 时，发送端同样回退到 `receivedBytes` 并重传。

### 4.9 `error`

```json
{
  "v": 1,
  "type": "error",
  "clientId": "b-uuid",
  "connId": "conn-uuid",
  "code": "offset_gap",
  "transferId": 12345,
  "fileId": "file-uuid",
  "fatal": false,
  "detail": "expected 537919488, got 538968064"
}
```

- `detail` 只能包含 offset/状态/错误码，不得包含文件内容或文件名。
- `fatal: true` 时，接收方关闭当前传输；不关闭 WebSocket，除非是协议版本错误。
- 错误码见第 8 节。

---

## 5. 二进制分片格式

每个文件分片是一个 WebSocket **二进制帧**。DO 原样转发，不解析。

### 5.1 帧布局（16 字节头 + 负载）

| 偏移 | 长度 | 字段 | 说明 |
|---:|---:|---|---|
| 0 | 2 | `magic` | `0x52 0x4C` = "RL" |
| 2 | 1 | `version` | `1` |
| 3 | 1 | `flags` | bit0 `FINAL`；bit1 `ENCRYPTED`（预留，阶段 1 必须为 0）；其余保留为 0 |
| 4 | 4 | `transferId` | 大端 u32 |
| 8 | 8 | `offset` | 大端 u64；本分片第一个字节在文件中的偏移 |
| 16 | N | `payload` | 原始文件字节 |

```ts
interface ChunkHeader {
  magic: 0x524c;
  version: 1;
  flags: number;        // bit0 FINAL
  transferId: number;   // u32
  offset: bigint;       // u64
}
```

### 5.2 规则

- `payload.length = frame.byteLength - 16`，必须 ≥ 1 且 ≤ `chunkSize`。
- `offset` 必须是接收端当前 `receivedBytes`；否则按 4.6 的 gap/duplicate 规则处理。
- `offset` 必须 ≤ `2^53-1`（JSON/JS 安全整数范围）；文件大小超过该范围时 v1 不支持。
- `FINAL` 只能在最后一个分片设置；接收端收到 `FINAL` 后必须满足 `offset + payload.length === size`，否则 `error { code: "final_size_mismatch" }`。
- 发送端不得在 `file-accept` 之前发送任何二进制帧。
- 接收端不得把二进制帧内容写入日志、控制帧、IndexedDB 或任何服务端可读位置；只写入本地 OPFS。
- 阶段 1 `ENCRYPTED=0`；阶段 5 若加端到端加密，负载加密、头部保留（或另行定义 v2）。
- DO 只做 `peer.send(frame)`；不读取头部，不校验 magic，不缓存。

### 5.3 分片与窗口默认值

- `chunkSize = 256 KiB`。
- `windowSize = 8`（在途 2 MiB）。
- `persistEveryChunks = 8`（每 2 MiB 一个持久化边界）。
- 允许范围：`chunkSize 256 KiB–4 MiB`，`windowSize 1–32`；默认突发 ≤2 MiB，自适应窗口 8–16 时最多 4 MiB（硬上限）。
- 大文件按 `file.slice(offset, offset + chunkSize)` 读取，不一次性读入内存。
- 浏览器端必须 `ws.binaryType = "arraybuffer"`，否则兼容日期 ≥ 2026-03-17 的默认 Blob 会引入异步读取和额外拷贝。

**为什么是 256 KiB × 8，而不是 1 MiB × 8**

2026-10-20 线上实测（Cloudflare Worker + DO，wrangler 4.151.1）：

| 突发 | 结果 |
|---|---|
| 1 × 1 MiB | 1/1 到达 |
| 2 × 1 MiB（2 MiB） | 2/2 到达 |
| 5 × 1 MiB（5 MiB） | 1/5 到达 |
| 5 × 1 MiB，间隔 200ms | 5/5 到达 |
| 8 × 256 KiB（2 MiB） | 4 次重复均 8/8 到达 |
| 8 × 512 KiB（4 MiB） | 8/8 到达（边界） |
| 16 × 256 KiB（4 MiB） | 15/16 到达 |

结论：一次突发超过约 2–4 MiB 时，平台会丢 WebSocket 消息。2 MiB 是安全区。256 KiB × 8 既保留 8 片窗口/8 片持久化的默认，又把突发控制在 2 MiB；1 MiB × 8 的 8 MiB 突发不可用。

---

## 6. 断点续传与重连（重点）

### 6.1 本地持久化

接收端 OPFS：

```text
/relay/{roomId}/{fileId}.bin       # 文件数据
/relay/{roomId}/{fileId}.meta.a    # 元数据槽 A
/relay/{roomId}/{fileId}.meta.b    # 元数据槽 B
```

每个槽的格式：

```json
{
  "v": 1,
  "metaVersion": 7,
  "epoch": "receiver-epoch-uuid",
  "bodyJson": "{\"v\":1,\"roomId\":\"...\",\"fileId\":\"...\",\"transferId\":12345,\"senderClientId\":\"a-uuid\",\"receiverClientId\":\"b-uuid\",\"name\":\"example.bin\",\"expectedSize\":1073741824,\"chunkSize\":1048576,\"receivedBytes\":536870912,\"updatedAt\":1792424000000}",
  "checksum": "sha256-hex-of-utf8(bodyJson)"
}
```

- `bodyJson` 是元数据正文的**原始 JSON 字符串**；`checksum = SHA-256(UTF-8(bodyJson))`。这样不依赖对象键顺序。
- 正文至少包含：`roomId`、`fileId`、`transferId`、`senderClientId`、`receiverClientId`、`name`、`expectedSize`、`chunkSize`、`receivedBytes`、`updatedAt`。
- `receivedBytes` **只表示已持久化字节数**。内存中的 `writtenBytes` 不得写进任何槽。
- `epoch` 在首次创建时生成；同一文件状态谱系内保持不变；`.meta` 全部失效/清空后重新生成。

**写入顺序（硬规则）**

1. 写数据到 `.bin` 的 `offset`；
2. 建立持久化边界（见 6.2）；
3. 构造新正文：`receivedBytes = 已确认持久化的字节数`、`updatedAt = now`；
4. `metaVersion = 当前最高有效版本 + 1`；
5. 写入**非当前**槽（A/B 轮换），连同 `bodyJson` 和 `checksum`；
6. 读回该槽，解析并校验 `checksum` 与 `metaVersion`；
7. 校验通过后，才把该槽视为当前槽；旧槽保留；
8. 发送 `ack`。

**加载顺序**

1. 读两个槽；
2. 分别解析、校验 `checksum`、记录 `metaVersion`；
3. 选择 `metaVersion` 最高且 checksum 有效的槽；
4. 两个槽都无效：视为无状态，生成新 `epoch`，`receivedBytes = 0`；阶段 3 在继续写入前 `truncate(0)`；
5. 有有效槽：使用其 `epoch` 和 `receivedBytes`；阶段 3 在继续写入前 `truncate(receivedBytes)`，丢弃未持久化的尾部脏数据。

任何时刻崩溃，`.meta` 的 `receivedBytes` 只能**小于或等于**实际持久化字节数；绝不能大于。宁可重传，不可跳过。

### 6.2 OPFS 持久化语义与窗口死锁（硬规则）

**核心不变量：**

```text
persistEveryChunks <= windowSize
默认 persistEveryChunks = windowSize = 8
```

原因：如果 `persistEveryChunks = 16` 而 `windowSize = 8`，发送端发满 8 片后停下等 ACK；接收端只收到 8 片，还没到 16 片的持久化边界，不 close、不更新 `.meta`、不发 ACK；双方互等到 10s 的 `ack-query` 才解开。这是必须避免的窗口死锁。

规则：

- `persistEveryChunks` 必须 ≤ `windowSize`；如果以后想 16 片一持久化，窗口必须 ≥ 16。
- 也可以在窗口边界强制持久化并 ACK；但不能把 `persistEveryChunks` 独立配置成大于窗口。
- 发送端窗口即将填满时，接收端必须**提前**建立持久化边界并 ACK，不能等到下一个 `persistEveryChunks` 边界。
- 若没有可靠的 `flush()`：每 `persistEveryChunks` 个分片 `close()` 当前 writable，再重新 `createWritable({ keepExistingData: true })`，以此建立持久化边界；`.meta` 和 ACK 只在这个边界之后。
- 若实测 `write()` 立即持久化，可放宽，但仍必须保持“先数据、后 meta、再 ACK”的顺序，并满足上面的不变量。
- **`writtenBytes` 与 `persistedBytes` 严格区分**：`writtenBytes` 是写进 writable stream 的字节数，可能还在缓冲；`receivedBytes` 只表示已确认持久化的字节数。ACK、`.meta`、重连一律使用 `receivedBytes`。
- 阶段 3 必须做崩溃测试：传输 50% 时强杀标签页，重开后从 `.meta` 恢复，最终哈希一致。
- 阶段 3 必须验证：加载 `.meta` 后先 `truncate(receivedBytes)` 再 `seek(receivedBytes)`，否则未持久化的尾部可能残留脏数据。

### 6.3 重连握手（双方运行同一套算法）

**连接的权威身份**：

- `clientId` 稳定；`connId` 每次重连新生成。
- 对端当前 `connId` 由 `presence(peer-joined)` 和 `hello` 共同确定；以最新的 `peer-joined` 为准。
- **新连接必须能得知房间内已有连接**：DO 在新连接发出的第一条消息之后，向它逐条回发 `presence(peer-joined)`（101 之前直接 `send()` 的帧会丢失，不能用于此目的）。否则双方同时重连、且后加入的一方先收到对端 `hello` 时，会因为 `connId` 不匹配而丢弃它，导致双方互等。
- 所有控制帧带 `clientId`/`connId`；不匹配当前 `peerConnId` 的帧丢弃。

**步骤**：

1. **打开连接**：发送 `hello`。
2. **收到 `presence(peer-joined)`**：更新 `peerConnId`；发送 `hello`；丢弃旧 `connId` 的待处理帧。
3. **收到对端 `hello`**：标记 `peerReady = true`；如果自己还没发 `hello`，补发；然后进入第 4 步。
4. **发送 offer**：对本端所有“我要发送”的传输，按 `transferId` 升序发送 `file-offer`（`resume: true` 如果有本地状态，否则 `resume: false`）。
5. **接收 offer**：只处理 `receiverClientId === 本端 clientId` 且 `senderClientId === peerClientId` 的 offer；加载 `.meta`；回复 `file-accept { startOffset, metaVersion, epoch, completed }` 或 `file-reject`。重复 offer 按 4.3 的幂等规则回复当前 accept，不得拒绝。
6. **收到 `file-accept`**：校验；若 `completed: true`，直接标记完成；否则设置 `ackedOffset = startOffset`；标记 `transferring`；开始按窗口发送二进制帧。重复 accept 按 4.4 规则忽略或单调更新。
7. **收到 `ack`**：推进 `ackedOffset`；继续发送直到窗口满。ACK 停滞 10s 时发送 `ack-query`；重复 `ack-query` 始终回当前 `ack`。
8. **收到 `complete`**：校验 `receivedBytes === size`、`epoch === accept.epoch`、`metaVersion >= accept.metaVersion` 和可选 `sha256`；回复 `complete-ack`；重复 `complete` 始终回复当前 `complete-ack`。
9. **收到 `presence(peer-offline)`**：暂停所有出站传输；保留状态；等待下一次 `peer-joined`；不重置 offset。
10. **连接关闭/错误**：进入重连；退避 0.5s、1s、2s、4s、8s、最大 10s，加 0–250ms 抖动；生成新 `connId`；保留 `clientId` 和本地传输状态。
11. **终止关闭码**：收到 `4001`（被新连接替换）必须**停止自动重连**，清理本地连接状态，UI 提示“已在其他标签页连接”或静默停止；只有用户主动刷新/重新加入才允许新建连接。`4002`（协议版本）、`4003`（房间已满）、`4004`（房间过期）同样不自动重连；重连只会制造循环。

**超时**：

| 事件 | 超时 | 处理 |
|---|---|---|
| `hello` 无对端应答 | 2/4/8/16s 重试 | 30s 后 UI 显示等待；继续等 presence |
| `file-offer` 无 accept/reject | 10s | 相同 `transferId` 重发，最多 3 次；收到 `peer_not_ready` 时按 `retryAfterMs` 重试，不计入 3 次失败 |
| `ack` 停滞后 | 10s | 发送 `ack-query { transferId }`；2s 内仍无进展，把 `nextOffset` 回退到 `ackedOffset` 并重传；连续 3 次无进展则重连 |
| `ack-query` 无应答 | 10s | 暂停传输；主动重建连接 |
| `complete` 无 ack | 4s 起步，翻倍退避（上限 30s） | 重发 `complete`；接收端始终回 `complete-ack`；若收到 `incomplete`，回退到 `receivedBytes` 重传 |
| 收到 4001/4002/4003/4004/4005 | 立即 | **停止自动重连**，清理连接状态，等待用户操作 |
| 重连总时长 | 无硬上限 | UI 显示“正在重新连接”；用户可取消 |

### 6.4 双方同时重连：为什么不会死锁

核心规则只有一条：**只有发送端发起 offer，接收端只回复 accept；接收端的 `receivedBytes` 是唯一权威。**

因此：

- 两个方向是两条独立的“发送端 → 接收端”协商；即使同时发生，也不共享锁或顺序。
- 每个方向只有一个 offer 发起者；不会出现“双方都等对方先发”的对称等待。
- 双方同时重连时，各自发送 `hello`；各自按 `transferId` 升序发送自己的 offer；各自处理对方发来的、`receiverClientId` 指向自己的 offer；互不阻塞。
- 即使一方的 offer 先到、另一方的 `hello` 后到，接收端可以先把 offer 放入待处理队列（最多 16 条），等 `hello` 确认 `senderClientId` 后再处理；或直接校验 `senderClientId` 后处理。v1 规定：**收到 offer 时若尚未收到对端 `hello`，最多等待 2s；2s 内收到 hello 则处理，否则回 `file-reject { reason: "peer_not_ready", retryAfterMs: 2000 }`**。
- 若双方同时为同一个 `fileId` 发 offer（不应该发生，因为 `fileId` 由发送端唯一生成），接收端保留 `transferId` 较小者，另一个回 `file-reject { reason: "duplicate_file" }`；这是确定性 tie-break。
- 若 `transferId` 冲突（两个方向的随机数相同），接收端回 `transfer_id_conflict`；发送端重新生成 `transferId` 并重发 offer。重发不会影响另一个方向。
- 若一方在重连后发现本地状态被清空（`.meta.epoch` 变化或不存在），它仍按 `startOffset: 0` 回复；发送端从 0 开始。旧的 `complete`/`ack` 因 `epoch` 不同或 `metaVersion` 更旧被丢弃。
- 重连会重发 `file-offer`、`ack-query`、`complete`；接收端必须按 4.3/4.6a/4.8 的幂等规则处理，不能把重复 offer 当成 `duplicate_file` 拒绝，否则双方同时重连时会出现“明明有状态却无法续传”的假失败。
- `persistEveryChunks <= windowSize` 保证接收端不会因为“还没到持久化边界”而不发 ACK；窗口填满前一定有 ACK 可推进。
- 收到 4001 后旧连接必须停止自动重连；否则旧连接会生成新 `connId` 顶掉新连接，两个标签页/两个重连循环会无限互踢。

### 6.5 双方同时重连的时序

```text
A（发送方）                     Server (DO)                     B（接收方）
    |                              |                              |
    |-- WS connect (connId A2) --->|                              |
    |                              |<--- WS connect (connId B2) --|
    |<-- presence(peer-joined B2)--|-- presence(peer-joined A2) ->|
    |                              |                              |
    |-- hello(A2) ---------------->|----------------------------->|
    |<-----------------------------|<---------------- hello(B2) --|
    |                              |                              |
    |-- file-offer(t=7, resume) -->|----------------------------->|
    |                              |<---------------- file-accept(startOffset=50%) --|
    |-- chunk @50% -------------->|----------------------------->|
    |                              |<---------------- ack(50%+1MiB) --|
    |-- chunk ... ---------------->|----------------------------->|
    |                              |                              |
    |<---------------- file-offer(t=9, resume) ------------------|
    |-- file-accept(startOffset=30%) ->|------------------------>|
    |<---------------- chunk @30% --------------------------------|
    |-- ack(30%+1MiB) ------------>|----------------------------->|
```

两个方向交错进行，互不等待。

### 6.6 单方重连

- B 重连，A 保持：
  1. DO 向 A 发 `presence(peer-offline B1)`。
  2. B 新连接；DO 向 A 发 `presence(peer-joined B2)`。
  3. A 发送 `hello(A1)`；B 发送 `hello(B2)`；互相收到。
  4. A 对自己的出站传输发 `file-offer`；B 用自己的 `.meta.receivedBytes` 回复 `file-accept`。
  5. 传输继续。
- A 重连，B 保持：对称。

### 6.7 DO hibernation / 重启 / 部署

- DO hibernation：客户端连接保留；DO 构造函数重跑；`getWebSockets()` 仍返回连接，attachment 仍在。presence 不会重放；客户端靠 `hello` 重试和后续事件恢复。
- DO 重启/驱逐：所有 WebSocket 断开；双方进入重连，按 6.3 执行。
- 部署新版本：所有 WebSocket 断开（Cloudflare 行为）；双方重连，按 6.3 执行。部署期间不要假设任何服务端状态保留。
- 房间过期：DO 关闭连接；客户端收到 close 后停止重连，UI 显示“房间已过期”。

---

## 7. 不存储边界（重点）

### 7.1 定义

**文件字节**：任何来自用户选定文件的字节序列，或用于重建用户文件的字节序列，包括分片、部分文件、完整文件、压缩/编码/加密后的形式。

**“不存储”的准确含义**：服务端（Worker/DO）**不在应用层持久化文件字节**；不把文件字节写入 KV、R2、D1、DO storage、Queues、Cache API、日志、分析或错误上报。单个 WebSocket 帧在转发过程中会短暂经过内存，这是任何中继都不可避免的**瞬时传输**，不是持久化存储。Cloudflare 网络栈内部的瞬时缓冲属于平台行为，不是本应用写入的存储。

UI 文案必须按这个口径写：**“服务器不持久化文件；文件只在本机落盘”**，不要写成“任何地方都不存在副本”。

### 7.2 服务端允许保存的内容（白名单）

只允许以下两类：

**A. 房间元数据（DO storage）**

```text
room:{roomId}:meta = {
  v: 1,
  roomId,
  createdAt,
  expiresAt,
  protocolVersion: 1,
  passwordHash: null | string,   // 阶段 4 起：密码的 SHA-256 hex；无密码为 null/缺省
  passwordSalt: null | string,   // 保留字段；v1.4 不使用（客户端直接传 SHA-256）
  fails: number,                 // v1.5：当前窗口内的密码失败次数
  failResetAt: number,           // v1.5：失败计数窗口结束时间
  blockedUntil: number,          // v1.5：封禁到期时间（8 次失败后 10 分钟）
  expired: bool,                 // 2026-10-21：alarm 标记已过期
  expiredAt: number              // 2026-10-21：标记过期的时间；7 天后删除
}
```

- 允许的键前缀只有 `room:`。
- 密码房间：仅在「第一个连接设置密码」时写入这一条（实现中为 `{ passwordHash, createdAt, expiresAt, fails, failResetAt, blockedUntil }`）；公开房间完全不写 storage。
- 到期连接 close `4004` 并删除 key；`alarm()` 关闭在线连接、标记 `expired`，7 天后删除。

**B. 连接元数据（WebSocket attachment，平台持久化，≤16 KiB/连接）**

```text
{
  roomId,
  clientId,
  connId,
  joinedAt,
  earlyData: null | string,   // 只含 transferId/fileId/receivedBytes/metaVersion
  serverNonce: null | string, // 2026-10-21：relay.v1.<nonce> 的连接 token
  rejected: bool,             // 握手被拒绝的连接（4002/4003）
  greeted: bool,              // 是否已向该连接回发过房间内已有连接
  announced: bool,            // 是否已向已有 peer 广播 peer-joined
  authChecked: bool,          // 是否已处理首帧认证
  authed: bool                // 认证是否通过；未通过不转发、不 greet
}
```

- attachment 随连接存在，连接关闭即消失。
- 不允许在 attachment 里放文件字节、文件名、文件大小、完整 offset 列表、ACK 历史、哈希。

**C. 瞬时内存**

- `webSocketMessage` 的 `message` 参数（单个帧）；
- 一次 `peer.send(message)`；
- 不允许创建数组、队列、Map 来累积多个帧；
- 不允许对多个帧做 `concat`、`Blob`、`ReadableStream` 合并。

**D. 日志/指标（只允许元数据）**

允许：事件类型、方向、字节数、`transferId`、错误码、延迟。
禁止：文件内容、文件名、文件大小、`fileId`、`receivedBytes`、`sha256`、完整控制帧 JSON。
（`fileId` 是随机 UUID，但为了最小化元数据，日志不记录。）

### 7.3 明确禁止

```text
禁止：ctx.storage.put / delete 写入任何文件字节、分片、文件名、manifest、offset、ACK、哈希
禁止：KV / R2 / D1 / Queues / Cache API 存储文件字节或传输状态
禁止：在 DO 中 chunks.push() / concat() / new Blob() / 全量 arrayBuffer()
禁止：在 DO 中计算文件哈希或对分片做任何内容解析
禁止：“临时缓存”“断点状态备份”“为了提速先攒几片”
禁止：console.log / console.error 输出 payload、文件名、完整帧
禁止：打印 WebSocket upgrade 请求头（包括 sec-websocket-protocol / earlyData）
禁止：用 waitUntil/后台任务持有或复制分片
禁止：把早期数据写入 storage 或日志
禁止：把文件字节放进控制帧（包括 base64）
```

### 7.4 强制执行与审计

1. **静态检查**：CI 对 `src/` 做 grep，命中以下模式即失败（除非在白名单行内）：
   - `storage.put`, `storage.delete`, `env.KV`, `env.R2`, `env.DB`, `caches.`
   - `chunks.push`, `concat`, `new Blob`, `arrayBuffer()` 在 `webSocketMessage` 路径上
   - `console.log` 后跟 `message` / `data` / `payload` / `frame`
2. **运行时审计（仅测试部署）**：`GET /__debug/storage/{roomId}` 返回：
   - `ctx.storage.list()` 的键列表（不返回值）；
   - 每个 WebSocket 的 attachment 键（不返回值）；
   - 断言：除 `room:{roomId}:meta` 外没有其他键。
   该端点不得部署到公开生产环境，或用 `DEBUG_TOKEN` 保护。
3. **哨兵测试**：构造一个文件，内容包含 64 字节随机哨兵（重复）。传输 100 MiB 后：
   - `wrangler tail` 抓取的日志中搜索哨兵，必须 0 命中；
   - DO storage 的值中搜索哨兵，必须 0 命中；
   - 调试端点返回的键中不得出现 `chunk`/`file`/`payload`/`data`。
4. **内存压力**：传输 1 GiB 文件，DO 不出现 OOM/1102；日志只有字节数，没有内容。
5. **代码评审清单**：每个 PR 对照 7.2/7.3 逐条检查；新增 storage 写入必须单独评审。

### 7.5 客户端侧存储

- 接收端：OPFS 保存文件数据和 `.meta`（本地，不属于服务端存储）。
- **多标签页互斥**：接收端必须用 Web Locks API（`navigator.locks.request("relay:file:" + fileId, ...)`）保证同一 `fileId` 只有一个标签页在写 OPFS。拿不到锁时回 `file-reject { reason: "receiver_not_ready", retryAfterMs: 2000 }`，不得两个标签页同时 seek/write 同一文件。
- 发送端：源文件保留在本地；阶段 3 可以在 IndexedDB 保存 `{fileId, transferId, name, size, handle?}` 的**本地**传输清单，用于页面重载后续传。该清单不上传、不写入服务端。
- 密码：不进入 URL、日志、控制帧；阶段 5 只在本地计算哈希后发给 DO 校验。DO 只保存哈希与 salt（元数据）。

---

## 8. 错误码与关闭码

### 8.1 应用错误码（`error.code`）

| code | 含义 | fatal | 处理 |
|---|---|---|---|
| `protocol_version` | 版本不支持 | 是 | close 4002 |
| `bad_message` | JSON/字段非法 | 否 | 丢弃该帧 |
| `control_too_large` | 控制帧 >64 KiB | 否 | 丢弃 |
| `wrong_receiver` | offer 的 receiverClientId 不匹配 | 否 | 忽略 |
| `wrong_sender` | offer 的 senderClientId 不匹配当前对端 | 否 | 忽略 |
| `duplicate_file` | 同一 fileId 已有活动传输 | 否 | 保留先到者 |
| `transfer_id_conflict` | transferId 冲突 | 否 | 重新生成 |
| `bad_chunk_size` | 分片大小非法 | 否 | 拒绝 offer |
| `bad_window` | 窗口非法 | 否 | 拒绝 offer |
| `size_mismatch` | 本地 meta 与 offer.size 不一致 | 否 | 拒绝 |
| `receiver_not_ready` | OPFS meta 未就绪 | 否 | 按 retryAfterMs 重试 |
| `quota` | OPFS 配额不足 | 否 | 停止传输 |
| `offset_gap` | 二进制帧 offset 跳跃；带 `receivedBytes` | 否 | 回当前 ack，发送端重置 nextOffset |
| `stale_offset` | resume/pause 的 offset 已过期 | 否 | 重新 offer |
| `final_size_mismatch` | FINAL 后大小不符 | 是（该传输） | 停止该传输 |
| `incomplete` | complete 时 receivedBytes ≠ size | 否 | 继续/重新 offer |
| `hash_mismatch` | 完成后哈希不符 | 是（该传输） | 停止，UI 提示重传 |
| `peer_not_ready` | offer 到达时对端 hello 未到 | 否 | 按 retryAfterMs 重试，不计入 3 次失败 |
| `transfer_failed` | 接收端已判定该传输失败（离线超过宽限、元数据失败等） | 是（该传输） | 发送端先做有限次重新协商（≤3 次；接收端 OPFS meta 仍在，新 offer 从 `receivedBytes` 续传）；仍失败才终止并提示 |
| `room_full` | 房间已有两个客户端 | 是 | close 4003 |
| `room_expired` | 房间过期 | 是 | close 4004 |
| `internal` | 未分类错误 | 是 | close 1011 |

### 8.2 WebSocket 关闭码

| code | 含义 |
|---|---|
| 1000 | 正常关闭 |
| 1001 | 对端离开/服务器关闭 |
| 1006 | 异常断开（客户端重连） |
| 1009 | 消息过大（平台） |
| 4001 | 同一 clientId 的新连接替换旧连接；旧连接停止重连 |
| 4002 | 协议版本不支持 |
| 4003 | 房间已满 |
| 4004 | 房间过期 |
| 4005 | 房间密码错误；客户端停止自动重连并提示输入密码 |
| 1011 | 服务端内部错误 |

---

## 9. 默认参数（冻结）

| 参数 | 默认值 | 允许范围 |
|---|---:|---|
| `chunkSize` | 256 KiB | 256 KiB–4 MiB |
| `windowSize` | 8 | 1–32；实现限 8–16；自适应需 ≥ `persistEveryChunks` |
| `persistEveryChunks` | 8 | 1–`windowSize`；**必须 ≤ windowSize** |
| **突发上限** | **2 MiB** | 默认 ≤2 MiB；自适应窗口 8–16 时硬上限 4 MiB |
| 控制帧上限 | 64 KiB | — |
| 二进制帧上限 | 4 MiB payload | 受平台 32 MiB 限制，但 v1 自我限制 4 MiB |
| `hello` 重试 | 2/4/8/16s | — |
| offer 重试 | 10s，最多 3 次 | `peer_not_ready` 按 retryAfterMs 重试，不计入 |
| ACK 看门狗 | 10s | — |
| complete 重试 | 4s 起步，翻倍退避，上限 30s | 收到 complete-ack 即停止 |
| 房间 TTL | 24h | 1h–72h |
| 重连退避 | 0.5/1/2/4/8/10s + 0–250ms jitter | 4001/4002/4003/4004 停止自动重连 |

**不设置高频应用层心跳**。免费版 100 秒空闲超时在测试中未复现；依赖 WebSocket 协议 ping/pong（平台自动处理）和断线重连。若实测发现平台会闲置关闭，再评估是否加入低频心跳。

---

## 10. 冻结清单（实现时不得偏离）

1. `fileId` 是随机 UUID，不是内容哈希。
2. 只有发送端发 `file-offer`；接收端只回 `file-accept`/`file-reject`。
3. 发送端收到 `file-accept` 前不得发二进制帧。
4. 接收端 `receivedBytes` 是唯一权威；发送端 `ackedOffset` 永远不超过它。
5. 二进制头固定 16 字节：`RL` + version + flags + transferId(u32) + offset(u64) + payload。
6. `chunkSize` 默认 256 KiB；`windowSize` 默认 8（自适应 8–16）；默认突发 ≤2 MiB，硬上限 4 MiB。
7. 早期数据只用 base64url；101 必须回显同一 token。
8. `clientId` 稳定，`connId` 每连接新生成；服务端按 `clientId` 替换旧连接（4001）。
9. 服务端 storage 只允许 `room:{roomId}:meta`；attachment 只允许连接元数据。
10. 服务端不解析二进制帧、不计算哈希、不缓存分片。
11. 所有日志只记录事件/字节数/错误码，不记录文件内容、文件名、fileId、offset。
12. **`persistEveryChunks <= windowSize`**；默认 8/8；窗口边界必须强制持久化并 ACK。
13. **`.meta` 必须 A/B 双槽 + `metaVersion` + checksum**；只接受最高版本且 checksum 有效的槽；写入/校验失败回退上一个有效槽。
14. **`receivedBytes` 只表示已持久化字节数**；`writtenBytes` 不得用于 ACK、`.meta` 或重连；恢复时先 `truncate(receivedBytes)` 再 `seek`。
15. **重复控制帧幂等**：重复 `file-offer` 回当前 `file-accept`（已完成则 `completed:true`）；重复 `ack-query` 回当前 `ack`；重复 `complete` 回当前 `complete-ack`；重复 `ack`/`complete-ack` 只做单调推进或忽略。
16. **4001/4002/4003/4004/4005 是终止关闭码**：收到后停止自动重连；只有用户主动刷新/重新加入才允许新建连接。
17. **突发上限**：默认 `windowSize * chunkSize <= 2 MiB`；自适应窗口 8–16 时最多 4 MiB（硬上限），异常立即回退到 8 片/2 MiB。不得用 1 MiB × 8（8 MiB 突发）。
18. **WebSocket 不是无损传输**：`offset_gap`、ACK 停滞、`incomplete` 时，发送端必须回退到接收端的 `receivedBytes`/`ackedOffset` 并重传。
19. **未持久化尾部**：接收端处理重传帧前，先 `truncate(receivedBytes)` 丢弃未持久化尾部，再按 offset 规则写入。
20. 任何协议变更必须升版本并写迁移说明（当前 v1.5；2026-10-21 实现加固见 §0.1，不改变线协议版本 1），不得静默加字段或改语义。
