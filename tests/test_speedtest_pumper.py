import datetime as dt
import json
from pathlib import Path
import urllib.error
import unittest

from app.speedtest_pumper import (
    AppConfig,
    SpeedtestServer,
    SpeedtestDownloadUrlProvider,
    bytes_per_second_from_mbps,
    choose_speedtest_server,
    load_config,
    parse_speedtest_servers,
    parse_bool,
    parse_time,
    speedtest_servers_query_url,
    speedtest_download_url,
    should_run_now,
    worker_count,
)


class ConfigParsingTests(unittest.TestCase):
    def test_parse_time_accepts_24_hour_hh_mm(self):
        self.assertEqual(parse_time("08:30"), dt.time(8, 30))
        self.assertEqual(parse_time("23:59"), dt.time(23, 59))

    def test_parse_time_rejects_invalid_values(self):
        for value in ["24:00", "12:60", "abc", "8:00"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_time(value)

    def test_parse_bool_accepts_common_env_values(self):
        self.assertTrue(parse_bool("true"))
        self.assertTrue(parse_bool("1"))
        self.assertTrue(parse_bool("yes"))
        self.assertFalse(parse_bool("false"))
        self.assertFalse(parse_bool("0"))
        self.assertFalse(parse_bool("no"))

    def test_mbps_limit_uses_network_megabits(self):
        self.assertEqual(bytes_per_second_from_mbps(80), 10_000_000)

    def test_auto_speedtest_config_allows_empty_manual_urls(self):
        config = load_config(
            {
                "LINE_COUNT": "2",
                "CONNECTIONS_PER_LINE": "4",
                "RATE_LIMIT_ENABLED": "false",
                "START_TIME": "00:00",
                "END_TIME": "00:00",
                "AUTO_SELECT_SPEEDTEST": "true",
                "DOWNLOAD_URLS": "",
            }
        )

        self.assertTrue(config.auto_select_speedtest)
        self.assertEqual(config.urls, ())

    def test_manual_download_config_requires_urls(self):
        with self.assertRaises(ValueError):
            load_config(
                {
                    "LINE_COUNT": "2",
                    "CONNECTIONS_PER_LINE": "4",
                    "RATE_LIMIT_ENABLED": "false",
                    "START_TIME": "00:00",
                    "END_TIME": "00:00",
                    "AUTO_SELECT_SPEEDTEST": "false",
                    "DOWNLOAD_URLS": "",
                }
            )


class ScheduleTests(unittest.TestCase):
    def test_should_run_inside_same_day_window(self):
        self.assertTrue(
            should_run_now(dt.time(10, 0), dt.time(9, 0), dt.time(18, 0))
        )
        self.assertFalse(
            should_run_now(dt.time(8, 59), dt.time(9, 0), dt.time(18, 0))
        )
        self.assertFalse(
            should_run_now(dt.time(18, 0), dt.time(9, 0), dt.time(18, 0))
        )

    def test_should_run_inside_overnight_window(self):
        self.assertTrue(
            should_run_now(dt.time(23, 0), dt.time(22, 0), dt.time(6, 0))
        )
        self.assertTrue(
            should_run_now(dt.time(3, 0), dt.time(22, 0), dt.time(6, 0))
        )
        self.assertFalse(
            should_run_now(dt.time(12, 0), dt.time(22, 0), dt.time(6, 0))
        )

    def test_same_start_and_end_means_all_day(self):
        self.assertTrue(
            should_run_now(dt.time(12, 0), dt.time(0, 0), dt.time(0, 0))
        )


class WorkerPlanningTests(unittest.TestCase):
    def test_worker_count_multiplies_lines_by_connections_per_line(self):
        config = AppConfig(
            line_count=3,
            connections_per_line=4,
            rate_limit_enabled=False,
            rate_limit_mbps=0,
            start_time=dt.time(0, 0),
            end_time=dt.time(0, 0),
            urls=("https://example.test/file",),
        )

        self.assertEqual(worker_count(config), 12)

    def test_worker_count_rejects_non_positive_lines(self):
        config = AppConfig(
            line_count=0,
            connections_per_line=4,
            rate_limit_enabled=False,
            rate_limit_mbps=0,
            start_time=dt.time(0, 0),
            end_time=dt.time(0, 0),
            urls=("https://example.test/file",),
        )

        with self.assertRaises(ValueError):
            worker_count(config)


class SpeedtestServerTests(unittest.TestCase):
    def test_parse_speedtest_servers_keeps_required_fields(self):
        servers = parse_speedtest_servers(
            [
                {
                    "id": "5396",
                    "sponsor": "China Mobile",
                    "name": "Shanghai",
                    "country": "China",
                    "cc": "CN",
                    "url": "https://speedtest.example.cn/speedtest/upload.php",
                    "distance": "12.4",
                },
                {"id": "bad", "sponsor": "Missing URL"},
            ]
        )

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].country_code, "CN")
        self.assertEqual(servers[0].distance_km, 12.4)

    def test_choose_speedtest_server_prefers_nearest_china_server(self):
        servers = (
            SpeedtestServer(
                server_id="1",
                sponsor="Hong Kong Test",
                name="Hong Kong",
                country="Hong Kong",
                country_code="HK",
                upload_url="https://hk.example.net/speedtest/upload.php",
                distance_km=3.0,
            ),
            SpeedtestServer(
                server_id="2",
                sponsor="China Telecom",
                name="Guangzhou",
                country="China",
                country_code="CN",
                upload_url="https://gz.example.cn/speedtest/upload.php",
                distance_km=30.0,
            ),
            SpeedtestServer(
                server_id="3",
                sponsor="China Mobile",
                name="Shanghai",
                country="China",
                country_code="CN",
                upload_url="https://sh.example.cn/speedtest/upload.php",
                distance_km=20.0,
            ),
        )

        selected = choose_speedtest_server(servers, "CN")

        self.assertEqual(selected.server_id, "3")

    def test_speedtest_download_url_uses_random_image_on_same_server(self):
        self.assertEqual(
            speedtest_download_url(
                "https://speedtest.example.cn:8080/speedtest/upload.php", 4000
            ),
            "https://speedtest.example.cn:8080/speedtest/random4000x4000.jpg",
        )

    def test_speedtest_server_query_searches_china_by_default(self):
        url = speedtest_servers_query_url(
            "https://www.speedtest.net/api/js/servers", 20, "China"
        )

        self.assertIn("search=China", url)
        self.assertIn("limit=20", url)

    def test_speedtest_discovery_continues_when_public_ip_lookup_fails(self):
        config = AppConfig(
            line_count=1,
            connections_per_line=1,
            rate_limit_enabled=False,
            rate_limit_mbps=0,
            start_time=dt.time(0, 0),
            end_time=dt.time(0, 0),
            urls=(),
        )

        class FakeProvider(SpeedtestDownloadUrlProvider):
            def _read_text(self, url: str) -> str:
                if url == config.public_ip_url:
                    raise urllib.error.URLError("public ip unavailable")
                return json.dumps(
                    [
                        {
                            "id": "2",
                            "sponsor": "China Telecom",
                            "name": "Guangzhou",
                            "country": "China",
                            "cc": "CN",
                            "url": "https://gz.example.cn/speedtest/upload.php",
                            "distance": "30",
                        }
                    ]
                )

        with self.assertLogs("speedtest_pumper", level="WARNING"):
            self.assertEqual(
                FakeProvider(config)._discover_url(0),
                "https://gz.example.cn/speedtest/random4000x4000.jpg",
            )


class DeployScriptTests(unittest.TestCase):
    def test_shell_deploy_prompts_do_not_pollute_captured_values(self):
        script = Path("deploy.sh").read_text(encoding="utf-8")

        self.assertIn('printf "%s [%s]: " "$prompt" "$default" >&2', script)

    def test_docker_base_image_is_configurable_and_uses_reachable_default(self):
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        example_env = Path(".env.example").read_text(encoding="utf-8")

        self.assertIn(
            "ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim",
            dockerfile,
        )
        self.assertIn("FROM ${PYTHON_IMAGE}", dockerfile)
        self.assertIn("PYTHON_IMAGE: ${PYTHON_IMAGE", compose)
        self.assertIn("PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim", example_env)
        self.assertIn("TZ=Asia/Shanghai", example_env)

    def test_macvlan_compose_and_deploy_options_exist(self):
        macvlan = Path("docker-compose.macvlan.yml").read_text(encoding="utf-8")
        ps_deploy = Path("deploy.ps1").read_text(encoding="utf-8")
        sh_deploy = Path("deploy.sh").read_text(encoding="utf-8")
        readme = Path("README.md").read_text(encoding="utf-8")

        self.assertIn("driver: macvlan", macvlan)
        self.assertIn("parent: ${LAN_PARENT_INTERFACE}", macvlan)
        self.assertIn("ipv4_address: ${CONTAINER_IPV4_ADDRESS}", macvlan)
        self.assertIn("独立局域网 IP", ps_deploy)
        self.assertIn("docker-compose.macvlan.yml", ps_deploy)
        self.assertIn("$upArgs = $composeArgs +", ps_deploy)
        self.assertIn("& docker @upArgs", ps_deploy)
        self.assertIn("独立局域网 IP", sh_deploy)
        self.assertIn("docker-compose.macvlan.yml", sh_deploy)
        self.assertIn("默认 bridge 网络不会让路由器看到独立设备 IP", readme)

    def test_one_click_install_scripts_clone_from_github_and_run_deploy(self):
        install_ps = Path("install.ps1").read_text(encoding="utf-8")
        install_sh = Path("install.sh").read_text(encoding="utf-8")
        readme = Path("README.md").read_text(encoding="utf-8")

        repo_url = "https://github.com/MengxingFusheng/speedtest-pumper.git"
        self.assertIn(repo_url, install_ps)
        self.assertIn(repo_url, install_sh)
        self.assertIn(".\\deploy.ps1", install_ps)
        self.assertIn("./deploy.sh", install_sh)
        self.assertIn("irm https://raw.githubusercontent.com/MengxingFusheng/speedtest-pumper/main/install.ps1", readme)
        self.assertIn("curl -fsSL https://raw.githubusercontent.com/MengxingFusheng/speedtest-pumper/main/install.sh", readme)


if __name__ == "__main__":
    unittest.main()
