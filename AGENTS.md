# AGENTS.md

## System diagnostics

Machine state (CPU, memory, disk, uptime, processes, ports, benchmark) is
collected only via the `system-stats` skill. Never gather these metrics
with ad-hoc shell commands; read-only: never kill processes or change
system settings.
