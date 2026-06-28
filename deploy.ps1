param(
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"

function Read-WithDefault {
    param(
        [string]$Prompt,
        [string]$Default
    )

    $value = Read-Host "$Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value.Trim()
}

function Read-YesNo {
    param(
        [string]$Prompt,
        [string]$Default
    )

    while ($true) {
        $value = (Read-WithDefault $Prompt $Default).ToLowerInvariant()
        if ($value -in @("y", "yes", "1", "true")) {
            return $true
        }
        if ($value -in @("n", "no", "0", "false")) {
            return $false
        }
        Write-Host "请输入 y 或 n"
    }
}

function Assert-PositiveInt {
    param(
        [string]$Name,
        [string]$Value
    )

    $parsed = 0
    if (-not [int]::TryParse($Value, [ref]$parsed) -or $parsed -le 0) {
        throw "$Name 必须是大于 0 的整数"
    }
    return $parsed
}

function Assert-Time {
    param(
        [string]$Name,
        [string]$Value
    )

    if ($Value -notmatch '^\d{2}:\d{2}$') {
        throw "$Name 必须使用 HH:MM 格式，例如 08:30"
    }
    $parts = $Value.Split(":")
    $hour = [int]$parts[0]
    $minute = [int]$parts[1]
    if ($hour -gt 23 -or $minute -gt 59) {
        throw "$Name 时间超出范围"
    }
    return $Value
}

Write-Host "Speedtest 下载流量脚本部署配置"
Write-Host "提示：请只对你有权持续访问的测速/下载源使用，建议先配置合理限速。"

$lineCount = Assert-PositiveInt "外部线路条数" (Read-WithDefault "外部线路条数" "2")
$connectionsPerLine = Assert-PositiveInt "每线路连接数" (Read-WithDefault "每线路连接数" "4")
$rateLimitEnabled = Read-YesNo "是否启用总限速? y/n" "n"
$rateLimitMbps = "0"
if ($rateLimitEnabled) {
    $rateLimitMbps = Read-WithDefault "总限速 Mbps" "100"
    $parsedLimit = 0.0
    if (-not [double]::TryParse($rateLimitMbps, [ref]$parsedLimit) -or $parsedLimit -le 0) {
        throw "总限速 Mbps 必须大于 0"
    }
}
$startTime = Assert-Time "开始时间" (Read-WithDefault "每天开始时间 HH:MM" "00:00")
$endTime = Assert-Time "结束时间" (Read-WithDefault "每天结束时间 HH:MM" "00:00")
$speedtestCountry = Read-WithDefault "Speedtest 服务器国家/地区代码" "CN"
$speedtestSearch = Read-WithDefault "Speedtest 服务器搜索关键词" "China"
$downloadUrls = Read-WithDefault "手动备用下载地址，多个用英文逗号分隔，留空表示不使用备用" ""
$useMacvlan = Read-YesNo "是否启用独立局域网 IP? y/n" "n"
$lanParentInterface = "eth0"
$lanSubnetCidr = "192.168.1.0/24"
$lanGateway = "192.168.1.1"
$containerIpv4Address = "192.168.1.250"
if ($useMacvlan) {
    Write-Host "说明：独立局域网 IP 依赖 Docker macvlan，通常适合 Linux/NAS/软路由宿主机。Docker Desktop for Windows 可能不支持把容器直接接入你的物理局域网。"
    $lanParentInterface = Read-WithDefault "宿主机连接局域网的网卡名，例如 eth0/enp3s0" $lanParentInterface
    $lanSubnetCidr = Read-WithDefault "局域网网段 CIDR" $lanSubnetCidr
    $lanGateway = Read-WithDefault "局域网网关" $lanGateway
    $containerIpv4Address = Read-WithDefault "分配给容器的独立 IP" $containerIpv4Address
}

$envContent = @"
PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim
LINE_COUNT=$lineCount
CONNECTIONS_PER_LINE=$connectionsPerLine
RATE_LIMIT_ENABLED=$($rateLimitEnabled.ToString().ToLowerInvariant())
RATE_LIMIT_MBPS=$rateLimitMbps
TZ=Asia/Shanghai
START_TIME=$startTime
END_TIME=$endTime
AUTO_SELECT_SPEEDTEST=true
SPEEDTEST_COUNTRY=$speedtestCountry
SPEEDTEST_SEARCH=$speedtestSearch
SPEEDTEST_SERVER_LIMIT=20
SPEEDTEST_DOWNLOAD_SIZE=4000
SPEEDTEST_REFRESH_SECONDS=3600
PUBLIC_IP_URL=https://api64.ipify.org
DOWNLOAD_URLS=$downloadUrls
CHUNK_SIZE=262144
REQUEST_TIMEOUT_SECONDS=30
SCHEDULE_POLL_SECONDS=30
STATS_INTERVAL_SECONDS=60
LOG_LEVEL=INFO
USE_MACVLAN=$($useMacvlan.ToString().ToLowerInvariant())
LAN_PARENT_INTERFACE=$lanParentInterface
LAN_SUBNET_CIDR=$lanSubnetCidr
LAN_GATEWAY=$lanGateway
CONTAINER_IPV4_ADDRESS=$containerIpv4Address
"@

Set-Content -LiteralPath ".env" -Value $envContent -Encoding UTF8
Write-Host ".env 已生成"

$composeArgs = @("compose", "-f", "docker-compose.yml")
if ($useMacvlan) {
    $composeArgs += @("-f", "docker-compose.macvlan.yml")
}

if (-not $NoStart) {
    $upArgs = $composeArgs + @("up", "-d", "--build")
    & docker @upArgs
    if ($useMacvlan) {
        Write-Host "容器已启动。查看日志：docker compose -f docker-compose.yml -f docker-compose.macvlan.yml logs -f"
    } else {
        Write-Host "容器已启动。查看日志：docker compose logs -f"
    }
}
