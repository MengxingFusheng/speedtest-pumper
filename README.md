# Speedtest 下载流量循环脚本

这个项目用于在 Docker 里循环拉取 Speedtest 下载测速地址，持续产生下载流量。它默认会按出口公网 IP 自动选择中国境内的 Speedtest 服务器。它适合多 WAN 路由器按“新建连接数”分流的场景：脚本会按“外部线路条数 × 每线路连接数”创建并发下载连接；启用总限速时，会把总 Mbps 平均分到每个连接上，让各线路更容易接近均匀分摊。

请只对你有权持续访问的测速或下载源使用。公共测速站不一定允许长时间循环下载，建议先限速并设置运行时间窗口。

## 快速部署

从 GitHub 一键安装并进入交互部署：

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/MengxingFusheng/speedtest-pumper/main/install.ps1 | iex
```

Linux / NAS / 软路由：

```sh
curl -fsSL https://raw.githubusercontent.com/MengxingFusheng/speedtest-pumper/main/install.sh | sh
```

脚本会克隆或更新 `speedtest-pumper` 目录，然后调用交互式部署脚本。

## 本地部署

Windows PowerShell：

```powershell
.\deploy.ps1
```

Linux / macOS：

```sh
chmod +x ./deploy.sh
./deploy.sh
```

部署脚本会询问：

- 外部线路条数
- 每线路连接数
- 是否启用总限速
- 总限速 Mbps
- 每天开始时间和结束时间
- Speedtest 服务器国家/地区代码，默认 `CN`
- Speedtest 服务器搜索关键词，默认 `China`
- 手动备用下载地址，可留空
- 是否启用独立局域网 IP。启用后需要填写宿主机网卡名、局域网网段、网关和容器 IP

配置完成后会生成 `.env`，并执行：

```sh
docker compose up -d --build
```

如果启用了独立局域网 IP，部署脚本会改用：

```sh
docker compose -f docker-compose.yml -f docker-compose.macvlan.yml up -d --build
```

## 手动配置

也可以复制示例配置：

```sh
cp .env.example .env
docker compose up -d --build
```

常用配置：

```env
PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim
LINE_COUNT=2
CONNECTIONS_PER_LINE=4
RATE_LIMIT_ENABLED=true
RATE_LIMIT_MBPS=200
TZ=Asia/Shanghai
START_TIME=01:00
END_TIME=07:00
AUTO_SELECT_SPEEDTEST=true
SPEEDTEST_COUNTRY=CN
SPEEDTEST_SEARCH=China
SPEEDTEST_SERVER_LIMIT=20
SPEEDTEST_DOWNLOAD_SIZE=4000
SPEEDTEST_REFRESH_SECONDS=3600
PUBLIC_IP_URL=https://api64.ipify.org
DOWNLOAD_URLS=
CHUNK_SIZE=262144
REQUEST_TIMEOUT_SECONDS=30
SCHEDULE_POLL_SECONDS=30
STATS_INTERVAL_SECONDS=60
LOG_LEVEL=INFO
USE_MACVLAN=false
LAN_PARENT_INTERFACE=eth0
LAN_SUBNET_CIDR=192.168.1.0/24
LAN_GATEWAY=192.168.1.1
CONTAINER_IPV4_ADDRESS=192.168.1.250
```

含义：

- `PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim`：Docker 构建用的基础镜像。默认用 ECR Public 的 Docker 官方 Python 镜像副本；如果你的环境 Docker Hub 更顺，可以改成 `python:3.12-slim`。
- `LINE_COUNT=2`：外部线路 2 条。
- `CONNECTIONS_PER_LINE=4`：每条线路准备 4 个下载连接，总连接数为 8。
- `RATE_LIMIT_ENABLED=true`：启用总限速。
- `RATE_LIMIT_MBPS=200`：总下载限速 200 Mbps，8 个连接时每个连接约 25 Mbps。
- `TZ=Asia/Shanghai`：容器按北京时间判断每天运行时间。
- `START_TIME=01:00`、`END_TIME=07:00`：每天 01:00 到 07:00 运行。
- `START_TIME` 和 `END_TIME` 相同表示全天运行。
- `AUTO_SELECT_SPEEDTEST=true`：自动从 Speedtest 服务器列表选择下载测速源。
- `SPEEDTEST_COUNTRY=CN`：优先选择中国境内服务器。
- `SPEEDTEST_SEARCH=China`：请求 Speedtest 服务器列表时搜索中国服务器，避免只按当前出口附近返回境外候选。
- `SPEEDTEST_SERVER_LIMIT=20`：每次从 Speedtest 获取 20 个候选服务器。
- `SPEEDTEST_DOWNLOAD_SIZE=4000`：使用 Speedtest 服务器上的 `random4000x4000.jpg` 做下载。
- `SPEEDTEST_REFRESH_SECONDS=3600`：每个线路槽位 1 小时刷新一次服务器选择。
- `PUBLIC_IP_URL=https://api64.ipify.org`：用于记录每次自动选服时看到的出口公网 IP；失败时不影响选服。
- `DOWNLOAD_URLS=`：手动备用地址。留空时，自动选服失败会等待后重试，不会改用境外默认源。
- `USE_MACVLAN=false`：是否启用独立局域网 IP。这个值主要给部署脚本使用；手动部署时是否加载 `docker-compose.macvlan.yml` 才是关键。
- `LAN_PARENT_INTERFACE=eth0`：宿主机连接局域网的网卡名，例如 `eth0`、`enp3s0`。
- `LAN_SUBNET_CIDR=192.168.1.0/24`：局域网网段。
- `LAN_GATEWAY=192.168.1.1`：局域网网关，通常是路由器 IP。
- `CONTAINER_IPV4_ADDRESS=192.168.1.250`：分配给容器的独立局域网 IP，必须避开 DHCP 地址池和已有设备。

## 独立 IP 说明

默认 bridge 网络不会让路由器看到独立设备 IP。容器在 Docker 内部会有自己的私有 IP，但访问外网时通常会被 NAT 成宿主机 IP。

如果你希望路由器把测速容器当成一台独立设备，用它的独立 IP 做策略路由、限速或分流，请启用 macvlan：

```sh
docker compose -f docker-compose.yml -f docker-compose.macvlan.yml up -d --build
```

macvlan 常见于 Linux、NAS、软路由宿主机。Docker Desktop for Windows/macOS 不一定能把容器直接桥接到物理局域网；这种情况下需要把项目部署到 Linux/NAS/软路由上，或者在路由器侧用其他策略做分流。

## 自动选服逻辑

脚本会为每个线路槽位单独探测：

1. 通过公网 IP 查询接口识别这次请求看到的出口 IP。
2. 请求 Speedtest 服务器列表。
3. 优先过滤 `SPEEDTEST_COUNTRY=CN` 的服务器。
4. 在候选服务器里选择距离最近的服务器。
5. 把该服务器的 `upload.php` 地址转换成 Speedtest 下载文件地址，例如 `random4000x4000.jpg`。

多 WAN 路由器如果按“新建连接数”分流，容器无法强制某一次请求一定走指定线路；脚本能做的是为每个线路槽位建立独立探测和下载连接，让路由器有足够的新连接去分摊。若你的路由器支持按源地址或接口绑定，分流会更可控。

## 常用命令

查看日志：

```sh
docker compose logs -f
```

停止：

```sh
docker compose down
```

修改配置后重启：

```sh
docker compose up -d --build
```

本地测试：

```sh
python -m unittest discover -s tests
```

## 注意事项

- 多线路是否均匀，最终取决于路由器的分流算法。如果路由器按源地址固定分流，Docker 默认桥接网络可能无法做到均匀，需要调整路由器策略或 Docker 网络模式。
- 自动选服依赖 Speedtest 服务器列表接口。如果接口不可用，脚本会等待重试；你也可以在 `DOWNLOAD_URLS` 配置你有权使用的备用 Speedtest 下载地址。
- `RATE_LIMIT_MBPS` 是总限速，不是单线路限速。
