"""Regression tests with synthetic diagnostics; no live system collection."""

import contextlib
import ctypes
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "system_stats", Path(__file__).with_name("system-stats.py")
)
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)


class WindowsCollectorsTests(unittest.TestCase):
    def setUp(self):
        system = patch.object(stats, "SYSTEM", "Windows")
        system.start()
        self.addCleanup(system.stop)

    def test_cpu_uses_deltas_and_subtracts_idle_from_kernel_time(self):
        with patch.object(stats, "_windows_cpu_times", side_effect=[
            (1000, 2000), (1300, 2400)
        ]), patch.object(stats.time, "sleep"), patch.object(
            stats.time, "perf_counter", side_effect=[10.0, 11.0]
        ), patch.object(stats.os, "cpu_count", return_value=8):
            self.assertEqual(stats.collect_load(), {
                "cpu_pct": 25.0, "sample_seconds": 1.0, "cpu_count": 8
            })

    def test_invalid_cpu_sample_reports_error(self):
        for after in [(10, 20), (9, 30), (30, 25)]:
            with self.subTest(after=after), patch.object(
                stats, "_windows_cpu_times", side_effect=[(10, 20), after]
            ), patch.object(stats.time, "sleep"):
                self.assertIn("error", stats._section(stats.collect_load))

    def test_filetime_high_bits_are_preserved(self):
        def get_times(idle, kernel, user):
            idle._obj.dwHighDateTime, idle._obj.dwLowDateTime = 1, 5
            kernel._obj.dwHighDateTime, kernel._obj.dwLowDateTime = 2, 6
            user._obj.dwHighDateTime, user._obj.dwLowDateTime = 3, 7
            return True

        with patch.object(stats, "_windows_api", return_value=get_times):
            self.assertEqual(stats._windows_cpu_times(), (
                (1 << 32) + 5, (5 << 32) + 13
            ))

    def test_physical_memory_in_bytes(self):
        def get_status(pointer):
            status = pointer._obj
            self.assertEqual(status.dwLength, ctypes.sizeof(stats._MemoryStatusEx))
            status.ullTotalPhys = 16 * 1024**3
            status.ullAvailPhys = 4 * 1024**3
            return True

        with patch.object(stats, "_windows_api", return_value=get_status):
            self.assertEqual(stats.collect_mem(), {
                "total_bytes": 16 * 1024**3,
                "used_bytes": 12 * 1024**3,
                "available_bytes": 4 * 1024**3,
            })

    def test_native_failure_becomes_section_error(self):
        with patch.object(stats, "_windows_api", return_value=Mock(return_value=0)), \
                patch.object(ctypes, "get_last_error", return_value=5, create=True), \
                patch.object(ctypes, "WinError", return_value=OSError("denied"), create=True):
            for collector in (stats.collect_load, stats.collect_mem):
                self.assertEqual(stats._section(collector), {"error": "denied"})

    def test_disk_uses_current_volume_root(self):
        usage = Mock(total=1000, used=250, free=750)
        with patch.object(stats.os.path, "abspath", return_value="D:\\"), \
                patch.object(stats.shutil, "disk_usage", return_value=usage) as disk:
            result = stats.collect_disk()
        disk.assert_called_once_with("D:\\")
        self.assertEqual(result["path"], "D:\\")
        self.assertEqual(result["used_pct"], 25.0)

    def test_uptime_converts_milliseconds_without_32_bit_overflow(self):
        seconds = 60 * 86400 + 2 * 3600 + 3 * 60
        with patch.object(stats, "_windows_api", return_value=lambda: seconds * 1000):
            self.assertEqual(stats.collect_uptime(), {
                "uptime_seconds": seconds, "uptime_human": "60d 2h 3m"
            })

    def test_top_sorts_numerically_limits_and_normalizes_cpu(self):
        rows = [
            {"IDProcess": 10, "Name": "low", "PercentProcessorTime": "9", "WorkingSet": 100},
            {"IDProcess": 20, "Name": "high", "PercentProcessorTime": "200", "WorkingSet": 500},
        ]
        with patch.object(stats, "_powershell_rows", return_value=rows), \
                patch.object(stats, "_mem_windows", return_value={"total_bytes": 1000}), \
                patch.object(stats.os, "cpu_count", return_value=4):
            self.assertEqual(stats.collect_top(1), {"processes": [
                {"pid": 20, "cmd": "high", "cpu_pct": 50.0, "mem_pct": 50.0}
            ]})

    def test_ports_deduplicate_ipv4_ipv6_listeners(self):
        rows = [
            {"port": 443, "process": "server"},
            {"port": 80, "process": "server"},
            {"port": 443, "process": "server"},
        ]
        with patch.object(stats, "_powershell_rows", return_value=rows):
            self.assertEqual(stats.collect_ports(), {"listening": [
                {"port": 80, "process": "server"},
                {"port": 443, "process": "server"},
            ]})

    def test_empty_powershell_results(self):
        with patch.object(stats, "_powershell_rows", return_value=[]), \
                patch.object(stats, "_mem_windows", return_value={"total_bytes": 1000}):
            self.assertEqual(stats.collect_top(5), {"processes": []})
            self.assertEqual(stats.collect_ports(), {"listening": []})

    def test_powershell_error_is_readable_in_section(self):
        failure = subprocess.CalledProcessError(1, "powershell.exe", stderr="CIM denied\n")
        with patch.object(stats, "_run", side_effect=failure):
            self.assertEqual(stats._section(stats.collect_ports), {"error": "CIM denied"})


class ReportTests(unittest.TestCase):
    def run_report(self, args, collectors):
        output = io.StringIO()
        with patch.dict(os.environ, {"TOOL_ARGS": json.dumps(args)}), \
                patch.dict(stats.SECTIONS, collectors, clear=True), \
                contextlib.redirect_stdout(output):
            code = stats.main()
        return code, json.loads(output.getvalue())

    def test_default_request_isolates_errors_and_skips_optional_collectors(self):
        optional = Mock(side_effect=AssertionError("unexpected collection"))
        code, result = self.run_report({}, {
            "load": Mock(side_effect=OSError("denied")),
            "mem": lambda: {"total_bytes": 1000},
            "disk": lambda: {"used_pct": 25},
            "ports": optional,
            "bench": optional,
        })
        self.assertEqual(code, 0)
        self.assertEqual(result, {
            "load": {"error": "denied"},
            "mem": {"total_bytes": 1000},
            "disk": {"used_pct": 25},
        })
        optional.assert_not_called()

    def test_single_section_remains_unwrapped(self):
        code, result = self.run_report(
            {"section": "uptime"}, {"uptime": lambda: {"uptime_seconds": 123}}
        )
        self.assertEqual((code, result), (0, {"uptime_seconds": 123}))

    def test_invalid_section_does_not_collect(self):
        collector = Mock()
        code, result = self.run_report({"section": "unknown"}, {"load": collector})
        self.assertEqual(code, 1)
        self.assertIn("invalid arguments", result["error"])
        collector.assert_not_called()

    def test_unix_load_average_is_preserved(self):
        with patch.object(stats, "SYSTEM", "Linux"), patch.object(
            stats.os, "getloadavg", return_value=(1.0, 2.0, 3.0), create=True
        ), patch.object(stats.os, "cpu_count", return_value=8):
            self.assertEqual(stats.collect_load(), {
                "loadavg_1m": 1.0, "loadavg_5m": 2.0,
                "loadavg_15m": 3.0, "cpu_count": 8,
            })


if __name__ == "__main__":
    unittest.main()
