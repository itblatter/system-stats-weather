# Weather / Open-Meteo

Run `python tools/weather/weather.py` from the project root. Uses Python 3
stdlib on Windows, macOS and Linux. Requires internet access; requests have a
20-second timeout. The public Open-Meteo API needs no key for non-commercial
use. Weather: https://api.open-meteo.com/v1/forecast; location search:
https://geocoding-api.open-meteo.com/v1/search.

## Arguments and defaults

Arguments are a JSON object in `TOOL_ARGS`:

- `location`: city name, optionally with country/region, e.g. `Berlin, Germany`.
- `country_code`: optional two-letter ISO country filter, e.g. `DE`;
  only used with `location`.
- `latitude`, `longitude`: numeric coordinates, both required when used,
  ranges -90..90 and -180..180. An alternative to `location`.
- `days`: integer 1..16, default 1. Includes today in the location's timezone.
  Tomorrow is the second element when `days=2`.

Explicit location arguments override defaults. Otherwise the tool reads
`WEATHER_LOCATION` (a city string), then `tools/weather/location.json`.
It does not detect the user's position automatically.
If no location is configured, it returns `location_required` without a request.

PowerShell:

```powershell
$env:TOOL_ARGS='{"location":"Berlin, Germany","days":3}'
python tools/weather/weather.py
```

Coordinates:

```powershell
$env:TOOL_ARGS='{"latitude":52.52,"longitude":13.405,"days":2}'
python tools/weather/weather.py
```

bash/zsh:

```bash
TOOL_ARGS='{"location":"Berlin, Germany","days":3}' python3 tools/weather/weather.py
```

## Home location

After the user specifies their home city, create `tools/weather/location.json`:

```json
{"location": "Berlin, Germany", "country_code": "DE"}
```

This is an example, not the user's actual location. If the city name is
ambiguous, save the confirmed coordinates instead:

```json
{"latitude": 52.52, "longitude": 13.405}
```

The file is ignored by git. A default city can also be set for a terminal
session with `$env:WEATHER_LOCATION='Berlin, Germany'`.
To use the home location, clear any previous explicit city in `TOOL_ARGS`:

```powershell
$env:TOOL_ARGS='{}'
python tools/weather/weather.py
```

## Result and interpretation

Exit 0: JSON with `source`, resolved `location`, `timezone`,
`utc_offset_seconds`, `current`, `current_units`, `daily`, `daily_units`.
`daily` contains parallel arrays indexed by `daily.time`.
Weather codes follow WMO and have Russian `weather_description` values.
Units: °C, m/s, mm, and % for humidity/precipitation probability.
Times are local to the requested location, not the computer's timezone.
Keep the timezone and dates in the answer. `null` means unavailable, not zero.
Current values are model estimates; daily values are forecasts.

Exit 1: JSON with `error` and `code`:

- `invalid_arguments`: invalid JSON, unsupported keys or argument values.
- `location_required`: ask the user for their city/country.
- `location_not_found`: ask for a corrected name or coordinates.
- `ambiguous_location`: `candidates` provides names, regions and coordinates.
  Use user-provided context to resolve the city, then retry with coordinates.
- `network_error` / `api_error`: explain the failure; do not invent weather.

Use the returned `source` link to attribute Open-Meteo.
API documentation: https://open-meteo.com/en/docs and
https://open-meteo.com/en/docs/geocoding-api.

## Tests

```powershell
python -B -m unittest discover -s tools/weather -p test_weather.py -v
```

Tests use synthetic responses and do not access the network.
