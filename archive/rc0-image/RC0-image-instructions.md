> **Archived 2026-10-07.** This is the Isabelle 2026-**RC0** track: a hand-assembled image
> (docker cp into a 2025-2 base container + commit) that predates the multi-version
> `deploy/Dockerfile`. The current turnkey recipe is `deploy/export_turnkey.sh` +
> `deploy/Dockerfile.export` (README "A7. Turnkey image"). Kept only for the provenance of
> the `isabelle-pool-server:2026rc0-turnkey` image.

# Isabelle Pool Server turnkey image — distribution & recipient runbook

This document covers the **pre-built Docker image** of the Isabelle Pool Server
(Isabelle **2026-RC0** track), distributed for reproducing published results.
The image is self-contained: Isabelle, the Scala REPL backend, the API server,
all pre-built session heaps, and the ML heap cap — no build steps required.

## For recipients: run it

Prerequisites: Docker (Engine 20.10+ or Docker Desktop), ~35 GB free disk,
16 GB+ RAM recommended.

```bash
# 1. Load the image (one time, a few minutes — it is large)
docker load < isabelle-pool-server-2026rc0-turnkey.tar.gz

# 2. Run it — the server starts automatically (foreground entrypoint)
docker run -d --name isabelle-pool-server \
  -p 8000:8000 \
  --memory 14g \
  -e ISABELLE_ADMIN_TOKEN="$(openssl rand -hex 16 2>/dev/null || echo change-me)" \
  isabelle-pool-server:2026rc0-turnkey

# 3. Verify (first start takes 1–2 minutes: gateway JVM spawn)
curl http://localhost:8000/healthz     # {"status":"alive"}
curl http://localhost:8000/            # full health: gateway_alive, pool, memory
```

That's all — the API is serving. Useful next steps:

- **Admin console:** `http://localhost:8000/admin` (uses the token you passed).
- **Logs:** `docker logs -f isabelle-pool-server`.
- **Heaps included** (visible at `GET /api/v1/heaps/available`): Pure, HOL,
  HOL-Library, HOL-Computational_Algebra, HOL-Analysis, HOL-Number_Theory,
  HOL-Combinatorics.
- **Attach an agent:** point an MCP client at this server exactly as in
  README.md "Connecting the MCP server to an agent" (`ISABELLE_MCP_SERVER_URL` for the stepwise server, `ISABELLE_MCP_LSP_GYM_URL` for the lsp server, both `http://localhost:8000`).
- **Persistence (optional):** add `-v isabelle_user_data:/root/.isabelle` to
  keep heap-pool manifests and user settings across container replacement.
  Without a volume the container is fully self-contained and disposable.
- **Memory sizing:** `--memory 14g` with the default `ISABELLE_POOL_SIZE=3`
  leaves headroom for one `isabelle build`. Every ML process is hard-capped
  (default 9 GB `--maxheap`); a build that exceeds it fails cleanly instead of
  OOM-killing the container. Override with `-e ISABELLE_ML_MAXHEAP_MB=<MB>`.

Reproducibility: the image tag embeds the source commit —
`docker image inspect isabelle-pool-server:2026rc0-turnkey --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`
gives the exact Isabelle Pool Server commit the server was built from. Cite it in
reproduction reports.

## For maintainers: how the image was produced

Executed on the maintainer machine from the `2026-RC0` branch (after merging
`main`):

```bash
# 1. Clean image via build_rc0_image.sh (the validated scripted assembly —
#    docker cp into a base container + commit; the declarative BuildKit path
#    in Dockerfile.rc0 is currently blocked by the JAVA_HOME env quirk).
#    app/ in the staging dir is a fresh git archive of the branch:
./deploy/build_rc0_image.sh ~/isabelle2026-build isabelle-pool-server:2026rc0-clean

# 2. Bake the volume state in — LEAN bake: only heaps/ + etc/ (settings with
#    the ML heap cap, component registration). The 5.6 GB contrib tree is
#    already in the base image layer (verified byte-identical to the volume's),
#    so baking it again would just duplicate a layer:
docker cp isabelle-pool-server-rc0:/root/.isabelle ~/isabelle2026-build/export_isabelle_home
#    prune export_isabelle_home to heaps/ + etc/ only, then:
docker build -f deploy/Dockerfile.export -t isabelle-pool-server:2026rc0-turnkey ~/isabelle2026-build
#    Dockerfile.export (in the repo): FROM ...:2026rc0-clean,
#    COPY export_isabelle_home /root/.isabelle, revision LABEL,
#    server-default ENVs, CMD ["bash", "./server/repl/Admin/container_entrypoint.sh"]

# 3. Cold test WITHOUT the volume (this is the recipient experience)
docker run -d --name turnkey-test -p 8002:8000 --memory 14g isabelle-pool-server:2026rc0-turnkey
curl --retry 30 --retry-delay 5 --retry-connrefused http://localhost:8002/healthz
#    + one acquire with an HOL-Library import + one trivial bigstep
docker rm -f turnkey-test

# 4. Export
docker save isabelle-pool-server:2026rc0-turnkey | gzip > isabelle-pool-server-2026rc0-turnkey.tar.gz
```
