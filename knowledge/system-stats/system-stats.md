# system-stats reference

Run from the project root; args go as JSON in `TOOL_ARGS`, result prints
to stdout as JSON:

Windows (PowerShell):

```powershell
$env:TOOL_ARGS='{"section":"load,mem,disk"}'
python tools/system-stats/system-stats.py
```

macOS / Linux (bash or zsh):

```bash
TOOL_ARGS='{"section":"load,mem,disk"}' python3 tools/system-stats/system-stats.py
```

## Arguments

- `section` — a name or comma-separated list; default `load,mem,disk`.
- `top_n` — processes in `top`, 1..100, default 5.
- `mb` — megabytes hashed in `bench`, 1..1024, default 64.

## Sections

- `load` — Windows: `cpu_pct` (0..100 across logical CPUs), measured over
  approximately one second (`sample_seconds`), and `cpu_count`.
  macOS / Linux: load average (1/5/15 min) and `cpu_count`.
  Windows does not supply Unix load averages; those keys are omitted.
- `mem` — total / used / available physical RAM in bytes
- `disk` — `/` usage on macOS / Linux; on Windows, the root of the current
  working directory's volume (e.g. `D:\`). Includes `path` and `used_pct`.
- `uptime` — system uptime
- `top` — top processes by CPU (`top_n`). On Windows, uses CIM performance
  counters from their latest sampling interval; `cpu_pct` is normalized
  across logical CPUs and `mem_pct` uses the process working set.
- `ports` — listening TCP ports
- `bench` — SHA-256 throughput benchmark (`mb`)
- `all` — all sections; explicit only, cannot be combined with others

No third-party Python packages are needed. Windows uses `GetSystemTimes`,
`GlobalMemoryStatusEx` and `GetTickCount64` via `ctypes`; `top` and `ports`
require Windows PowerShell with CIM / `Get-NetTCPConnection` (Windows 10/11).
The CPU sample uses the calling processor group on Windows systems with more
than 64 logical CPUs, so it may not represent the whole machine.

## Output and error format

- One requested section — its result prints unwrapped; several — an
  object keyed by section name.
- Invalid arguments — `{"error": "invalid arguments: ..."}`, exit 1.
- A failed collector — `"error"` inside its section's result, exit 0.
  Inspect the error. If the execution sandbox blocks system access,
  request permission through the environment's approval mechanism and
  rerun this same tool outside the sandbox. Never bypass a denied approval
  or collect metrics with alternative commands. If the approved run still
  fails, report the error; do not claim successful diagnostics.

## Interpretation

- On macOS / Linux, compare `loadavg_5m` with `cpu_count`.
- On Windows, report `cpu_pct` and `sample_seconds`. A single short sample
  does not establish sustained CPU load; do not interpret it as a load average.
- Flag RAM when available is below ~10% of total; flag disk when
  `used_pct` is above 90%.
