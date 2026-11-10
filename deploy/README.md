# deploy/

Everything that installs, builds, ships or runs the server. Step-by-step
versions of what the scripts do are in [docs/INSTALL_MANUAL.md](../docs/INSTALL_MANUAL.md).

| File | What it is |
|---|---|
| `setup.sh` | Docker path in one command: `.env`, image build, start, health wait; `--verify` proves `lemma True by simp` through the API; `--install-docker`, `--build-heaps "..."`, `--no-build`, `--isabelle-version <V>` |
| `native_setup.sh` | Native Linux path: `--system` installs apt packages and Isabelle, then `.venv`, the Isabelle component, the Scala backend, the ML heap cap, `.env`; `--systemd` installs the service; `--verify` as above |
| `Dockerfile` | One image for every Isabelle version (`ISABELLE_VERSION` build arg); `Dockerfile.export` + `export_turnkey.sh` ship a ready-made image with heaps |
| `isabelle-pool-server.service` | systemd unit template filled by `native_setup.sh --systemd` |
| `lib.sh` | Shared helpers (status lines, logged runs, health wait, smoke test) |
| `monitoring/` | Prometheus, Grafana and cAdvisor configuration used by `docker-compose.yml` |
| `apt_packages.txt`, `isabelle_mirrors.txt` | Package list and Isabelle download mirrors |

## Sizing

Budget about 30 GB of disk (Isabelle plus heaps) and 16 GB or more of RAM. Each
Isabelle session is 1.5 to 4 GB resident; `ISABELLE_POOL_SIZE` times about 4 GB
plus one `isabelle build` must fit. Heaps are version-locked: never share a
user-data directory or volume between two Isabelle versions.

## Operating

- **Tune `.env`**: `ISABELLE_POOL_SIZE`, `ISABELLE_INITIAL_SESSIONS`,
  `ISABELLE_MEMORY_PRESSURE_THRESHOLD`, `ISABELLE_ML_MAXHEAP_MB`,
  `ISABELLE_ADMIN_TOKEN`; apply with
  `docker compose up -d --force-recreate isabelle-pool-server`.
- **Logs**: `docker compose logs -f isabelle-pool-server` (also `logs/server.log`);
  natively `logs/server.stdout.log` and `logs/gateway-jvm.log`.
- **Upgrade**: `git pull && ./deploy/setup.sh` (Docker) or re-run
  `native_setup.sh` after a pull or an Isabelle upgrade. A Scala/ML change needs
  `isabelle scala_build` and a server restart; new sessions pick up `REPL.ML`
  at their start.
- **New Isabelle**: `./deploy/setup.sh --isabelle-version <V>`; rebuild heaps
  with `--build-heaps`.
- **Share an image**: `./deploy/export_turnkey.sh --save`
  ([manual A6](../docs/INSTALL_MANUAL.md#a6-turnkey-image-no-build-heaps-included)).
- **Network**: the server listens on `0.0.0.0:8000` with no authentication.
  Firewall it (`sudo ufw allow from <your-ip> to any port 8000`) or tunnel
  (`ssh -N -L 8000:localhost:8000 user@server`). The admin console at `/admin`
  needs `ISABELLE_ADMIN_TOKEN` for destructive actions but is itself public.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/readyz` stays 503 for minutes | Gateway JVM still starting (1 to 2 min is normal) or it died: check `logs/gateway-jvm.log`; natively also `isabelle components -l` and re-run `server/repl/Admin/init` |
| `Not found: py4j` on start (Docker) | Stale `isabelle_user_data` volume: `docker compose exec isabelle-pool-server ./server/repl/Admin/init` (ISSUES.md Bug 7) |
| `Fontconfig head is null` in the JVM log (native) | Install `fontconfig fonts-dejavu-core` (`native_setup.sh --system` does) |
| `Unknown JAVA_HOME` from `isabelle` (native) | A global `JAVA_HOME` is exported; Isabelle resolves its own JDK; unset it in the server's environment |
| HTTP 503 `memory_pressure` | The admission gate is protecting the host: lower `ISABELLE_POOL_SIZE`, raise `mem_limit`, or wait for idle sessions to be evicted |
| First `acquire` takes many minutes | A heap for a heavy import set is being built: prebuild it (`setup.sh --build-heaps` / `isabelle build -b`) |
| `verify_chunk` returns `success=false, timed_out=true` | The budget expired; `stuck_line` names the looping command; it was rolled back. Retry with a larger `timeout` or a different proof |
| HTTP 504 `BackendTimeout` on `sledgehammer` | The run outlived its budget (ATP timeout plus a grace of half the timeout, 30 s at least): the goal is too hard as stated; split it rather than raising `timeout_s` |
| A stray `poly` process after a bigstep build timed out | Known: the build wrapper is killed but the ML process keeps the pipes open; kill it by hand |
| `success=True` but the agent is not done | Working as intended: check `proof_open` / `used_sorry` |
| MCP subprocess dies at once, `enter_theory` hangs | See [mcp_servers/README.md](../mcp_servers/README.md), "Smoke test" |
