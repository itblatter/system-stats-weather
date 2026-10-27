---
name: system-stats
description: Collect local machine diagnostics (CPU load, memory, disk, uptime, top processes) via the read-only tools/system-stats/system-stats.py. Use when asked to check machine state, health, load, memory, disk usage or processes. Listening ports and a SHA-256 benchmark are available on explicit request.
---

# system-stats

1. For a generic "check the machine" request run the tool with
   `section` = `load,mem,disk` (also the tool's default).
2. `ports` and `bench` — only on direct request of those diagnostics or
   an explicit full set (`all`).
3. Interpret the report and answer with one line per area.
   On Windows, `load` reports `cpu_pct` over `sample_seconds`, not Unix
   load averages. Use the reported `disk.path` when identifying the volume.

Interface (arguments, sections, output and error format) and
interpretation rules: [reference](../../../knowledge/system-stats/system-stats.md)
(path relative to this skill).
