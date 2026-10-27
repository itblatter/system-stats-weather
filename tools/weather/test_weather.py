"""Weather contract tests with synthetic Open-Meteo responses."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location("weather", Path(__file__).with_name("weather.py"))
weather = importlib.util.module_from_spec(spec)
spec.loader.exec_module(weather)


def forecast(days=2):
    return {
        "timezone": "Europe/Berlin",
        "utc_offset_seconds": 3600,
        "current": {
            "time": "2026-10-27T12:00",
            **{key: None for key in weather.CURRENT_FIELDS},
            "temperature_2m": 0,
            "weather_code": 3,
        },
        "current_units": {"temperature_2m": "°C", "wind_speed_10m": "m/s"},
        "daily": {
            "time": ["2026-10-27", "2026-10-28"][:days],
            **{key: [None] * days for key in weather.DAILY_FIELDS},
            "weather_code": [0, 61][:days],
        },
        "daily_units": {"temperature_2m_min": "°C"},
    }


class ArgumentsTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.saved = Mock()
        self.saved.exists.return_value = False
        saved = patch.object(weather, "DEFAULT_LOCATION_FILE", self.saved)
        saved.start()
        self.addCleanup(saved.stop)

    def run_main(self, args):
        os.environ["TOOL_ARGS"] = json.dumps(args)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = weather.main()
        return code, json.loads(output.getvalue())

    def test_missing_location_never_calls_network(self):
        with patch.object(weather, "_get_json") as request:
            code, report = self.run_main({})
        self.assertEqual((code, report["code"]), (1, "location_required"))
        request.assert_not_called()

    def test_bad_arguments_fail_before_network(self):
        invalid = [
            [], {"location": ""}, {"location": "Berlin", "days": True},
            {"location": "Berlin", "days": 17}, {"location": "Berlin", "days": 0},
            {"latitude": 20}, {"latitude": True, "longitude": 0},
            {"latitude": float("nan"), "longitude": 0},
            {"latitude": 91, "longitude": 0}, {"latitude": 0, "longitude": 181},
            {"location": "Berlin", "latitude": 0, "longitude": 0},
            {"location": "Berlin", "country_code": "Germany"},
            {"location": "Berlin", "day": 2},
        ]
        with patch.object(weather, "_get_json") as request:
            for args in invalid:
                with self.subTest(args=args):
                    code, report = self.run_main(args)
                    self.assertEqual((code, report["code"]), (1, "invalid_arguments"))
        request.assert_not_called()

    def test_malformed_json(self):
        os.environ["TOOL_ARGS"] = "{"
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(weather.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "invalid_arguments")

    def test_explicit_location_overrides_both_defaults(self):
        os.environ.update(TOOL_ARGS='{"location":"Riga","days":7}', WEATHER_LOCATION="Berlin")
        self.saved.exists.return_value = True
        self.assertEqual(weather.parse_args(), {"location": "Riga", "days": 7})
        self.saved.read_text.assert_not_called()

    def test_environment_overrides_saved_location(self):
        os.environ["WEATHER_LOCATION"] = "Riga, Latvia"
        self.saved.exists.return_value = True
        self.assertEqual(weather.parse_args()["location"], "Riga, Latvia")
        self.saved.read_text.assert_not_called()

    def test_saved_coordinates_keep_requested_forecast_days(self):
        self.saved.exists.return_value = True
        self.saved.read_text.return_value = '{"latitude":0,"longitude":0}'
        os.environ["TOOL_ARGS"] = '{"days":7}'
        self.assertEqual(weather.parse_args(), {"latitude": 0, "longitude": 0, "days": 7})

    def test_bad_saved_configuration(self):
        self.saved.exists.return_value = True
        for content in ("{", "[]", '{"days":3}', '{"latitude":90}'):
            with self.subTest(content=content):
                self.saved.read_text.return_value = content
                code, report = self.run_main({})
                self.assertEqual((code, report["code"]), (1, "invalid_arguments"))


class ApiTests(unittest.TestCase):
    def test_geocoding_cyrillic_query_and_country_filter(self):
        row = {"name": "Москва", "latitude": 55.75, "longitude": 37.62, "country": "Россия"}
        with patch.object(weather, "_get_json", return_value={"results": [row]}) as request:
            self.assertEqual(weather.resolve_location({"location": "Москва", "country_code": "RU"}), row)
        self.assertEqual(request.call_args.args[1]["name"], "Москва")
        self.assertEqual(request.call_args.args[1]["countryCode"], "RU")

    def test_location_not_found_and_ambiguity(self):
        row = {"name": "Springfield", "latitude": 40, "longitude": -89}
        for results, code in [([], "location_not_found"), ([row, row], "ambiguous_location")]:
            with self.subTest(code=code), patch.object(
                weather, "_get_json", return_value={"results": results}
            ), self.assertRaises(weather.WeatherError) as error:
                weather.resolve_location({"location": "Springfield"})
            self.assertEqual(error.exception.report["code"], code)
            if results:
                self.assertEqual(len(error.exception.report["candidates"]), 2)

    def test_forecast_uses_local_timezone_units_and_preserves_missing_values(self):
        with patch.object(weather, "_get_json", return_value=forecast()) as request:
            result = weather.collect_weather({"latitude": 52.52, "longitude": 13.405, "days": 2})
        request.assert_called_once()
        params = request.call_args.args[1]
        self.assertEqual(params["timezone"], "auto")
        self.assertEqual(params["wind_speed_unit"], "ms")
        self.assertEqual(params["forecast_days"], 2)
        self.assertEqual(result["timezone"], "Europe/Berlin")
        self.assertEqual(result["current"]["temperature_2m"], 0)
        self.assertIsNone(result["current"]["precipitation"])
        self.assertEqual(result["daily"]["weather_description"], ["Ясно", "Слабый дождь"])

    def test_incomplete_api_response_is_not_reported_as_success(self):
        original = forecast()
        bad_daily = copy.deepcopy(original)
        bad_daily["daily"]["temperature_2m_min"] = [1]
        for data in ({}, {**original, "current": {}}, bad_daily):
            with self.subTest(data=data), patch.object(
                weather, "_get_json", return_value=data
            ), self.assertRaises(weather.WeatherError) as error:
                weather.collect_weather({"latitude": 0, "longitude": 0, "days": 2})
            self.assertEqual(error.exception.report["code"], "api_error")

    def test_http_request_encodes_unicode_and_sets_timeout(self):
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(b'{"results": []}'))
        response.__exit__ = Mock(return_value=False)
        with patch.object(weather, "urlopen", return_value=response) as request:
            weather._get_json(weather.GEOCODING_URL, {"name": "Москва, Россия"})
        query = parse_qs(urlparse(request.call_args.args[0].full_url).query)
        self.assertEqual(query["name"], ["Москва, Россия"])
        self.assertEqual(request.call_args.kwargs["timeout"], 20)

    def test_network_http_and_invalid_json_errors(self):
        for failure, code in [
            (URLError("offline"), "network_error"),
            (TimeoutError(), "network_error"),
            (HTTPError(weather.FORECAST_URL, 429, "rate limited", {}, None), "api_error"),
        ]:
            with self.subTest(failure=failure), patch.object(
                weather, "urlopen", side_effect=failure
            ), self.assertRaises(weather.WeatherError) as error:
                weather._get_json(weather.FORECAST_URL, {})
            self.assertEqual(error.exception.report["code"], code)
        for body in (b"not json", b"[]", b'{"error":true,"reason":"bad request"}'):
            response = Mock()
            response.__enter__ = Mock(return_value=io.BytesIO(body))
            response.__exit__ = Mock(return_value=False)
            with patch.object(weather, "urlopen", return_value=response), \
                    self.assertRaises(weather.WeatherError) as error:
                weather._get_json(weather.FORECAST_URL, {})
            self.assertEqual(error.exception.report["code"], "api_error")


if __name__ == "__main__":
    unittest.main()
