# Dual MT5 Accounts

The two profiles use separate terminal processes and separate child Python
processes:

- Prop firm: `11911448 / FundingPips2-SIM` on `C:\Program Files\MetaTrader 5 Terminal\terminal64.exe`.
- Synthetic indices: `41204968 / Deriv-Demo` on `C:\Program Files\Deriv VU MT5 Terminal\terminal64.exe`.

## Start / stop

Better to use the launcher scripts (they set the prop env vars, redirect logs,
and kill the whole tree):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_dual_live.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\stop_dual_all.ps1
```

`start_dual_live.ps1` refuses to start when a supervisor already exists.
`run_dual_accounts.py` also takes a pid lock
(`logs\dual_accounts.lock`, atomic `O_EXCL` create) so a second supervisor
exits immediately instead of racing the status file. Status writes retry on
Windows file-lock errors instead of crashing the supervisor.

Standalone (advanced) run:

```powershell
python run_dual_accounts.py --once   # dry run, one evaluation per profile
python run_dual_accounts.py --live   # continuous live submission
```

## Interpreter trap (read before inspecting processes)

The hermes venv python (`C:\Users\Dave\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`)
is a launcher: every invocation also starts a second process using the uv base
interpreter (`C:\Users\Dave\AppData\Roaming\uv\python\cpython-3.11-...\python.exe`,
same command line, parent = the venv process). Each logical process therefore
looks like a "pair" — this is normal, not a duplicate.

- Always start the supervisor with the **hermes venv** python (see
  `start_dual_live.ps1`). Running the **uv base** python directly with
  `run_dual_accounts.py` makes children crash on `ModuleNotFoundError: dotenv`
  (base interpreter has no venv site-packages) and the synthetic watcher
  respawn-loops (`restarts` climbing in the status file).
- MCP servers show the same pattern (`server.py` × hermes + `server.py` × uv
  base): only N logical servers, not 2N.
- `stop_dual_all.ps1` kills by command-line match so both launcher and worker
  halves are removed together; avoid killing single PIDs.

## Prop credentials

The prop password is read from the process environment (set by
`start_dual_live.ps1`) or `.env`; it is not duplicated in the launcher.
The old `MT5AutoTraderSIM` scheduled task should remain disabled while the
dual supervisor is running to avoid duplicate prop cycles and terminal IPC
contention.
