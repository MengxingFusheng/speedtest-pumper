from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field


DEFAULT_DOWNLOAD_URLS: tuple[str, ...] = ()
DEFAULT_USER_AGENT = "speedtest-traffic-pumper/1.0"
DEFAULT_SPEEDTEST_SERVERS_URL = "https://www.speedtest.net/api/js/servers"
DEFAULT_PUBLIC_IP_URL = "https://api64.ipify.org"
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


@dataclass(frozen=True)
class AppConfig:
    line_count: int
    connections_per_line: int
    rate_limit_enabled: bool
    rate_limit_mbps: float
    start_time: dt.time
    end_time: dt.time
    urls: tuple[str, ...]
    auto_select_speedtest: bool = True
    speedtest_country: str = "CN"
    speedtest_search: str = "China"
    speedtest_server_limit: int = 20
    speedtest_download_size: int = 4000
    speedtest_servers_url: str = DEFAULT_SPEEDTEST_SERVERS_URL
    public_ip_url: str = DEFAULT_PUBLIC_IP_URL
    speedtest_refresh_seconds: int = 3600
    chunk_size: int = 256 * 1024
    request_timeout_seconds: int = 30
    schedule_poll_seconds: int = 30
    stats_interval_seconds: int = 60


@dataclass(frozen=True)
class SpeedtestServer:
    server_id: str
    sponsor: str
    name: str
    country: str
    country_code: str
    upload_url: str
    distance_km: float


@dataclass
class TrafficStats:
    total_bytes: int = 0
    failures: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_bytes(self, size: int) -> None:
        with self._lock:
            self.total_bytes += size

    def add_failure(self) -> None:
        with self._lock:
            self.failures += 1

    def snapshot(self) -> tuple[int, int]:
        with self._lock:
            return self.total_bytes, self.failures


def parse_time(value: str) -> dt.time:
    if not TIME_RE.match(value):
        raise ValueError(f"时间格式必须是 HH:MM，例如 08:30，当前值: {value!r}")

    hour_text, minute_text = value.split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if hour > 23 or minute > 59:
        raise ValueError(f"时间超出范围: {value!r}")

    return dt.time(hour, minute)


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"布尔值必须是 true/false、yes/no 或 1/0，当前值: {value!r}")


def bytes_per_second_from_mbps(mbps: float) -> int:
    if mbps <= 0:
        raise ValueError("限速 Mbps 必须大于 0")
    return int(mbps * 1_000_000 / 8)


def should_run_now(now: dt.time, start: dt.time, end: dt.time) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def worker_count(config: AppConfig) -> int:
    if config.line_count <= 0:
        raise ValueError("LINE_COUNT 必须大于 0")
    if config.connections_per_line <= 0:
        raise ValueError("CONNECTIONS_PER_LINE 必须大于 0")
    return config.line_count * config.connections_per_line


def load_config(env: os._Environ[str] | dict[str, str] | None = None) -> AppConfig:
    source = env if env is not None else os.environ

    urls = tuple(
        item.strip()
        for item in source.get("DOWNLOAD_URLS", ",".join(DEFAULT_DOWNLOAD_URLS)).split(",")
        if item.strip()
    )
    config = AppConfig(
        line_count=int(source.get("LINE_COUNT", "1")),
        connections_per_line=int(source.get("CONNECTIONS_PER_LINE", "4")),
        rate_limit_enabled=parse_bool(source.get("RATE_LIMIT_ENABLED", "false")),
        rate_limit_mbps=float(source.get("RATE_LIMIT_MBPS", "0")),
        start_time=parse_time(source.get("START_TIME", "00:00")),
        end_time=parse_time(source.get("END_TIME", "00:00")),
        urls=urls,
        auto_select_speedtest=parse_bool(source.get("AUTO_SELECT_SPEEDTEST", "true")),
        speedtest_country=source.get("SPEEDTEST_COUNTRY", "CN").strip().upper(),
        speedtest_search=source.get("SPEEDTEST_SEARCH", "China").strip(),
        speedtest_server_limit=int(source.get("SPEEDTEST_SERVER_LIMIT", "20")),
        speedtest_download_size=int(source.get("SPEEDTEST_DOWNLOAD_SIZE", "4000")),
        speedtest_servers_url=source.get(
            "SPEEDTEST_SERVERS_URL", DEFAULT_SPEEDTEST_SERVERS_URL
        ),
        public_ip_url=source.get("PUBLIC_IP_URL", DEFAULT_PUBLIC_IP_URL),
        speedtest_refresh_seconds=int(source.get("SPEEDTEST_REFRESH_SECONDS", "3600")),
        chunk_size=int(source.get("CHUNK_SIZE", str(256 * 1024))),
        request_timeout_seconds=int(source.get("REQUEST_TIMEOUT_SECONDS", "30")),
        schedule_poll_seconds=int(source.get("SCHEDULE_POLL_SECONDS", "30")),
        stats_interval_seconds=int(source.get("STATS_INTERVAL_SECONDS", "60")),
    )
    validate_config(config)
    return config


def validate_config(config: AppConfig) -> None:
    worker_count(config)
    if not config.auto_select_speedtest and not config.urls:
        raise ValueError("关闭自动选服时 DOWNLOAD_URLS 至少需要一个下载地址")
    if config.rate_limit_enabled and config.rate_limit_mbps <= 0:
        raise ValueError("启用限速时 RATE_LIMIT_MBPS 必须大于 0")
    if config.speedtest_server_limit <= 0:
        raise ValueError("SPEEDTEST_SERVER_LIMIT 必须大于 0")
    if config.speedtest_download_size <= 0:
        raise ValueError("SPEEDTEST_DOWNLOAD_SIZE 必须大于 0")
    if config.speedtest_refresh_seconds <= 0:
        raise ValueError("SPEEDTEST_REFRESH_SECONDS 必须大于 0")
    if config.chunk_size <= 0:
        raise ValueError("CHUNK_SIZE 必须大于 0")
    if config.request_timeout_seconds <= 0:
        raise ValueError("REQUEST_TIMEOUT_SECONDS 必须大于 0")
    if config.schedule_poll_seconds <= 0:
        raise ValueError("SCHEDULE_POLL_SECONDS 必须大于 0")
    if config.stats_interval_seconds <= 0:
        raise ValueError("STATS_INTERVAL_SECONDS 必须大于 0")
    for url in config.urls:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"DOWNLOAD_URLS 包含无效地址: {url!r}")


def parse_speedtest_servers(items: object) -> tuple[SpeedtestServer, ...]:
    if not isinstance(items, list):
        return ()

    servers: list[SpeedtestServer] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        upload_url = str(item.get("url", "")).strip()
        if not upload_url:
            continue
        try:
            distance_km = float(item.get("distance", 0) or 0)
        except (TypeError, ValueError):
            distance_km = 0.0
        servers.append(
            SpeedtestServer(
                server_id=str(item.get("id", "")).strip(),
                sponsor=str(item.get("sponsor", "")).strip(),
                name=str(item.get("name", "")).strip(),
                country=str(item.get("country", "")).strip(),
                country_code=str(item.get("cc", "")).strip().upper(),
                upload_url=upload_url,
                distance_km=distance_km,
            )
        )
    return tuple(servers)


def choose_speedtest_server(
    servers: tuple[SpeedtestServer, ...], country_code: str
) -> SpeedtestServer:
    if not servers:
        raise ValueError("没有可用的 Speedtest 服务器")

    preferred_country = country_code.strip().upper()
    preferred = tuple(
        server for server in servers if server.country_code == preferred_country
    )
    candidates = preferred or servers
    return min(candidates, key=lambda server: server.distance_km)


def speedtest_download_url(upload_url: str, download_size: int) -> str:
    parsed = urllib.parse.urlparse(upload_url)
    base_path = parsed.path.rsplit("/", 1)[0] if "/" in parsed.path else ""
    image_name = f"random{download_size}x{download_size}.jpg"
    path = f"{base_path.rstrip('/')}/{image_name}"
    return urllib.parse.urlunparse(parsed._replace(path=path, query="", fragment=""))


def speedtest_servers_query_url(base_url: str, limit: int, search: str) -> str:
    query = {
        "engine": "js",
        "https_functional": "true",
        "limit": str(limit),
    }
    if search.strip():
        query["search"] = search.strip()
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urllib.parse.urlencode(query)}"


class DownloadUrlProvider:
    def url_for_worker(self, worker_id: int) -> str:
        raise NotImplementedError


class StaticDownloadUrlProvider(DownloadUrlProvider):
    def __init__(self, urls: tuple[str, ...]) -> None:
        if not urls:
            raise ValueError("DOWNLOAD_URLS 至少需要一个下载地址")
        self.urls = urls

    def url_for_worker(self, worker_id: int) -> str:
        return self.urls[worker_id % len(self.urls)]


class SpeedtestDownloadUrlProvider(DownloadUrlProvider):
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.fallback = StaticDownloadUrlProvider(config.urls) if config.urls else None
        self._lock = threading.Lock()
        self._cache: dict[int, tuple[float, str]] = {}

    def url_for_worker(self, worker_id: int) -> str:
        line_slot = worker_id % self.config.line_count
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(line_slot)
            if cached and now - cached[0] < self.config.speedtest_refresh_seconds:
                return cached[1]

        try:
            url = self._discover_url(line_slot)
        except (TimeoutError, urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            if self.fallback:
                logging.getLogger("speedtest_pumper").warning(
                    "线路槽位 %s 自动选择 Speedtest 服务器失败，改用手动备用地址: %s",
                    line_slot + 1,
                    exc,
                )
                return self.fallback.url_for_worker(worker_id)
            raise
        with self._lock:
            self._cache[line_slot] = (now, url)
        return url

    def _discover_url(self, line_slot: int) -> str:
        logger = logging.getLogger("speedtest_pumper")
        try:
            public_ip = self._read_text(self.config.public_ip_url).strip()
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            logger.warning("线路槽位 %s 公网 IP 查询失败，继续自动选服: %s", line_slot + 1, exc)
            public_ip = "未知"
        payload = self._read_text(
            speedtest_servers_query_url(
                self.config.speedtest_servers_url,
                self.config.speedtest_server_limit,
                self.config.speedtest_search,
            )
        )
        servers = parse_speedtest_servers(json.loads(payload))
        selected = choose_speedtest_server(servers, self.config.speedtest_country)
        logger.info(
            "线路槽位 %s 出口 IP=%s，选择 Speedtest 服务器=%s/%s/%s，距离=%.2fkm",
            line_slot + 1,
            public_ip or "未知",
            selected.country_code,
            selected.name,
            selected.sponsor,
            selected.distance_km,
        )
        return speedtest_download_url(
            selected.upload_url, self.config.speedtest_download_size
        )

    def _read_text(self, url: str) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
        with urllib.request.urlopen(
            request, timeout=self.config.request_timeout_seconds
        ) as response:
            return response.read().decode("utf-8", errors="replace")


def add_cache_buster(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("_pumper_ts", str(time.time_ns())))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def throttle(started_at: float, downloaded_bytes: int, limit_bytes_per_second: float) -> None:
    if limit_bytes_per_second <= 0:
        return

    expected_elapsed = downloaded_bytes / limit_bytes_per_second
    actual_elapsed = time.monotonic() - started_at
    delay = expected_elapsed - actual_elapsed
    if delay > 0:
        time.sleep(min(delay, 1.0))


def download_once(
    worker_id: int,
    config: AppConfig,
    url_provider: DownloadUrlProvider,
    per_worker_limit_bps: float | None,
    stop_event: threading.Event,
    stats: TrafficStats,
) -> None:
    url = add_cache_buster(url_provider.url_for_worker(worker_id))
    request = urllib.request.Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": DEFAULT_USER_AGENT,
        },
    )
    started_at = time.monotonic()
    downloaded = 0

    with urllib.request.urlopen(request, timeout=config.request_timeout_seconds) as response:
        while not stop_event.is_set():
            if not should_run_now(dt.datetime.now().time(), config.start_time, config.end_time):
                break
            chunk = response.read(config.chunk_size)
            if not chunk:
                break
            downloaded += len(chunk)
            stats.add_bytes(len(chunk))
            if per_worker_limit_bps:
                throttle(started_at, downloaded, per_worker_limit_bps)


def worker_loop(
    worker_id: int,
    config: AppConfig,
    url_provider: DownloadUrlProvider,
    per_worker_limit_bps: float | None,
    stop_event: threading.Event,
    stats: TrafficStats,
) -> None:
    logger = logging.getLogger("speedtest_pumper")
    while not stop_event.is_set():
        if not should_run_now(dt.datetime.now().time(), config.start_time, config.end_time):
            time.sleep(config.schedule_poll_seconds)
            continue

        try:
            download_once(
                worker_id, config, url_provider, per_worker_limit_bps, stop_event, stats
            )
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            stats.add_failure()
            logger.warning("worker %s 下载失败: %s", worker_id + 1, exc)
            time.sleep(3)
        except (ValueError, json.JSONDecodeError) as exc:
            stats.add_failure()
            logger.warning(
                "worker %s 自动选择 Speedtest 服务器失败，且未配置手动备用地址: %s",
                worker_id + 1,
                exc,
            )
            time.sleep(3)


def format_bytes(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TiB"


def run(config: AppConfig) -> None:
    logger = logging.getLogger("speedtest_pumper")
    workers = worker_count(config)
    total_limit_bps = (
        bytes_per_second_from_mbps(config.rate_limit_mbps)
        if config.rate_limit_enabled
        else None
    )
    per_worker_limit_bps = total_limit_bps / workers if total_limit_bps else None
    stop_event = threading.Event()
    stats = TrafficStats()
    url_provider: DownloadUrlProvider
    if config.auto_select_speedtest:
        url_provider = SpeedtestDownloadUrlProvider(config)
    else:
        url_provider = StaticDownloadUrlProvider(config.urls)

    def stop(_signum: int, _frame: object) -> None:
        logger.info("收到停止信号，等待当前连接退出")
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    logger.info(
        "启动: 线路=%s, 每线路连接=%s, 总连接=%s, 时间=%s-%s, 总限速=%s",
        config.line_count,
        config.connections_per_line,
        workers,
        config.start_time.strftime("%H:%M"),
        config.end_time.strftime("%H:%M"),
        f"{config.rate_limit_mbps:g} Mbps" if config.rate_limit_enabled else "不限速",
    )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for worker_id in range(workers):
            executor.submit(
                worker_loop,
                worker_id,
                config,
                url_provider,
                per_worker_limit_bps,
                stop_event,
                stats,
            )

        previous_total = 0
        while not stop_event.is_set():
            time.sleep(config.stats_interval_seconds)
            total, failures = stats.snapshot()
            delta = total - previous_total
            previous_total = total
            running = should_run_now(
                dt.datetime.now().time(), config.start_time, config.end_time
            )
            logger.info(
                "状态=%s, 本周期=%s, 累计=%s, 失败=%s",
                "下载中" if running else "等待时间窗口",
                format_bytes(delta),
                format_bytes(total),
                failures,
            )


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        run(load_config())
    except ValueError as exc:
        logging.getLogger("speedtest_pumper").error("配置错误: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
