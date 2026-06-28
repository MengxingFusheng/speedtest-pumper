#!/bin/sh
set -eu

NO_START=0
if [ "${1:-}" = "--no-start" ]; then
  NO_START=1
fi

read_with_default() {
  prompt="$1"
  default="$2"
  printf "%s [%s]: " "$prompt" "$default" >&2
  read -r value
  if [ -z "$value" ]; then
    printf "%s" "$default"
  else
    printf "%s" "$value"
  fi
}

require_positive_int() {
  name="$1"
  value="$2"
  case "$value" in
    ''|*[!0-9]*)
      echo "$name 必须是大于 0 的整数" >&2
      exit 1
      ;;
  esac
  if [ "$value" -le 0 ]; then
    echo "$name 必须是大于 0 的整数" >&2
    exit 1
  fi
}

require_time() {
  name="$1"
  value="$2"
  if ! printf "%s" "$value" | grep -Eq '^[0-9]{2}:[0-9]{2}$'; then
    echo "$name 必须使用 HH:MM 格式，例如 08:30" >&2
    exit 1
  fi
  hour=${value%:*}
  minute=${value#*:}
  if [ "$hour" -gt 23 ] || [ "$minute" -gt 59 ]; then
    echo "$name 时间超出范围" >&2
    exit 1
  fi
}

echo "Speedtest 下载流量脚本部署配置"
echo "提示：请只对你有权持续访问的测速/下载源使用，建议先配置合理限速。"

line_count=$(read_with_default "外部线路条数" "2")
require_positive_int "外部线路条数" "$line_count"

connections_per_line=$(read_with_default "每线路连接数" "4")
require_positive_int "每线路连接数" "$connections_per_line"

limit_enabled=$(read_with_default "是否启用总限速? y/n" "n")
case "$limit_enabled" in
  y|Y|yes|YES|1|true|TRUE)
    rate_limit_enabled=true
    rate_limit_mbps=$(read_with_default "总限速 Mbps" "100")
    awk "BEGIN { exit !($rate_limit_mbps > 0) }" || {
      echo "总限速 Mbps 必须大于 0" >&2
      exit 1
    }
    ;;
  n|N|no|NO|0|false|FALSE)
    rate_limit_enabled=false
    rate_limit_mbps=0
    ;;
  *)
    echo "是否启用总限速请输入 y 或 n" >&2
    exit 1
    ;;
esac

start_time=$(read_with_default "每天开始时间 HH:MM" "00:00")
require_time "开始时间" "$start_time"

end_time=$(read_with_default "每天结束时间 HH:MM" "00:00")
require_time "结束时间" "$end_time"

speedtest_country=$(read_with_default "Speedtest 服务器国家/地区代码" "CN")
speedtest_search=$(read_with_default "Speedtest 服务器搜索关键词" "China")
download_urls=$(read_with_default "手动备用下载地址，多个用英文逗号分隔，留空表示不使用备用" "")
use_macvlan_answer=$(read_with_default "是否启用独立局域网 IP? y/n" "n")
case "$use_macvlan_answer" in
  y|Y|yes|YES|1|true|TRUE)
    use_macvlan=true
    echo "说明：独立局域网 IP 依赖 Docker macvlan，通常适合 Linux/NAS/软路由宿主机。Docker Desktop for Windows 可能不支持把容器直接接入你的物理局域网。"
    lan_parent_interface=$(read_with_default "宿主机连接局域网的网卡名，例如 eth0/enp3s0" "eth0")
    lan_subnet_cidr=$(read_with_default "局域网网段 CIDR" "192.168.1.0/24")
    lan_gateway=$(read_with_default "局域网网关" "192.168.1.1")
    container_ipv4_address=$(read_with_default "分配给容器的独立 IP" "192.168.1.250")
    ;;
  n|N|no|NO|0|false|FALSE)
    use_macvlan=false
    lan_parent_interface=eth0
    lan_subnet_cidr=192.168.1.0/24
    lan_gateway=192.168.1.1
    container_ipv4_address=192.168.1.250
    ;;
  *)
    echo "是否启用独立局域网 IP 请输入 y 或 n" >&2
    exit 1
    ;;
esac

cat > .env <<EOF
PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim
LINE_COUNT=$line_count
CONNECTIONS_PER_LINE=$connections_per_line
RATE_LIMIT_ENABLED=$rate_limit_enabled
RATE_LIMIT_MBPS=$rate_limit_mbps
TZ=Asia/Shanghai
START_TIME=$start_time
END_TIME=$end_time
AUTO_SELECT_SPEEDTEST=true
SPEEDTEST_COUNTRY=$speedtest_country
SPEEDTEST_SEARCH=$speedtest_search
SPEEDTEST_SERVER_LIMIT=20
SPEEDTEST_DOWNLOAD_SIZE=4000
SPEEDTEST_REFRESH_SECONDS=3600
PUBLIC_IP_URL=https://api64.ipify.org
DOWNLOAD_URLS=$download_urls
CHUNK_SIZE=262144
REQUEST_TIMEOUT_SECONDS=30
SCHEDULE_POLL_SECONDS=30
STATS_INTERVAL_SECONDS=60
LOG_LEVEL=INFO
USE_MACVLAN=$use_macvlan
LAN_PARENT_INTERFACE=$lan_parent_interface
LAN_SUBNET_CIDR=$lan_subnet_cidr
LAN_GATEWAY=$lan_gateway
CONTAINER_IPV4_ADDRESS=$container_ipv4_address
EOF

echo ".env 已生成"

if [ "$NO_START" -eq 0 ]; then
  if [ "$use_macvlan" = "true" ]; then
    docker compose -f docker-compose.yml -f docker-compose.macvlan.yml up -d --build
    echo "容器已启动。查看日志：docker compose -f docker-compose.yml -f docker-compose.macvlan.yml logs -f"
  else
    docker compose up -d --build
    echo "容器已启动。查看日志：docker compose logs -f"
  fi
fi
