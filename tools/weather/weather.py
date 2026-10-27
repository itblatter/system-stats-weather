#!/usr/bin/env python3
"""Open-Meteo weather as JSON. Stdlib only; arguments arrive in TOOL_ARGS."""

import json
import math
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_LOCATION_FILE = Path(__file__).with_name("location.json")
HTTP_TIMEOUT = 20
CURRENT_FIELDS = (
    "temperature_2m", "relative_humidity_2m", "apparent_temperature",
    "is_day", "precipitation", "weather_code", "wind_speed_10m",
)
DAILY_FIELDS = (
    "weather_code", "temperature_2m_max", "temperature_2m_min",
    "precipitation_sum", "precipitation_probability_max",
    "wind_speed_10m_max", "sunrise", "sunset",
)
LOCATION_KEYS = {"location", "latitude", "longitude", "country_code"}
WEATHER_CODES = {
    0: "Ясно", 1: "Преимущественно ясно", 2: "Переменная облачность",
    3: "Пасмурно", 45: "Туман", 48: "Туман с изморозью",
    51: "Слабая морось", 53: "Умеренная морось", 55: "Сильная морось",
    56: "Слабая переохлаждённая морось", 57: "Сильная переохлаждённая морось",
    61: "Слабый дождь", 63: "Умеренный дождь", 65: "Сильный дождь",
    66: "Слабый переохлаждённый дождь", 67: "Сильный переохлаждённый дождь",
    71: "Слабый снег", 73: "Умеренный снег", 75: "Сильный снег",
    77: "Снежные зёрна", 80: "Слабый ливень", 81: "Умеренный ливень",
    82: "Сильный ливень", 85: "Слабый снежный ливень",
    86: "Сильный снежный ливень", 95: "Гроза",
    96: "Гроза со слабым градом", 99: "Гроза с сильным градом",
}


class WeatherError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.report = {"error": message, "code": code, **details}


def _get_json(url, params):
    request = Request(
        url + "?" + urlencode(params),
        headers={"User-Agent": "weather-skill/1.0", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:
            result = json.load(response)
    except HTTPError as e:
        e.close()
        raise WeatherError("api_error", f"Open-Meteo returned HTTP {e.code}") from e
    except (URLError, TimeoutError, OSError) as e:
        raise WeatherError("network_error", f"Cannot reach Open-Meteo: {e}") from e
    except (ValueError, UnicodeError) as e:
        raise WeatherError("api_error", "Open-Meteo returned invalid JSON") from e
    if not isinstance(result, dict):
        raise WeatherError("api_error", "Open-Meteo returned an unexpected response")
    if result.get("error"):
        raise WeatherError("api_error", str(result.get("reason", "Open-Meteo error")))
    return result


def _coordinate(value, name, limit):
    if (
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or not -limit <= value <= limit
    ):
        raise ValueError(f"{name} must be a finite number in {-limit}..{limit}")
    return value


def _validate_location(args):
    location = args.get("location")
    has_coordinates = "latitude" in args or "longitude" in args
    if "location" in args:
        if not isinstance(location, str) or not location.strip():
            raise ValueError("location must be a non-empty city name")
        args["location"] = location.strip()
    if has_coordinates:
        if location is not None:
            raise ValueError("use either location or latitude/longitude, not both")
        _coordinate(args.get("latitude"), "latitude", 90)
        _coordinate(args.get("longitude"), "longitude", 180)
    if "country_code" in args:
        country = args["country_code"]
        if (
            not isinstance(country, str) or len(country) != 2
            or not country.isascii() or not country.isalpha()
        ):
            raise ValueError("country_code must be a two-letter ISO country code")
        if location is None:
            raise ValueError("country_code requires location")
        args["country_code"] = country.upper()
    if location is None and not has_coordinates:
        raise WeatherError(
            "location_required",
            "Specify a city and country, coordinates, WEATHER_LOCATION, "
            "or tools/weather/location.json",
        )
    return args


def parse_args():
    args = json.loads(os.environ.get("TOOL_ARGS", "{}"))
    if not isinstance(args, dict):
        raise ValueError("TOOL_ARGS must be a JSON object")
    unknown = set(args) - LOCATION_KEYS - {"days"}
    if unknown:
        raise ValueError(f"unknown argument(s): {', '.join(sorted(unknown))}")
    days = args.get("days", 1)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 16:
        raise ValueError("days must be an integer in 1..16")
    if not (set(args) & LOCATION_KEYS):
        default = os.environ.get("WEATHER_LOCATION", "").strip()
        if default:
            args["location"] = default
        elif DEFAULT_LOCATION_FILE.exists():
            try:
                saved = json.loads(DEFAULT_LOCATION_FILE.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as e:
                raise ValueError(f"cannot read saved location: {e}") from e
            if not isinstance(saved, dict) or set(saved) - LOCATION_KEYS:
                raise ValueError("saved location must be an object with location fields only")
            args.update(saved)
    args["days"] = days
    return _validate_location(args)


def resolve_location(args):
    if "latitude" in args:
        return {"latitude": args["latitude"], "longitude": args["longitude"]}
    params = {"name": args["location"], "count": 5, "language": "ru", "format": "json"}
    if "country_code" in args:
        params["countryCode"] = args["country_code"]
    results = _get_json(GEOCODING_URL, params).get("results", [])
    if not isinstance(results, list):
        raise WeatherError("api_error", "Open-Meteo returned invalid location results")
    if not results:
        raise WeatherError("location_not_found", f"No location found for {args['location']!r}")
    candidates = []
    for row in results:
        if not isinstance(row, dict) or not {"latitude", "longitude", "name"} <= row.keys():
            raise WeatherError("api_error", "Open-Meteo returned an incomplete location")
        try:
            _coordinate(row["latitude"], "latitude", 90)
            _coordinate(row["longitude"], "longitude", 180)
        except ValueError as e:
            raise WeatherError("api_error", str(e)) from e
        candidates.append({
            key: row[key] for key in (
                "name", "country", "country_code", "admin1", "admin2",
                "latitude", "longitude", "timezone",
            ) if key in row
        })
    if len(candidates) > 1:
        raise WeatherError(
            "ambiguous_location", "Several locations match; specify a region or coordinates",
            candidates=candidates,
        )
    return candidates[0]


def _description(code):
    if code is None:
        return None
    return WEATHER_CODES.get(code, f"Неизвестный погодный код: {code}")


def collect_weather(args):
    location = resolve_location(args)
    data = _get_json(FORECAST_URL, {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "current": ",".join(CURRENT_FIELDS),
        "daily": ",".join(DAILY_FIELDS),
        "timezone": "auto",
        "forecast_days": args["days"],
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
    })
    current, daily = data.get("current"), data.get("daily")
    if (
        not isinstance(current, dict) or not {"time", *CURRENT_FIELDS} <= current.keys()
        or not isinstance(daily, dict)
        or not isinstance(data.get("current_units"), dict)
        or not isinstance(data.get("daily_units"), dict)
        or not data.get("timezone")
        or "utc_offset_seconds" not in data
    ):
        raise WeatherError("api_error", "Open-Meteo returned incomplete weather data")
    for key in ("time", *DAILY_FIELDS):
        if not isinstance(daily.get(key), list) or len(daily[key]) != args["days"]:
            raise WeatherError("api_error", f"Open-Meteo returned an incomplete daily field: {key}")
    current["weather_description"] = _description(current["weather_code"])
    daily["weather_description"] = [_description(code) for code in daily["weather_code"]]
    return {
        "source": "https://open-meteo.com/",
        "location": location,
        "timezone": data["timezone"],
        "utc_offset_seconds": data["utc_offset_seconds"],
        "current": current,
        "current_units": data["current_units"],
        "daily": daily,
        "daily_units": data["daily_units"],
    }


def main():
    try:
        args = parse_args()
    except WeatherError as e:
        report, code = e.report, 1
    except (ValueError, TypeError) as e:
        report, code = {"error": f"invalid arguments: {e}", "code": "invalid_arguments"}, 1
    else:
        try:
            report, code = collect_weather(args), 0
        except WeatherError as e:
            report, code = e.report, 1
    # ASCII escapes keep JSON portable in Windows terminals; json.loads restores Unicode.
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
