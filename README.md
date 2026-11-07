# wxbridge

wxbridge 是运行在 Linux 微信上的 AI 机器人，支持群聊、私聊、图片理解与生成、引用改图、长期记忆和 Web 管理。

一个微信账号对应一个容器，内部可创建多个独立会话实例。Docker 固定程序和依赖版本，账号登录、配置及长期数据保存在外部目录，方便在其他主机部署和升级。默认直连模型服务，不要求代理或 CPA。Docker 是唯一支持的部署方式。

- [功能](#功能)
- [项目结构](#项目结构)
- [部署](#部署)
- [日常使用](#日常使用)
- [更新](#更新)
- [本地开发](#本地开发)
- [配置与参考资料](#配置与参考资料)

## 功能

- **多会话实例**：群聊、私聊各自配置模型、提示词、启停状态和记忆；实例可独立应用配置。
- **模型接入**：支持 OpenAI、Anthropic、DeepSeek、Gemini 的现有协议适配，以及相应兼容网关；代理按需配置。
- **AI 能力**：自动回复、群聊参与判断、上下文管理、长期记忆和关系学习，具体取决于模型与实例设置。
- **图片能力**：理解引用图片，可选自动附带会话窗口中的图片；支持 OpenAI Images 和 Gemini 文生图，以及引用原图后的修改，详见[图片上下文与引用改图](#图片上下文与引用改图)。
- **微信接入**：通过 [`adapter_wc`](#项目结构) 读取数据库、会话、消息和媒体，经原生 helper 提交文本、图片并协调发送确认。兼容档案针对微信 `4.1.13.26 x86_64`。
- **运行管理**：集成虚拟桌面、扫码登录、恢复和有序停机；Compose 停机宽限期为 10 分钟。未扫码不会仅因未登录而反复重启。
- **数据隔离**：配置、登录和长期记忆持久化；队列使用私有 tmpfs；解密库及扫描出的数据库密钥使用 `tmpfs,noswap`，验证失败拒绝启动。
- **网络**：账号容器采用 host 网络，仍保留私有 PID、IPC 和文件系统。管理台默认只监听 `127.0.0.1`；从旧版迁移时会将原来的 `0.0.0.0` 改为 loopback。
- **管理台与 CLI**：Web 管理台带认证及 Host/Origin 检查；容器内可通过 `docker compose exec account wxbridge status` 等命令运维；整个容器的启停与日志使用 `docker compose`。

### 图片上下文与引用改图

图片理解使用实例的回复模型，该模型需支持图片输入。在管理台实例配置中打开“自动将图片加入上下文”，或在实例 JSON 中设置以下字段，再保存并应用：

```json
{
  "vision": {
    "enabled": true,
    "context_images_enabled": true,
    "max_images": 4
  }
}
```

`vision.enabled` 默认开启，`vision.context_images_enabled` 默认关闭。关闭自动上下文时，仍可通过微信“引用”图片发问；开启后，会从当前会话保留的上下文中附带图片，不扩大历史读取范围。引用图片优先，其余按最近图片补充，共用每次最多 4 张、每张最多 8 MiB 的限制；读不到图片时会标明不可用。

生图和改图还需开启 `image_generation.enabled`，选择 `model_clients.image` 并填写 `models.image.model`。回复模型需支持工具调用，改图时还需能读取引用图片。图片连接可选 `openai` 或 `gemini`，配置见[实例示例](examples/instance/config.example.jsonc)和[模型服务配置](#2-填写模型密钥)。

修改图片时，在微信中引用要修改的那张图片，再描述要求，例如“把背景改成雪山”。每轮最多一个图片任务；只有本轮实际读到的引用图片可作为改图来源，普通历史图片不会自动作为改图原图。原图不可用时需重新引用；编辑接口失败也不会自动切换成文生图。

机器人生成的图片在确认发出后会按上下文窗口有限保留，供后续识图或引用修改；这不是长期图片记忆，重建账号容器会清空临时索引。图片输入的用量不包含在 `target.context_max_chars` 文本字符预算中，同一图片在后续请求中再次附带也会产生输入用量，实际以模型服务返回的 usage 为准。

## 项目结构

系统由两个独立的 Git 仓库组成：本仓库 [`message_bot`](https://github.com/yz68ac/message_bot) 和微信适配库 [`adapter_wc`](https://github.com/yz68ac/adapter_wc)。需要同时开发两者时，建议放在同一父目录下：

```text
bot/
├── message_bot/   本仓库：机器人（wxbridge）、Web 管理台、模型调用库（src/provider）
└── adapter_wc/    微信适配库 adapter_wc：数据库、消息、媒体、登录与原生提交
```

| 组件 | 位置 | 职责 |
| --- | --- | --- |
| wxbridge | `src/wxbridge`、`src/wxbridge_cli` | 实例、授权、Journal、发送确认、记忆和学习；CLI 入口 `wxbridge` |
| Web 管理台 | `src/web` | FastAPI 后端与 Vue 前端 |
| model-provider | `src/provider` | 模型协议和 SDK 调用，导入名 `provider`，见 [provider 说明](src/provider/README.md) |
| adapter_wc | `../adapter_wc`，以固定 wheel 形式放在 `dependencies/` | 微信数据库、消息、媒体与原生提交，接口见适配库仓库的 `docs/API.md` |

机器人**不**直接使用 `../adapter_wc` 的工作区源码，而是依赖 `dependencies/adapter_wc-<版本>-py3-none-any.whl`；`dependencies/adapter_wc.json` 记录该 wheel 的版本、SHA-256、源码提交和标签。Docker 构建、离线测试和发布都只读取这个固定产物，因此**部署和日常开发不需要 `adapter_wc` 仓库**，只有修改适配库时才需要（见[更新微信适配库](#更新微信适配库)）。

## 部署

需要 **Linux amd64、Docker Engine 23+（默认启用 BuildKit）和 Compose 插件**，内核须支持 `tmpfs,noswap`（已在 Debian 13 / Linux 6.12 验收）。宿主机无需安装 Python、Node 或微信。Windows 和 macOS 不能运行机器人，只能用于[本地开发](#本地开发)。

### 0. 准备代码和微信安装包

还没有仓库时克隆：

```bash
git clone https://github.com/yz68ac/message_bot.git
cd message_bot
```

已有本地仓库时无需重新克隆，切到 `main` 并同步：

```bash
cd message_bot
git status                 # 确认没有未提交的修改
git checkout main
git pull --ff-only
```

微信安装包不进入 Git（已被 `.gitignore` 排除），每台构建主机都需单独下载，并用仓库内的校验和核对。固定版本的安装包附在 [GitHub Release `wechat-4.1.13.26`](https://github.com/yz68ac/message_bot/releases/tag/wechat-4.1.13.26)：

```bash
cd resources/wechat/linux-x86_64/4.1.13.26
curl -fL -o installer/WeChatLinux_x86_64.deb \
  https://github.com/yz68ac/message_bot/releases/download/wechat-4.1.13.26/WeChatLinux_x86_64.deb
sha256sum -c --ignore-missing SHA256SUMS
cd -
```

腾讯官方地址 `https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb` 始终指向最新版，只有在腾讯尚未更新时才能得到同一文件；校验失败说明版本已变化，应改用上面的 Release 备份。微信为腾讯公司的专有软件，本项目与腾讯无关联，使用须遵守微信软件许可及服务协议。

若本地仓库在 Windows 上，需把仓库复制到 Linux 主机（或在主机上克隆），并一并复制 `installer/WeChatLinux_x86_64.deb`。不要复制 `.venv/`、`node_modules/`、`provider.env`、`data/` 等本机文件；迁移已有账号时才复制后两者，见[备份与迁移](#备份与迁移)。

### 1. 启动

在仓库根目录执行：

```bash
WXBRIDGE_COMMIT=$(git rev-parse HEAD) docker compose up -d --build
```

`WXBRIDGE_COMMIT` 把当前提交写入镜像，供状态和日志显示运行版本；构建上下文不含 `.git`，省略时机器人照常运行，但版本显示为 `unknown`。若工作区有未提交的修改，镜像仍会标记为 HEAD 的提交；正式部署请从已提交的代码构建，或使用[分发镜像](#分发镜像)（自动写入提交）。

首次构建需要联网，并需要第 0 步放置的微信安装包。镜像会校验安装资源、固定的 `adapter_wc` wheel 及其原生资源，并构建依赖。构建按变动频率分层：微信客户端、Python 依赖和前端依赖只在安装包、锁文件或前端变化时重建，日常只改源码的更新只重做最后几步；下载缓存保留在 BuildKit 缓存中，微信安装包只在构建时挂载、不写入镜像。其他同架构主机可直接使用构建好的镜像，无需微信安装包和构建工具，见[分发镜像](#分发镜像)。当前尚未提供公开镜像。

Compose 自动创建 `data/` 和 `provider.env`，设置初始目录权限并生成管理台密码。短暂运行的 `init` 服务完成后退出，长期运行的是 `account` 容器。若 Docker 要求管理员权限，在命令前加 `sudo`。

### 2. 填写模型密钥

编辑 `provider.env`，只需填写所用服务的密钥，例如：

```bash
sudoedit provider.env
```

```dotenv
DEEPSEEK_API_KEY=你的密钥
```

| 变量 | 用途 |
| --- | --- |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `GEMINI_API_KEY` | 各 Provider 的密钥 |
| `<PROVIDER>_BASE_URL` | 可选，网关地址，如 `OPENAI_BASE_URL`；Gemini 地址不包含 `/v1` 或 `/v1beta` |
| `<PROVIDER>_PROXY_URL` | 可选，仅需要代理的 Provider 填写，如 `OPENAI_PROXY_URL`；未配置则直连 |
| `GEMINI_IMAGE_BASE_URL` | 可选，Gemini 生图默认复用 `GEMINI_BASE_URL`，仅需独立地址时填写 |
| `DEEPSEEK_SEARCH_API_KEY` | 仅在实例的 `search.api_key_env` 指向该变量时使用 |

完整模板见 [`.env.example`](.env.example)。默认使用 host 网络，与 Linux 宿主机共享网络；宿主机代理可直接填写 `http://127.0.0.1:10808`，本机 CPA 可填写 `http://127.0.0.1:8317`。无需 `extra_hosts`、端口映射或额外代理转发。

部署根目录只读挂载以读取 `provider.env`，支持原子替换；密钥不写入镜像。容器可读取部署目录中其他具有读取权限的文件，请勿在该目录混放无关私密文件。普通编辑可保留原权限；原子替换时需保留 `root:10001`、`0440` 的文件读取权限。

### 3. 打开管理台并登录微信

访问 **http://127.0.0.1:8730**，初始管理密码在 `data/settings/web-password`：

```bash
sudo cat data/settings/web-password
```

远程服务器默认仅允许本机访问，可在自己的电脑建立 SSH 隧道后打开同一地址：

```bash
ssh -L 8730:127.0.0.1:8730 用户名@服务器地址
```

在管理台完成以下操作：

1. 在工作台点击“查看 / 刷新登录二维码”，扫码并完成手机确认。
2. 在“会话实例”创建实例，填写目标会话的稳定 ID、选择模型并启用。可先用 `filehelper`（文件传输助手）测试；群聊还需配置参与模型。模型目录可在填写密钥后刷新。
3. 回到工作台，点击“绑定账号与实例会话”，再到“服务管理”启动机器人。

新增其他会话后，先在服务管理停止机器人，再绑定一次，使新会话获得 helper 发送授权。绑定操作不会自动启动机器人。

## 日常使用

| 操作 | 方式 |
| --- | --- |
| 修改实例、提示词 | 管理台保存并应用，无需重启容器 |
| 修改模型密钥、网关或代理 | 修改 `provider.env`，在服务管理应用机器人配置 |
| 查看状态、启停机器人 | 管理台“服务管理”，或 `docker compose exec account wxbridge status` |
| 查看容器日志 | `docker compose logs --tail 100 -f account` |
| 停止整套环境 | `docker compose stop` |
| 重启整套环境 | `docker compose restart account` |

完整重启或重建容器会清空队列、游标和未完成任务，以最新消息重新建立基线，不补回复旧消息。仅应用配置或重启内部机器人会保留内存队列和游标。微信登录与长期数据保留，微信要求手机确认时按提示操作。

### 多个账号

同一份账号数据只能由一个运行环境使用。第二个账号应使用独立部署目录、Compose 项目名和端口；可用 `WXBRIDGE_WEB_PORT` 设置各账号管理台的实际监听端口，例如 `WXBRIDGE_WEB_PORT=8731 docker compose -p account2 up -d`（启动时覆盖已保存的端口）；`WXBRIDGE_IMAGE` 指定镜像。多个账号共享主机网络，需选择不同端口。

## 更新

### 更新机器人

```bash
cd message_bot
git pull --ff-only
WXBRIDGE_COMMIT=$(git rev-parse HEAD) docker compose up -d --build
```

使用预先构建的镜像时，按[分发镜像](#分发镜像)导入新镜像，切换到对应提交后执行 `docker compose up -d --no-build`。重建容器会清空队列和游标并以最新消息重新建立基线（见[日常使用](#日常使用)），建议在空闲时更新；账号登录、配置和长期数据保留在 `data/` 中。

### 分发镜像

在一台构建主机上从干净的已提交代码构建一次，再分发到其他主机：

```bash
python3 scripts/container-image.py export ../images        # 构建并保存 wxbridge:<提交> 与对应的 .json 说明
scp ../images/wxbridge-image-<提交>.* 用户名@目标主机:/var/tmp/
```

在目标主机上（需要本仓库的同一提交，用于 `compose.yaml`，但不需要微信安装包、Node 或构建工具）：

```bash
git checkout <提交>
python3 scripts/container-image.py load /var/tmp/wxbridge-image-<提交>.json   # 校验 SHA-256 后 docker load 并标记为 wxbridge:local
docker compose up -d --no-build
```

改用镜像仓库时，可在构建主机上自行执行 `docker tag wxbridge:<提交> <仓库地址>/wxbridge:<提交>` 和 `docker push`，目标主机拉取后以 `WXBRIDGE_IMAGE=<仓库地址>/wxbridge:<提交> docker compose up -d --no-build` 启动。镜像内含微信客户端，请只推送到私有仓库。

### 备份与迁移

备份或迁移时保留根目录的 `provider.env` 和整个 `data/` 目录及权限：

| 目录 | 内容 |
| --- | --- |
| `data/config/` | 账号与实例配置 |
| `data/home/` | 微信登录、原始数据库和记忆 |
| `data/settings/` | 管理台及 helper 设置 |
| `data/service/` | 启停状态 |
| `data/catalog/` | 模型目录缓存 |

迁移到新主机时，先在新主机完成[准备代码](#0-准备代码和微信安装包)，复制上述文件后再执行[启动](#1-启动)中的构建命令。不要让两个运行环境同时使用同一份数据。

### 更新微信适配库

只有修改 `adapter_wc` 本身时才需要此流程。机器人只接受**从干净、带版本标签的提交构建**的 wheel，并拒绝同一版本号对应不同内容。

1. 在 `../adapter_wc` 中完成修改、测试并提交，使工作区干净（无需手动改版本号）。

2. 在本仓库用一条命令发布并导入新版本：

   ```bash
   cd ../message_bot
   python scripts/update-wechat-adapter.py --release <新版本号> --source-repo ../adapter_wc
   ```

   它依次执行适配库的 `scripts/release.py`（更新所有版本号、重新锁定、离线测试、创建 `release: adapter_wc <版本号>` 提交和 `v<版本号>` 标签、构建 wheel 并做隔离导入检查），再核对 wheel 内容与标签源码一致，更新 `pyproject.toml` 的版本固定和 `dependencies/adapter_wc.json`、删除旧版本 wheel，最后执行 `scripts/dependencies.py`、`uv sync` 和本仓库的离线测试。任何一步失败都会停止；适配库在提交前失败时会撤销版本号改动。

   已有从干净标签构建的 wheel 时，也可只导入：`python scripts/update-wechat-adapter.py ../adapter_wc/dist/adapter_wc-<版本号>-py3-none-any.whl --source-repo ../adapter_wc`，再按提示执行 `scripts/dependencies.py` 和 `uv sync`。

   检查 `pyproject.toml`、`uv.lock`、依赖清单与 wheel 的变更后再提交本仓库，然后按[更新机器人](#更新机器人)重新部署。

3. 镜像构建需要复制或核对的适配库文件（helper 与图片资源、profile、密钥恢复模块）及图片 helper 运行目录，统一由适配库包内的 `deployment.json`（`adapter_wc.deployment`）声明，由 `scripts/adapter-wheel.py` 校验读取，机器人不再自行列出。Docker 镜像构建时会自动安装与 wheel 一致的 helper；若新版本修改了 helper、图片资源或 profile，审阅后重新构建镜像即可。

接口、登录、图片和离线分析的详细说明见适配库仓库的 `README.md` 与 `docs/`（`API.md`、`NATIVE_INTERNALS.md`、`ANALYSIS.md`）。

## 本地开发

开发和离线测试可在 Linux、Windows 或 macOS 上进行，不需要 Docker、微信或 `adapter_wc` 源码仓库。需要 Git、Python 3.13+（与容器镜像一致）、[uv](https://docs.astral.sh/uv/) 和 Node.js（含 npm）。

### 安装依赖

在仓库根目录执行：

```bash
uv sync --locked --extra database --extra login --group test
npm --prefix src/web/frontend ci
```

`uv` 会在 `.venv/` 中创建虚拟环境，并从 `dependencies/` 中的固定 wheel 安装 `adapter_wc`。下文的 `python` 均指该虚拟环境中的解释器：可先激活（Linux/macOS `source .venv/bin/activate`，Windows Git Bash `source .venv/Scripts/activate`），或改用 `uv run python`。移动仓库目录后需重新执行 `uv sync`，否则虚拟环境仍指向旧路径。

### 运行检查

```bash
python -X utf8 scripts/test-offline.py         # 后端离线测试
python scripts/check-repository.py             # 仓库约定检查
python scripts/dependencies.py --check         # 锁文件与 requirements 一致
npm --prefix src/web/frontend test             # 前端测试
npm --prefix src/web/frontend run build        # 前端类型检查与构建
git diff --check
```

离线测试不读取真实配置、不联网、不调用真实模型、不发送微信消息。提交前的其他约定见[开发约定](CONTRIBUTING.md)。

## 配置与参考资料

账号配置的 `helper_transport.enabled` 控制微信 helper 发送，`submission_api.enabled` 独立控制本地任务提交 API；新安装默认只开启前者。旧 `native_api` 配置仍可读取，保留原有开关行为，但不能与新字段混写。

- [实例配置示例](examples/instance/config.example.jsonc) · [账号配置示例](examples/account.config.example.jsonc)
- [代码地图](src/wxbridge/README.md) · [脚本入口](scripts/README.md) · [provider 说明](src/provider/README.md)
- 适配库接口：`../adapter_wc/docs/API.md` · [开发约定](CONTRIBUTING.md) · [安全与隐私](SECURITY.md)

## 许可证与隐私

本项目以 [MIT 许可证](LICENSE) 发布。密钥、私有配置、账号数据、聊天记录和记忆不随源码发布。
