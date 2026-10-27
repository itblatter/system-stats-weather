---
name: weather
description: Check current weather and forecasts via Open-Meteo for the user's saved location or any named city or coordinates. Use for questions about weather, temperature, rain, snow, wind, or forecasts, including "погода у меня", "погода в городе" and "нужен ли зонт".
---

# Weather

1. Read the [reference](../../../knowledge/weather/weather.md). Run
   `tools/weather/weather.py` from the project root, passing JSON in `TOOL_ARGS`.
   Weather and location search both use Open-Meteo; no API key is required
   for the public non-commercial API.
2. Use the location explicitly requested in this turn. If the user says
   "here", "у меня", or omits it, use the saved default (empty arguments).
   If the tool returns `location_required`, ask for their city and country.
   Do not infer their location from the computer's language, timezone, or IP.
3. For `ambiguous_location`, use the user's country/region context to select
   a returned candidate and rerun with its latitude and longitude.
   If the context does not resolve the ambiguity, ask which candidate they mean.
   Never silently substitute another city when a search has no results.
4. Default to current weather and today's forecast (`days=1`).
   For tomorrow request `days=2`; for a week request `days=7`.
   The supported range is 1..16 days, starting today in the location's timezone.
5. State the resolved city/region, local data time and timezone, temperature,
   feels-like temperature, conditions, wind in m/s, and relevant precipitation.
   For a forecast include dates, min/max temperatures and precipitation
   probability. Distinguish current precipitation (mm) from forecast
   probability (%). Summarize in the user's language and link
   [Open-Meteo](https://open-meteo.com/) as the source.
   Current conditions are model data, not a live weather-station observation.
6. Report errors or unavailable (`null`) measurements honestly; never replace
   missing measurements with zero or invent a forecast beyond the returned dates.
7. To remember a location the user asks to save, write its confirmed city and
   country, or coordinates, to `tools/weather/location.json` as described in the
   reference. This local file is ignored by git. A one-off city request must
   not change the saved home location.
