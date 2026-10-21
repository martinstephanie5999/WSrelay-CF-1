# relay-min

一对一双向、纯中继、零存储的浏览器文件传输。
*A zero-storage, one-to-one file transfer relay on Cloudflare Workers.*

两个浏览器打开同一个房间链接即可互传文件。服务端只在 WebSocket 之间逐帧转发，**不保存任何文件字节**；接收端把数据流式写入浏览器本地的 OPFS，可断点续传、可校验。

## 特性

- **纯中继**：Cloudflare Worker + 单个 Durable Object，二进制帧直接转发，不解析、不缓存、不落盘。
- **流式接收**：256 KiB 分片，16 字节二进制头，接收端在专用 Worker 中用 `FileSystemSyncAccessHandle` 顺序写入 OPFS。
- **断点续传**：接收端 `.meta` 使用 A/B 双槽 + 版本号 + 校验和；页面刷新、双方重连后从已持久化字节继续。
- **丢帧恢复**：WebSocket 不保证送达；`offset_gap`、ACK 停滞、`incomplete` 时发送端回退到接收端已确认的 offset 重传。
- **自适应窗口**：默认 2 MiB 在途（8 × 256 KiB），连续干净 ACK 后升到 4 MiB 上限，异常立即回退。
- **多文件 / 文件夹**：按路径排序串行传输，每个文件独立续传与 SHA-256 校验。
- **接收确认**：默认先显示“接收/拒绝”，不自动写入 OPFS；支持“本批全部接收”和自动接收开关。
- **密码房间**：密码在本机做 SHA-256 后随 `hello` 发送，服务端只保存哈希；10 分钟内 8 次失败封禁 10 分钟。
- **单页界面**：管道穿流母题、明暗主题、二维码分享、桌面双栏与移动端布局。
- **清理**：接收标题栏可一键删除已接收或未完成（含 OPFS 半成品）；发送标题栏可清空已完成/失败记录（两步确认）。
- 无需注册、无需安装，支持任意大小的文件（受本机 OPFS 配额限制）。

## 工作原理

```text
浏览器 A  ──WebSocket──▶  Worker / Durable Object  ──WebSocket──▶  浏览器 B
          控制帧 + 二进制分片              原样转发                 OPFS 落盘
```

- **控制帧**（JSON）：`hello`、`presence`、`file-offer`、`file-accept`、`file-reject`、`ack`、`ack-query`、`pause`、`resume`、`complete`、`complete-ack`、`error`。
- **二进制分片**：16 字节头（`RL`、版本、FINAL、`transferId`、`offset`）+ 负载；发送端带窗口与累计 ACK，接收端在持久化边界回 ACK。
- **服务端零状态**：Durable Object 只保存房间密码哈希；连接 attachment 只有连接元数据。调试接口默认关闭，仅在显式配置 `DEBUG_TOKEN` 时可用。

完整协议见 [`PROTOCOL.md`](PROTOCOL.md)，界面与交互规格见 [`UI-SPEC.md`](UI-SPEC.md)。

## 快速开始（本地）

要求：Node.js 20+、npm。

```powershell
npm install
npm run dev
```

打开 `http://127.0.0.1:8787/`，点击“创建房间”，把链接给另一端。

> 同一个浏览器窗口的不同标签页共用 `localStorage` 中的客户端 ID，会被服务端视为同一客户端并互踢。请用两个浏览器、两个用户配置，或普通窗口 + 无痕窗口。

## 部署（推荐：GitHub → Workers Builds 全自动）

这套流程适合**换一个 Cloudflare 账号部署**：仓库里没有账号 ID、API Token、域名或任何 Secret；Cloudflare 连接 GitHub 时会为你的账号自动生成部署 Token。

### 0. 先确认仓库里有什么

- `wrangler.toml`：Worker 名、入口、Durable Object 绑定与 `v1` migration。
- 没有 `[[routes]]`：自定义域只在 Cloudflare 网页控制台绑定。
- `workers_dev = true`：首次部署后一定有一个 `*.workers.dev` 地址可用。
- `package.json` 已固定 `wrangler@4.151.1`，`package-lock.json` 已提交。

### 1. 复制到你自己的 GitHub

任选一种：

- 仓库页点 **Use this template → Create a new repository**；或
- **Fork** 到你的账号；或
- 本地 `git push` 到你新建的空仓库。

保持默认分支为 `main`。不要直接把别人的仓库连接到你的 Cloudflare 账号，先 Fork/复制。

### 2. 在 Cloudflare 连接仓库

**新 Worker：**

1. 打开 Cloudflare Dashboard → **Workers & Pages**。
2. **Create application → Workers → Connect to Git**（不同时间界面可能叫 **Import a repository**）。
3. 授权 **Cloudflare Workers & Pages** GitHub App，只选择这个仓库。
4. 选择仓库和 `main` 分支。

**已有 Worker：**

1. **Workers & Pages → 选择 `relay-min`**。
2. **Settings → Build → Git Repository → Connect**。
3. 选择你的仓库和 `main`。

> 连接现有 `relay-min` 会复用现有 Durable Object 和数据；连接一个新名字的 Worker 会创建新的 DO 命名空间，旧房间不会跟过去。

### 3. 构建配置（照抄）

| 配置项 | 填写 |
|---|---|
| Root directory | 留空（即仓库根目录 `/`） |
| Build command | `npm run build:single`（可选；只验证单文件产物，留空也能部署） |
| Deploy command | `npx wrangler deploy` |
| Preview command | 留空或默认，并关闭 Preview builds |
| Build variables | 不需要；如需固定 Node，可加 `NODE_VERSION=20` |
| Build secrets | 不需要 |

说明：

- 这条路线部署的是 `src/index.js`，不是 `dist/worker.js`；不要把 Root directory 改成 `/dist`。
- `npm run build:single` 只生成 `dist/worker.js` 供控制台粘贴/排查，不参与 Git 部署。
- 如果账号里已经有同名 Worker，先改 `wrangler.toml` 的 `name`，再连接仓库。
- 不需要在 GitHub 配 `CLOUDFLARE_API_TOKEN`；Workers Builds 会用自己的账号 Token。
- Preview builds 暂不开启。本项目使用 Durable Objects，Preview 需要额外的 `previews.durable_objects.bindings` 配置；先只跑生产分支最省事。

### 4. 第一次部署会自动创建 Durable Object

点击 **Deploy** 后，`npx wrangler deploy` 会按 `wrangler.toml` 一次性完成：

1. 创建/更新 Worker `relay-min`；
2. 创建 Durable Object 类 `RelayRoom`；
3. 应用 `v1` migration（`new_sqlite_classes`）；
4. 绑定 `env.RELAY_ROOM`；
5. 打开 `workers_dev` 地址。

不需要在网页里手动“启用 Durable Objects”或再点一次绑定。构建日志里应能看到：

```text
env.RELAY_ROOM (RelayRoom)      Durable Object
```

### 5. 在网页控制台绑定个人域名

1. **Workers & Pages → relay-min → Settings → Domains & Routes**。
2. **Add → Custom domain**。
3. 输入你的域名，例如 `files.example.com`。
4. 按提示创建 DNS 记录并等待证书生效。

注意：

- 域名所在的 Zone 必须在**同一个 Cloudflare 账号**；跨账号不能直接绑定 Custom domain。
- 如果域名在别的账号，先把 Zone 迁过来，或在目标账号建一个子域 Zone 再绑定。
- 不需要改 `wrangler.toml`，也不需要为了域名重新部署。
- 绑定成功后同时保留 `https://relay-min.<你的账号>.workers.dev`，可自行在 **Settings → Domains & Routes** 关闭。

### 6. 以后更新

```text
改代码 → push 到 main → Workers Builds 自动构建 → 自动部署
```

- Dashboard → Worker → **Deployments** 可看构建历史、重试、回滚。
- 生产环境不要配置 `DEBUG_TOKEN`；`/__debug/storage/...` 会返回 404。
- 每次部署的 DO migration 状态由 Cloudflare 记录，不要重复改 `tag = "v1"`。

### 7. 常见失败

| 现象 | 处理 |
|---|---|
| `wrangler: not found` | 确认 `package.json`/`package-lock.json` 已提交；Deploy command 用 `npx wrangler deploy` |
| Worker 名已存在 | 修改 `wrangler.toml` 的 `name`，或连接现有同名 Worker |
| 自定义域一直 Pending | 检查域名 Zone 是否在同一账号、DNS 是否已接管 |
| Preview 报缺少 `RELAY_ROOM` | 先关闭 Preview builds；要开 Preview 需补 `previews.durable_objects.bindings` |
| 部署到了错误的目录 | Root directory 保持空/`/`，不要填 `dist` |
| DO migration 报错 | 不要改已应用的 `v1` tag；新增类才加新 migration |

## 手动部署（备用）

```powershell
npm install
npx wrangler login
npx wrangler deploy
```

自定义域仍在 **Settings → Domains & Routes → Add → Custom domain** 绑定，不写进 `wrangler.toml`。

## 单文件部署（控制台粘贴，备用）

不想用 Git 自动部署、只想在 Cloudflare 控制台贴代码时：

```powershell
npm run build:single
```

产物是 `dist/worker.js`（约 282 KiB）：客户端 HTML、二维码库、字体、图标和协议常量都已内联，不再有相对导入。

1. Dashboard → **Workers & Pages → Create → Worker**，进入在线编辑器，粘贴 `dist/worker.js` 全文并 Deploy。
2. **Settings → Bindings → Add → Durable Object**：
   - Variable name：`RELAY_ROOM`
   - Durable Object class：`RelayRoom`（新建命名空间）
   保存后重新 Deploy 一次。
3. 可选：**Settings → Domains & Routes** 绑定自定义域。

说明：

- `dist/worker.js` 是构建产物，不要手改；改代码请改 `src/` 后重新生成。
- 单文件路线会创建名为 `relay-min-single` 的独立 Worker（见 `dist/wrangler.toml`），不要和 Git 部署的 `relay-min` 混用。
- 首次绑定 Durable Object 时，控制台会为新命名空间写入 `new_sqlite_classes` 迁移（免费版必须）。若要求手填 migration，用 tag `v1`、`new_sqlite_classes: ["RelayRoom"]`。
- 想先在本地验证生成物：`npx wrangler dev --config dist/wrangler.toml --port 8790 --local`。


## 测试

测试用 Playwright 驱动真实浏览器，覆盖丢帧重传、暂停、断线续传、密码、批量、Origin、限速与 UI 状态机。

```powershell
python -m pip install playwright
playwright install msedge        # 或改用本机已安装的 Chromium
wrangler dev --port 8787 --local # 另开一个终端

python test/phase1.py            # 100 MiB 双向传输 + 服务端存储审计
python test/phase1-loss.py       # 丢一帧，验证回退重传
python test/phase2-pause.py      # 暂停 / 继续
python test/phase3-reload.py     # 接收端刷新后续传
python test/phase4-ui.py         # 375/1280 布局、主题、状态文案
python test/phase5-batch.py      # 多文件批量
python test/phase5-folder.py     # 文件夹路径与逐文件哈希
python test/phase5-pending.py    # 待传输区、刷新恢复、清空已接收
```

- 需要在 `phase1.py` 里审计服务端存储时，用 `wrangler dev --port 8787 --local --var DEBUG_TOKEN:local` 启动；未配置时该审计会自动跳过。
- 测试数据生成在 `test/data/`（已加入 `.gitignore`），截图在 `test/shots/`。
- 针对已部署实例测试时，把地址作为第一个参数传入；本机需要绕过代理时设置 `RELAY_DIRECT=1`。
- 自动化测试通过页面暴露的 `window.__relay` 钩子驱动（丢帧、暂停、写入延迟、状态查询），只在浏览器端存在，不参与传输协议。

## 不存储边界

| 内容 | 是否允许 |
|---|---|
| 文件字节、分片、文件名、大小、offset、ACK、哈希 | ❌ 服务端任何存储 |
| 房间元数据（密码哈希、创建时间） | ✅ DO storage `room:{roomId}:meta` |
| 连接元数据（clientId、connId、加入时间） | ✅ WebSocket attachment |

接收端的 `.meta` 与数据只存在浏览器 OPFS 中，按房间隔离，`fileId` 命名，不使用用户提供的路径。

## 已知限制

- 只支持一对一双向房间；同一时间每个方向一个活动传输。
- 不提供端到端加密（传输依赖 TLS）；密码只用于进门校验，不是内容加密。
- 吞吐取决于两端到 Cloudflare 的路径质量；路径冻结时靠重连与回退重传兜底。
- 超大文件受本机 OPFS 配额限制，传输前会做一次空间预估。
- 文件夹内的相对路径只用于展示；单文件“保存到设备”会扁平化文件名（D14）。
- 同一 `fileId` 的跨标签页写入没有 Web Locks 保护，靠 `clientId` 接管避免同客户端多标签互写（D18）。
- 未认证连接没有独立空闲超时；非 `hello` 首帧立即 4002，客户端退避重连（D19）。
- 批量中某个文件失败会停止后续文件；需清理失败行后重新选择剩余文件（D15）。
- 整文件 SHA-256 只在 ≤256 MiB 时计算；更大文件依赖分片/完成 ACK（D8）。
- 推荐使用近期版本的 Chromium、Firefox 或 Safari（依赖 OPFS 与 WebSocket 子协议）。

## 目录结构

```text
wrangler.toml           # Worker + DO 绑定/migration；不写自定义域 routes
package.json            # build:single / test / deploy / dev
package-lock.json       # 固定 wrangler 4.151.1
.nvmrc                  # Node 20
.github/workflows/      # 静态检查 + 单元测试 CI
scripts/build-single.mjs # 生成 dist/worker.js（控制台粘贴用）
dist/worker.js          # 构建产物（已入库，避免源码/产物漂移）
dist/wrangler.toml      # 单文件部署示例配置（Worker 名 relay-min-single）
src/index.js            # Worker 入口 + RelayRoom Durable Object + 静态资源路由
src/protocol.js         # 协议常量（256 KiB / 窗口 8 / 持久化边界 8）
src/client.html         # 单页客户端：连接、发送、OPFS 接收、UI 状态机
src/qrcode.txt          # 内置 qrcode-generator（MIT）
src/favicon.txt         # 站点图标 SVG（另附 32/180 PNG）
src/fonts/              # JetBrains Mono latin 子集（SIL OFL 1.1）
test/                   # Playwright 验收脚本 + test/unit Node 单元测试
requirements.txt        # Playwright Python 依赖
LICENSE                 # MIT（代码）
PROTOCOL.md             # 协议 v1.5（冻结）
UI-SPEC.md              # 界面规格与验收清单
FONT-LICENSE.txt        # 字体许可证
```

## 字体许可

界面数字使用 JetBrains Mono 的 latin 子集，随 Worker 一起分发。字体以 SIL Open Font License 1.1 授权，详见 [`FONT-LICENSE.txt`](FONT-LICENSE.txt)。二维码由 [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator)（MIT）生成。

代码本身以 MIT 授权，见 [`LICENSE`](LICENSE)。
