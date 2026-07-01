# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Infrastructure-as-code for a data processing stack (Spark, Trino, Hive, PostgreSQL, SeaweedFS, JupyterHub,
Superset) running on **Docker Swarm** across 3 VMs on Huawei Cloud (Dataprev Gov-Cloud). There is no
application source code, build system, linter, or test suite here — the repo is shell scripts, Docker
stack YAML files, and service config files that get deployed to real VMs over SSH. Every component runs in
Docker — there is no native/host-installed service in the stack (PostgreSQL used to be; see below).

"Running the codebase" means executing scripts against the actual nodes, not running anything locally.

## Topology

| Node | Private IP | EIP | Role |
|---|---|---|---|
| node-1 | <ip-node-1> | <eip> | Swarm manager (leader), PostgreSQL, Hive Metastore, Spark Master, Spark History, Trino Coordinator, Redis, JupyterHub, Superset, Portainer, Swarmpit |
| node-2 | <ip-node-2> | none | Swarm manager, Spark Worker, Trino Worker, SeaweedFS |
| node-3 | <ip-node-3> | none | Swarm manager, Spark Worker, Trino Worker, SeaweedFS |

Swarm runs with **3 managers** (Raft quorum, tolerates 1 node loss) — not 1 manager + 2 workers. Only
node-1 has a public EIP; node-2/node-3 are private-only and have **no internet egress by default**. The
correct fix is a Huawei Cloud **NAT Gateway** (SNAT rule for the `<subnet-privada>` subnet) configured in the
VPC console. Do not route node-2/node-3 traffic through node-1 via ad-hoc iptables MASQUERADE + route
changes — it requires disabling "Source/Destination Check" on node-1's NIC (a real security control) and is
fragile; treat it as a last-resort temporary measure, never a standing configuration. If one specific node
still has no egress after the NAT Gateway is up, its Security Group is the next thing to check — nodes can
end up in a different/more restrictive group than their siblings. A node without egress can't `docker pull`
new images either, which blocks any service (including `global`-mode ones like `swarmpit_agent` or
`portainer-agent`) scheduled there.

## Deploy order

Scripts in `scripts/` and stacks in `stacks/` share one numbering sequence across both directories,
reflecting execution order, not file type:

```
00-ssh-setup.sh        node-1 only: generates SSH keypair, sets up passwordless access to node-2/node-3
01-base-setup.sh       ALL nodes: Docker install, formats/mounts the 3TB data disk at /data, sysctl tuning
02-swarm-init.sh       node-1 only: docker swarm init, prints the MANAGER join token
                       -> run `docker swarm join --token <TOKEN> <ip-node-1>:2377` on node-2 and node-3
03-swarm-networks.sh   node-1 only: creates the `datastack-net` overlay network, sets node labels
05-seaweedfs-stack.yml stack: SeaweedFS masters/volumes/filers (S3 storage layer)
06-datastack.yml       stack: PostgreSQL + Hive Metastore + Spark (+ History Server) + Trino
07-sync-config.sh      node-1 only: rsyncs config/ to node-2 and node-3 (see "Bind mounts" below)
08-apps-stack.yml      stack: Redis + JupyterHub + Superset + Portainer
09-deploy-all.sh       node-1 only: docker stack deploy for 05/06/08/10 in order, then initializes Superset
10-swarmpit-stack.yml  stack: Swarmpit (Swarm management UI)
```

`09-deploy-all.sh` expects the repo checked out at `/opt/datastack` on node-1 (it does
`cp /opt/datastack/stacks/*.yml /opt/stacks/`).

There used to be a `04-postgresql.sh` that installed PostgreSQL natively on node-1. It's gone — PostgreSQL
is now the `postgres` service inside `06-datastack.yml` (see "PostgreSQL is containerized" below). If you
see a reference to `04-postgresql.sh` anywhere (old docs, muscle memory), it's stale.

## The one rule that explains most bugs found here: use Swarm service DNS names

Every service-to-service connection string in this repo (Postgres URLs, Hive Metastore URIs, S3 endpoints,
Spark master URLs, Trino discovery URIs...) must use the **Swarm service name** of the target (e.g.
`postgres`, `spark-master`, `hive-metastore`, `seaweedfs-filer-1`, `trino-coordinator`) — never:

- **A VM hostname** (`node-1`, `node-2`, `node-3`). The overlay network's embedded DNS (127.0.0.11) only
  resolves Swarm service names and network aliases, not the underlying VM's hostname. Using one produces
  `no such host` and a crash loop.
- **The VM's real private IP** (`<ip-node-1>`, etc). Containers on `datastack-net` have no route to the
  VM's LAN interface at all — only to the overlay network (`10.0.3.0/24`-ish) and their own node's
  `docker_gwbridge` (`172.x.x.x`, NAT'd, node-local, does **not** span nodes). A connection attempt to the
  real IP just times out or resets; it never even reaches the other container. This is why PostgreSQL had
  to move from a native host install to a container (below) — a node-2/node-3 container had no way to reach
  a Postgres listening only on node-1's real IP.

The one exception: the human-facing UI URLs in comments/README (e.g. `http://<ip-node-1>:8090`) are
correct as-is — those are opened from a browser *outside* the cluster, where the real IP is exactly what
you want.

A related trap for **self-referencing** services (a master needing to bind to its own advertised address):
resolving your own Swarm service name under the default `vip` endpoint mode returns the *service VIP*, not
the task's real IP, and a literal `bind()` to that address fails (`cannot assign requested address`). Fix:
add `endpoint_mode: dnsrr` to that service's `deploy:` block (done for the SeaweedFS masters), or don't
force a bind address at all and let the app auto-detect its container IP (done for Spark Master/Workers —
removing `SPARK_MASTER_HOST`/`SPARK_WORKER_HOST` fixed a `BindException`).

## PostgreSQL is containerized, not native

PostgreSQL used to be installed directly on node-1's OS via `apt`/`systemctl` (old `04-postgresql.sh`). It
is now the `postgres` service in `06-datastack.yml`, for the DNS-name reason above: `trino-worker-node2`/
`trino-worker-node3` (which load every configured catalog, including `postgresql`) run on node-2/node-3 and
have no path at all to a Postgres listening only on node-1's real IP. As a container on `datastack-net`,
it's reachable as `postgres:5432` from any node.

- Data lives at `/data/postgres` (bind mount, on the 3TB disk, created by `01-base-setup.sh`), so it
  survives `docker stack rm`/redeploy.
- Init (`hive`/`superset` users and DBs) happens once via `config/postgres/init.sql` mounted at
  `/docker-entrypoint-initdb.d/init.sql` — only runs when the data directory is empty. Wipe
  `/data/postgres` if you need to re-run it (e.g. after changing `init.sql`).
- `command: postgres ... -c password_encryption=md5` is required: PostgreSQL 15 defaults to
  SCRAM-SHA-256, but Hive Metastore 3.1.3's bundled JDBC driver is too old to speak it
  (`authentication type 10 is not supported`). Don't remove this.
- If you ever containerize something else whose JDBC/client library might be similarly old, check this
  first before assuming a network problem.

This intentionally stays a **single instance on node-1**, not a Postgres cluster (Patroni/repmgr/streaming
replication) — that was evaluated and explicitly deferred; see "High-availability scope" below.

## Invariants that must stay consistent across files

These have been sources of real bugs before — check both sides when touching either:

- **Network name**: every stack's `networks:` block must reference `datastack-net` (hyphen), matching what
  `03-swarm-networks.sh` creates. A mismatched name (e.g. `datastack_net`) means the stack silently never
  gets the network and fails to deploy.
- **Placement labels**: `03-swarm-networks.sh` sets a `name` label per node (`name=node-1`, etc). Every
  `deploy.placement.constraints` in the stack files must reference `node.labels.name == node-X` — not
  `nodename` or any other key. A wrong label name means Swarm can never satisfy the constraint and the
  service sits in `Pending` forever.
- **Bind mounts are per-node, not cluster-wide.** `06-datastack.yml` and `08-apps-stack.yml` bind-mount
  paths under `/opt/datastack/config/...` (Trino coordinator/worker configs, `spark-defaults.conf`,
  `postgres/init.sql`). Since those services are constrained to specific nodes, the config directory must
  exist identically on whichever node the task lands on — Docker Swarm does not distribute bind-mount
  sources itself. This is why `07-sync-config.sh` exists: it rsyncs `/opt/datastack/config/` to node-2 and
  node-3 after any change on node-1, before deploying or redeploying `06-datastack.yml`/`08-apps-stack.yml`.
- **Swarmpit service names must be exactly `app`, `db`, `influxdb`, `agent` — no prefix.** The
  `swarmpit/agent` image has its event/healthcheck endpoints hardcoded to `http://app:8080/...`. Rename a
  service (e.g. to `swarmpit-app`) and the agent sits forever at `Waiting for Swarmpit...`, never sends
  stats, and **nothing logs an error anywhere** — the only symptom is an empty dashboard. This is the
  single highest-value thing to check first if Swarmpit stats stop working after editing the stack file.
- **Swarmpit app is pinned to `1.10`, not `:latest`.** `swarmpit/swarmpit:latest` (from `1.11-SNAPSHOT`
  onward, commit `d78d3e43` "harden auth") requires authentication on `POST /events`. The official
  `swarmpit/agent` image on Docker Hub hasn't been updated since 2019 and never sends an auth token on that
  call, so with `:latest` the agent's requests get silently rejected (`401 Unauthorized`) and the
  CPU/Memory/Disk dashboard graphs stay empty forever — no error surfaces anywhere except in the raw HTTP
  response if you capture traffic. `1.10` is the last tag before that change. Don't bump the `app` service
  past `1.10` unless the agent has also been updated to authenticate.
- **Swarmpit agent needs `DOCKER_API_VERSION=1.44`.** The installed engine (29.6.1) requires API >= 1.40;
  older values (e.g. `1.35`, from stale examples) crash-loop the agent (`panic: Event collector is broken`).
- **Same-port replicated/global services need `mode: host` on their published ports.** Docker Swarm's
  default ingress/routing-mesh publishing is a cluster-wide singleton per port — two services publishing
  the same port (even pinned to different nodes) fail with `port already in use ... as an ingress port`.
  This hit `05-seaweedfs-stack.yml` (3x master/volume/filer, one per node, same ports each) and
  `08-apps-stack.yml`'s `portainer-agent` (`mode: global`). Fix: publish with the long syntax and
  `mode: host` instead of the `"host:container"` shorthand.
- **SeaweedFS Volume uses port 8081, not SeaweedFS's documented default 8080** — 8080 collides with Trino
  Coordinator when both land on node-1 (both use `mode: host`, so it's a real same-node conflict, not just
  a same-service-replica one). SeaweedFS Volume's data dir bind-mounts to `/data/seaweedfs/volume` (created
  by `01-base-setup.sh`, on the 3TB disk) — not `/mnt/data/seaweedfs`, which is never created.
- **`bitnami/spark` no longer exists on Docker Hub.** Broadcom/VMware pulled the free `bitnami/*` catch-all
  images; `06-datastack.yml` uses `bitnamilegacy/spark:3.5` (a frozen mirror, last updated ~Aug 2025)
  instead. If a `bitnami/*` image pull ever starts failing with "not found" (not a network/auth error, an
  empty-repository error), check whether `bitnamilegacy/<name>` has the same tag before assuming anything
  else is wrong.
- **Trino 435 rejects properties that older Trino docs/examples still show.** `config.properties` must not
  set `query.max-total-memory-per-node` (defunct) or duplicate `node.data-dir` (belongs only in
  `node.properties`, not `config.properties`); `catalog/hive.properties` must not set
  `hive.s3select-pushdown.enabled` (defunct). Trino treats an unrecognized/defunct property as a **fatal**
  config error, not a warning — the whole process refuses to start.
- **Trino's `spiller-spill-path` must point somewhere the container's user (uid 1000) can actually write**,
  e.g. `/data/trino/spill` (inside the already-mounted `trino_data`/`trino_worker_data` volume) — not
  `/mnt/data/trino-spill`, an unmounted path on the container's own root filesystem that uid 1000 can't
  create (`AccessDeniedException`).
- **node-2/node-3 memory budgets: Spark Worker + Trino Worker share one 128GB node.** Both were originally
  sized as if each got a dedicated 128GB node (`SPARK_WORKER_MEMORY=90G`, Trino `-Xmx100G`, Docker
  reservation 80G each) — combined that's 160G+ reserved on a 128GB box, so Swarm leaves one of them
  permanently `Pending` ("insufficient resources"). Current budget: Spark Worker `SPARK_WORKER_MEMORY=40G`
  / Docker limit 48G / reservation 32G; Trino Worker JVM `-Xmx40G` / Docker limit 48G / reservation 32G —
  leaves headroom for SeaweedFS master/volume/filer and the OS on the same node. Keep the two roughly
  balanced if you resize either.

## Placeholder secrets

Passwords, keys, and secrets throughout `config/` and `stacks/*.yml` are literal placeholders ending in
`_CHANGE_ME` (Postgres users, Superset `SECRET_KEY`, SeaweedFS S3 keys). They are intentionally consistent
across files (e.g. the same `hive_password_CHANGE_ME` appears in `config/postgres/init.sql`,
`stacks/06-datastack.yml`, and `config/trino/*/catalog/postgresql.properties`) — if you change one for a
real deployment, change all occurrences together via `grep -rl CHANGE_ME`.

## High-availability scope

The Swarm control plane (3 managers) is HA by design. Individual services are **not**: PostgreSQL, Spark
Master, Trino Coordinator, Hive Metastore, and Redis are all pinned to node-1 via placement constraints
with no standby/replica. If node-1 goes down, those services stay down until it returns — this is the
current architecture, not a bug to silently "fix" by removing constraints (removing them would break the
bind-mount assumption above). A full Postgres cluster (streaming replication + failover) was evaluated and
explicitly deferred as a separate, larger project — don't add it unprompted.

## Known gotchas

- **`01-base-setup.sh`'s `apt-get upgrade` can hang forever when run non-interactively.** If a conffile
  (e.g. `/etc/ssh/sshd_config`) was already modified on the image, `dpkg` opens an interactive prompt with
  no TTY to answer it, and the script blocks indefinitely rather than failing. Recovery: `dpkg
  --force-confold --force-confdef --configure -a`, then re-run `apt-get` with
  `DEBIAN_FRONTEND=noninteractive -o Dpkg::Options::="--force-confold" -o Dpkg::Options::="--force-confdef"`.
  The script already runs with those flags from the start now.
- **`docker service logs` can hang/stall** when piped through more `grep`/`head` stages while the service
  is actively crash-looping — prefer redirecting to a file first (`docker service logs --raw --tail N svc >
  /tmp/x.log`, with a shell `timeout` wrapper) and then grepping the file.

## Validating cluster state

No test suite — validation is inspecting live Swarm state:

```bash
docker node ls                                   # cluster membership, manager status
docker node inspect <node> --format '{{.Spec.Labels}}'   # confirm name/role labels
docker network ls | grep datastack               # confirm datastack-net exists
docker service ls                                 # replica counts (N/M) across all stacks
docker stack ps <stack-name>                      # per-service task state/errors for a deployed stack
timeout 15 docker service logs --raw --tail 200 <service-name> > /tmp/x.log 2>&1  # logs, safely
```
