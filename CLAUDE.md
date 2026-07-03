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
- **`mode: host` publishing only reaches the container if the process listens on 0.0.0.0, not just the
  overlay IP.** With `mode: host`, docker-proxy forwards the host port to the container via its
  `docker_gwbridge` interface (172.18.x), *not* the overlay interface (10.0.3.x). SeaweedFS's `-ip=<name>`
  flag makes it bind its listener only to the overlay IP, so the host port answered `ERR_CONNECTION_REFUSED`
  even though the service was healthy and reachable by service name from inside the overlay. Fix: every
  SeaweedFS command (master/volume/filer) sets `-ip.bind=0.0.0.0`. Any other host-mode-published container
  that lets you pin its bind address needs the same treatment — a service being reachable by service name
  from another container does *not* mean its host-published port works.
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

The Swarm control plane (3 managers) is HA by design. **Spark Master is now HA too** (2026-07-04): 3
instances (`spark-master-1/2/3`, one per node) with `recoveryMode=ZOOKEEPER` against the `zookeeper` stack
(`zk-1/2/3`), failover tested at ~40s (kill the leader container, a standby is elected and workers/clients
recover). See "Spark Master HA via ZooKeeper" below for the wiring.

Everything else is **not** HA: PostgreSQL, Trino Coordinator, Hive Metastore, and Redis are all pinned to
node-1 via placement constraints with no standby/replica — and node-1 remains a SPOF for the *rest* of the
platform even after the Spark Master fix: Traefik (single ingress, 80/443), Keycloak (the only IdP), and
Airflow (scheduler/dag-processor/triggerer, all pinned to node-1) live there too, plus any Spark driver
running in **client mode** via `docker exec` into a master container (the driver itself isn't HA — only
which master it talks to is). If node-1 goes down, those services stay down until it returns — this is the
current architecture, not a bug to silently "fix" by removing constraints (removing them would break the
bind-mount assumption above). A full Postgres cluster (streaming replication + failover) was evaluated and
explicitly deferred as a separate, larger project — don't add it unprompted.

## Spark Master HA via ZooKeeper (`stacks/13-zookeeper.yml`)

The single-instance `spark-master` service is gone — replaced by `spark-master-1/2/3` (one per node,
`06-datastack.yml`) with Spark Standalone's built-in `recoveryMode=ZOOKEEPER`, backed by a dedicated
3-node ensemble (`zookeeper` stack, `zk-1/2/3`, one per node). Points worth knowing before touching either:

- **The ZK stack needs `endpoint_mode: dnsrr`, same root cause as the SeaweedFS masters.** Each ZK node
  binds its quorum/election ports (2888/3888) on the address its *own* `server.N=zk-N:...` entry resolves
  to — the official `zookeeper:3.9` image's entrypoint does not rewrite that entry to `0.0.0.0`. Under the
  default `vip` endpoint mode, `zk-N` resolves to the service VIP, and `bind()` to a VIP fails (`cannot
  assign requested address`). `dnsrr` makes it resolve to the task's real IP instead. No ports are
  published, so there's no ingress conflict to worry about (unlike SeaweedFS, which also had to solve
  `mode: host` + `-ip.bind=0.0.0.0` for its published ports — not needed here).
- **`SPARK_MASTER_OPTS` (a JVM `-D` flags env var), not `spark-defaults.conf`, carries the ZK config**
  (`-Dspark.deploy.recoveryMode=ZOOKEEPER -Dspark.deploy.zookeeper.url=zk-1:2181,zk-2:2181,zk-3:2181
  -Dspark.deploy.zookeeper.dir=/spark-ha`). Reason: the bitnami image launches the master daemon via
  Spark's `SparkClassCommandBuilder`, which reads `SPARK_MASTER_OPTS` and appends it to the JVM command
  line for the `Master` class specifically — `spark-defaults.conf` is read by the `SparkSubmit` launcher
  for *applications* (drivers), not by the master/worker daemons themselves. Putting recovery config in
  `spark-defaults.conf` would silently do nothing for the masters.
- **Only `spark-master-1` mounts `/opt/datastack/jobs` (ro) and `/data/shared`** — those bind-mount sources
  only exist on node-1's filesystem. This is deliberate: node-1 is the one submission node (Airflow's
  scheduler and `scripts/run-spark-job.sh` both `docker exec` into whichever spark-master container is
  local to node-1, which is always `spark-master-1` given the placement constraint) — it does **not** need
  to be the ZK/Spark leader for job submission to work, since `spark-submit`'s `--master`/`spark.master`
  handles leader discovery itself once given all three URLs.
- **Every client must use the multi-master URL**, never a single name: `spark://spark-master-1:7077,
  spark-master-2:7077,spark-master-3:7077`. Spark Standalone's HA client logic tries each host in order and
  follows redirects to whichever is the current leader; pointing at just one master defeats the purpose
  (that master could be a standby, or — since the old singular `spark-master` service was deleted outright
  — simply fail to resolve). Updated in `config/spark/spark-defaults.conf`, `scripts/run-spark-job.sh`,
  `dags/medallion_beneficios.py`, `config/jupyterhub/jupyterhub_config.py` (`SPARK_MASTER` env for notebook
  drivers), `notebooks/faker-landing.ipynb`, and the job docstrings in `jobs/`.
- **In-flight jobs survive a master failover.** Once a Spark application's executors are registered, they
  keep running and reporting to the driver independently of the master; the master is only needed for
  initial registration/resource negotiation. Validated: triggering `spark-master-1` down mid-job did not
  kill the running executors.
- **Traefik's `spark` router** (`config/traefik/dynamic/dynamic.yml`) load-balances across all three UIs
  (`http://spark-master-1:8090`, `-2`, `-3`) rather than pointing at one — see the comment in that file for
  why a standby-aware healthcheck isn't possible (Traefik's healthcheck is just HTTP 200, and a standby
  master answers 200 too, serving its own "Status: STANDBY" page with a pointer to the current leader).

## Trino: new `delta` catalog to query the medallion Delta tables; `hive` catalog had blank S3 creds

Spark writes the landing/bronze/prata/ouro Delta tables straight to `s3a://...` paths (`.write.save(path)`,
no `saveAsTable`), so they were never registered in the Hive Metastore and didn't show up as SQL tables
anywhere. Added `config/trino/{coordinator,worker}/catalog/delta.properties` (`connector.name=delta_lake`,
same `hive.metastore.uri` as the `hive` catalog, plus `delta.register-table-procedure.enabled=true` — the
`register_table` procedure that attaches an already-existing Delta table by path is off by default). A
new/changed catalog `.properties` file needs `docker service update --force datastack_trino-coordinator`
(and workers) to be picked up — Trino here runs static catalog management, it doesn't hot-reload the catalog
directory.

**S3 config must use the native filesystem (`fs.native-s3.enabled` + `s3.*`), not the legacy
`hive.s3.*` properties** — found the hard way (two errors in sequence testing `CREATE SCHEMA`):
1. `hive.s3.*` with blank keys → auth never worked (fixed the blank keys first, seemed plausible).
2. Still failed: `ClassNotFoundException: org.apache.hadoop.fs.s3a.S3AFileSystem`. This Trino 435 image's
   `hive` **and** `delta-lake` plugins both ship only `trino-filesystem-s3-435.jar` (Trino's own native S3
   client) — no `hadoop-aws` jar, so the legacy Hadoop-backed `hive.s3.*` filesystem can never work
   regardless of credentials. **Both catalogs never worked at all before this fix**, not just the blank-key
   half of it. Correct config for both `hive.properties` and `delta.properties`:
   ```
   fs.native-s3.enabled=true
   s3.endpoint=http://seaweedfs-filer-1:8333
   s3.region=us-east-1
   s3.path-style-access=true
   s3.aws-access-key=seaweedfs_access_CHANGE_ME
   s3.aws-secret-key=seaweedfs_secret_CHANGE_ME
   ```
   (`s3.region` is required by the native client even against a non-AWS S3-compatible endpoint; any value
   works since SeaweedFS ignores it.)

**The Hive Metastore ALSO needs S3A — the `CREATE SCHEMA WITH location`/`register_table` error was
`HIVE_METASTORE_ERROR`, i.e. it came from HMS, not Trino's filesystem.** Fixing Trino's S3 config above
did NOT resolve it: `CREATE SCHEMA delta.ouro WITH (location = 's3a://...')` makes the `apache/hive:3.1.3`
metastore itself `mkdirs` the schema dir, and HMS had neither `S3AFileSystem` on its classpath nor an
`fs.s3a.*` config → it bounced back `ClassNotFoundException: S3AFileSystem` (or `No FileSystem for scheme s3`)
as `HIVE_METASTORE_ERROR` during query *planning* (0ms running — the tell it's the metastore, not the scan).
Fix (`06-datastack.yml` hive-metastore service + `config/hive/core-site.xml`):
- The `hadoop-aws-3.1.0.jar` + `aws-java-sdk-bundle-1.11.271.jar` are already in the image at
  `/opt/hadoop/share/hadoop/tools/lib/` but off-classpath; put them on via
  `HADOOP_CLASSPATH` env (the entrypoint does `HADOOP_CLASSPATH=...:$HADOOP_CLASSPATH`, preserving it).
- Mount `config/hive/core-site.xml` (same `fs.s3a.endpoint`/keys/`path.style.access`/`connection.ssl.enabled`
  as Spark) at `/opt/hive/conf/core-site.xml` — HMS's `HADOOP_CONF_DIR` is `/opt/hive/conf` (not
  `/opt/hadoop/etc/hadoop`; if you `docker exec ... hadoop fs -ls s3a://...` to test, pass
  `-e HADOOP_CONF_DIR=/opt/hive/conf` or it reads the wrong, empty core-site and fails on credentials).
- **`IS_RESUME: "true"` is mandatory on the hive-metastore service** (was missing): the image entrypoint does
  `SKIP_SCHEMA_INIT="${IS_RESUME:-false}"` and otherwise runs `schematool -initSchema` on *every* start. The
  Postgres `hive_metastore` schema is already initialized, so a restart without `IS_RESUME=true` dies with
  `relation "BUCKETING_COLS" already exists` (exit 1) — the service only ever came up on the very first boot
  (empty DB) and silently couldn't restart. Redeploying `datastack` to apply the S3A change is what exposed
  this latent bug; both are fixed together now.

To attach the existing gold tables (or any Delta table under `s3a://<bucket>/...`) into the `delta` catalog,
run in Trino (e.g. from DBeaver once OAuth2-authenticated — there's no headless/service-account path to
Trino's HTTP API here since `directAccessGrantsEnabled=false` on the `trino` Keycloak client):
```sql
CREATE SCHEMA IF NOT EXISTS delta.ouro WITH (location = 's3a://ouro/pda/beneficios-emitidos/');
CALL delta.system.register_table(schema_name => 'ouro', table_name => 'fat_uf',
    table_location => 's3a://ouro/pda/beneficios-emitidos/fat_uf');
-- repeat per table (fat_especie, fat_banco, kpis_nacionais); same pattern works for
-- delta.landing / delta.bronze / delta.prata against their respective buckets.
```
**Must be `s3a://`, not `s3://`** — confirmed by testing (`No FileSystem for scheme "s3"`). With
`hive.s3.*` config, this connector's filesystem layer is a thin wrapper over Hadoop's own
`org.apache.hadoop.fs.s3a.S3AFileSystem`, which only registers the `s3a` scheme — unlike old
Presto/PrestoS3FileSystem, which used to accept `s3`/`s3n`/`s3a` interchangeably. Always match the scheme
Spark used to write (`s3a://`).

**Internal (service) clients — e.g. Superset — connect over plain HTTP without OAuth2:**
`http-server.authentication.allow-insecure-over-http=true` is set on the coordinator (a service has no
browser for the OAuth2 flow, and a container can't reach the EIP anyway). Plain-HTTP requests are
unauthenticated — the "user" is just the declared `X-Trino-User` header — while external access via Traefik
(HTTPS, `X-Forwarded-Proto=https`) still requires OAuth2. Trust model = overlay network + Security Group,
same as Postgres/SeaweedFS/Redis. Superset's SQLAlchemy URI (needs the `trino[sqlalchemy]` pip package,
baked into `datastack/superset:3.1.3-oidc`): `trino://superset@trino-coordinator:8080/delta`. If
service-level auth is ever wanted, PASSWORD authenticators require internal TLS too (Trino refuses password
auth over plain HTTP) — a separate project.

## Superset: DB schema must be migrated, and the init container filter was ambiguous

If `/superset/welcome/` (or any page) 500s with `psycopg2.errors.UndefinedTable: relation "user_attribute"
does not exist` (or similar), the Superset **app schema was never created** — only the 8 Flask-AppBuilder
auth tables (`ab_*`) exist, meaning `superset db upgrade` never ran. `superset fab list-users` returning
empty is the same symptom (no `create-admin` either). Fix (idempotent, safe to re-run):
```
docker exec <superset-web-container> superset db upgrade
docker exec <superset-web-container> superset fab create-admin --username admin --firstname Admin \
  --lastname User --email admin@datastack.local --password admin_password_CHANGE_ME
docker exec <superset-web-container> superset init
```
**Root cause**: `scripts/09-deploy-all.sh`'s `docker ps -q -f name=apps_superset | head -1` used an
unanchored substring filter that also matches `apps_superset-worker` — with `head -1`, which of the two
containers gets picked is non-deterministic. If it grabbed the worker (which can be crash-looping, see
Known Gotchas below) the `docker exec` fails and `set -e` aborts the script before `db upgrade`/`create-admin`/
`init` ever ran, leaving the DB schema empty with no error surfaced beyond the aborted deploy log. Fixed to
`-f "name=^apps_superset\."` (anchored, matches only `apps_superset.<replica>.<hash>`).

## Superset SSO via Keycloak — custom image, and a volume-shadowing trap

Superset authenticates via Flask-AppBuilder's native `AUTH_OAUTH` against Keycloak (same hairpin pattern as
Trino/JupyterHub: browser uses the external `https://keycloak...sslip.io` URL for `authorize_url`; the
back-channel — token/userinfo, called by the Superset container itself — uses `http://keycloak:8080`
internal). Config lives in `config/superset/superset_config.py` (`OAUTH_PROVIDERS` + a
`KeycloakSecurityManager` overriding `oauth_user_info`, since FAB has no built-in Keycloak provider and only
knows how to parse known ones like Google/GitHub). New Keycloak client `superset`
(`redirectUris: https://superset.<eip>.sslip.io/oauth-authorized/keycloak`), plus a
`superset-audience`-style realm role `superset_admin` mapped to Superset's `Admin` role via
`AUTH_ROLES_MAPPING` — requires a protocol mapper (`oidc-usermodel-realm-role-mapper`) on the client to
expose `realm_access.roles` in `/userinfo`, since Keycloak doesn't include realm roles there by default.
Traefik got a new router (`config/traefik/dynamic/dynamic.yml`, `superset` → `http://superset:8088`).

- **`Authlib` is a hard requirement for `AUTH_TYPE = AUTH_OAUTH`** (`from authlib.integrations.flask_client
  import OAuth`) and isn't in the official `apache/superset:3.1.3` image. Same fix pattern as JupyterHub's
  `datastack/jupyterhub:4.1-oidc`: a locally-built image, `datastack/superset:3.1.3-oidc`
  (`config/superset/Dockerfile`), pinned to node-1 in `08-apps-stack.yml` (no registry). Also pin
  `cryptography>=42.0.4,<43.0.0` explicitly — Authlib alone pulls the latest `cryptography` (49.x), which
  violates `apache-superset`'s own `<43.0.0` constraint.
- **`RUN pip install ...` in the Dockerfile must run as root, not the image's default `superset` user.**
  Without `USER root` before the install (and `USER superset` after, to restore it), pip silently falls back
  to a `--user` install because `superset` can't write to the system site-packages — landing Authlib under
  `/app/superset_home/.local/lib/...`. That path is *exactly* the mount point of the `superset-data` named
  volume (`volumes: - superset-data:/app/superset_home`), so at runtime the volume shadows whatever got
  baked into the image there, and the container boots with `ModuleNotFoundError: No module named 'authlib'`
  — even though `docker run` against the same image *without* the volume mounted shows the module present
  and importable. This is a general trap for any custom image built on top of a service that also gets a
  named-volume mount: verify `pip show <pkg>`'s install location isn't under a path the stack later mounts
  a volume onto, not just that the import succeeds in an unmounted test container.
- Both `config/superset/Dockerfile` and `config/jupyterhub/Dockerfile` are now committed to the repo (the
  JupyterHub one previously only existed at `/tmp/jhbuild/Dockerfile` on node-1 — lost if the VM were ever
  rebuilt). Build: `docker build -t datastack/superset:3.1.3-oidc config/superset/`.
- **Keycloak's `--import-realm` only imports a realm that doesn't already exist** (`Realm 'datastack'
  already exists. Import skipped` in the boot log) — it does **not** upsert/merge on restart. Adding a new
  client to `realm-datastack.json` and restarting Keycloak does nothing to an already-provisioned realm; the
  change has to be applied live via the Admin REST API (`POST .../admin/realms/datastack/clients`, etc.) to
  take effect on the running cluster, same as any other admin-API-created object per the "session state
  drifts" note above. The JSON file is still correct/authoritative for a *fresh* deploy (empty DB).

## PySpark client from JupyterHub notebooks — version-matching traps (all hit in sequence)

`notebooks/faker-landing.ipynb` is the working reference (Faker → `s3a://landing/faker-demo/pessoas/`).
A notebook driver connecting to `spark://spark-master-1:7077,spark-master-2:7077,spark-master-3:7077`
(multi-master HA URL — see "Spark Master HA via ZooKeeper" below; the old single-name `spark-master`
service no longer exists) must match the cluster on TWO axes, and the DockerSpawner image
(`jupyter/pyspark-notebook:spark-3.5.0`) matches neither out of the box:

1. **Exact Spark version (3.5.6, not just "3.5.x").** The image ships Spark/PySpark 3.5.0; the cluster runs
   3.5.6. Java task serialization is strict about `serialVersionUID` of scheduler classes → any patch-level
   mismatch dies with `InvalidClassException: org.apache.spark.scheduler.Task; local class incompatible`.
   Fix (notebook cell, before any `import pyspark`): download the exact `spark-3.5.6-bin-hadoop3` tarball
   from archive.apache.org into `work/` (persistent volume, one-time ~400MB), point `SPARK_HOME` +
   `sys.path` at it.
2. **Same Python minor version (3.12).** The notebook kernel is conda Python 3.11; the bitnami executors
   only have 3.12 → `[PYTHON_VERSION_MISMATCH] Python in worker has different version (3, 12) than that in
   driver 3.11`. In a notebook the driver Python IS the kernel process, so the fix is a matching kernel:
   `mamba create -y -p /home/jovyan/work/envs/py312 python=3.12 ipykernel`, then
   `/home/jovyan/work/envs/py312/bin/python -m ipykernel install --user --name py312-spark`. The conda env
   lives on the persistent volume; the kernelspec registration (`~/.local/share/jupyter/kernels/`) does NOT
   — re-run only the `ipykernel install` line after a container recycle. **The browser tab keeps its old
   kernel session after the kernelspec is registered** — switching in Kernel > Change Kernel (or reloading
   the page) is required, and a stale still-running 3.11 driver holds the old Spark app until killed.
3. Other per-notebook needs (the spawned container inherits nothing from the cluster's
   `spark-defaults.conf`): full S3A/magic-committer config in the SparkSession builder (including
   `fs.s3a.directory.marker.retention=keep`), the `spark-hadoop-cloud` jar via `spark.jars.packages`, and
   `spark.jars.ivy=/home/jovyan/work/.ivy2` so the ~750MB dependency cache survives container recycles.
   Credentials come from `work/.env` via python-dotenv (never hardcoded in cells).
4. **Any mid-write failure leaves `__magic`/`__magic.versions` + orphaned multipart uploads at the
   destination**, and the next `mode("overwrite")` fails with `AWSStatus500Exception: delete on <path>` /
   `FileAlreadyExistsException ... __magic ... since it is a file`. Clean with `weed shell`:
   `fs.rm -r /buckets/<bucket>/<path>` (+ `s3.clean.uploads -timeAgo 1m`) before re-running — same lesson
   as the landing job, it just recurs much more often in notebooks because every version-mismatch failure
   above also aborted mid-write.

## Known gotchas

- **`restart_policy.condition` must be `any` for every long-running service.** With `on-failure`, a clean
  SIGTERM shutdown (exit 0 — e.g. a Celery warm shutdown, a drain, a manual container stop) leaves the
  service down **forever** with no error anywhere: the task shows `Complete` and Swarm never reschedules it.
  This is how `apps_superset-worker` sat dead for a day. All stacks now use `any`.
- **`apache/superset`'s image ships a built-in HEALTHCHECK against the web port (8088).** A Celery worker
  container from that image serves no HTTP, fails the inherited healthcheck, and gets killed ~90s after
  becoming ready — in a loop. `superset-worker` sets `healthcheck: disable: true` for this reason; any
  non-web service reusing a web image needs the same check.
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

## SSO / Ingress (Traefik + Keycloak) — `stacks/11-ingress.yml`

Single cluster entrypoint is **Traefik** (publishes 80/443 on node-1), routing by hostname via
**sslip.io** (`<svc>.<eip>.sslip.io` resolves to the EIP with no DNS of our own). TLS is a
self-signed cert (browser warns; proceed). Traefik routers live in `config/traefik/dynamic/dynamic.yml`
(file provider, `watch: true` → edits hot-reload, no restart). Current routers: `keycloak`, `trino`,
`jupyter`, `seaweedfs` (SSO), `traefik-dashboard` (`api@internal`, read-only, at `/dashboard/`).

**Keycloak** is the IdP (realm `datastack`; master admin `admin`). Login goes through the realm; the
services authenticate against it. Because the security is only as good as the network, note the Huawei
**Security Group** currently allows 80/443 (and the service ports) from a **single source IP** — widen it
there for more users; a timeout (not a cert warning) means the SG is blocking the caller's IP.

**The hairpin pattern (used everywhere OIDC talks to Keycloak).** A container cannot reach the EIP
(`keycloak.<eip>.sslip.io` hairpins and fails), but *can* reach `http://keycloak:8080` on the overlay. So
every integration splits URLs: the **browser** leg (authorize/login, and logout) uses the **external
HTTPS** sslip.io URL; the **back-channel** (token / jwks / userinfo) uses the **internal**
`http://keycloak:8080/realms/datastack/...`. This avoids needing the self-signed CA in every client's
truststore. It's applied in: oauth2-proxy (SeaweedFS Admin), Trino (`oidc.discovery=false` + manual
`auth-url` external / `token-url`+`jwks-url`+`userinfo-url` internal), and JupyterHub GenericOAuthenticator
(`authorize_url` external / `token_url`+`userdata_url` internal).

- **Trino OAuth2** (`config/trino/coordinator/config.properties`): `http-server.authentication.type=oauth2`
  + `web-ui.authentication.type=oauth2` + `http-server.process-forwarded=true` (TLS terminates at Traefik;
  Trino trusts `X-Forwarded-Proto=https`). Enabling auth **requires** `internal-communication.shared-secret`
  identical on coordinator **and** all workers, or nodes won't start. The `trino` Keycloak client needs an
  **audience mapper** (adds `trino` to the token `aud`) or token validation fails after login. Trino 435
  treats any unknown property as **fatal** — verify property names against the jar before adding
  (`jar xf trino-main-435.jar io/trino/server/security/oauth2`).
- **JupyterHub** (`config/jupyterhub/jupyterhub_config.py`): the stock `jupyterhub/jupyterhub` image has
  **no** `oauthenticator`/`dockerspawner` and the repo config was **never mounted** before — it ran fully
  default. Now it uses a **locally-built image `datastack/jupyterhub:4.1-oidc`** (see `/tmp/jhbuild/Dockerfile`:
  base + `pip install oauthenticator dockerspawner`), pinned to node-1 (image is local, no registry). Config
  is now bind-mounted. DockerSpawner needs `datastack-net` to be **attachable** (it is) and
  `c.JupyterHub.hub_connect_ip = "jupyterhub"` so spawned notebook containers can reach the hub. Notebook
  image `jupyter/pyspark-notebook:spark-3.5.0` is pre-pulled on node-1; `pull_policy=ifnotpresent` (pull once).

**Keycloak realm is code, but session state drifts.** `config/keycloak/realm-datastack.json` is imported at
boot (`--import-realm`) and is the source of truth for clients/users. Anything created via the admin **API**
at runtime (done a lot this session) is **not** in the file until reconciled — reconcile it or a realm
re-import silently loses it. Keycloak redirect-URI **wildcards don't match a subdomain** (`https://*.<eip>...`
did not match `seaweedfs.<eip>...`); list the explicit callback URL per service.

**Single-identity model: `superadmin` (realm `datastack`) is the one administrative account for every UI.**
It carries all three realm roles (`user`, `superset_admin`, `airflow_admin`), which the per-tool
`AUTH_ROLES_MAPPING` (Superset, Airflow) turns into that tool's own `Admin` role at login, and it's also
listed in JupyterHub's `admin_users` (hub-admin, not a role-mapping — GenericOAuthenticator has no realm-role
concept). Portainer's OAuth resolves the same way via `preferred_username` — active and validated end-to-end
(2026-07-04, authorization-code flow via curl); ports 9000/9443 are closed, and the OAuth settings live in
Portainer's DB (`portainer-data` volume, applied via `PUT /api/settings`), **not** in any stack file — a
volume wipe loses them. **Portainer CE gotcha: with OAuth enabled, only the INITIAL admin (`admin`) can
still log in with an internal password**; internal users created later (e.g. the internal `superadmin`) get
`422 "Only initial admin is allowed to login without oauth"` and must use OAuth — the initial `admin` is the
anti-lockout fallback if Keycloak is down. The `superadmin` password is
**not versioned anywhere** (real value only lives in the running Keycloak, deliberately, unlike the
`_CHANGE_ME` placeholders elsewhere in this repo; on node-1 it also exists at
`/root/.credentials/portainer-superadmin`, outside git, used for the Portainer API). Exceptions to "one identity everywhere": (1) **Swarmpit**
keeps its own internal CouchDB-backed login on top of the SSO forward-auth gate — a real double login, no
role/claim from Keycloak reaches it, and there is currently no safe way to provision a matching `superadmin`
account there (its single seeded `admin` user's password is unknown, and the app's own login API doesn't
expose enough to tell a bad password from a malformed request — do **not** "fix" this by writing to its
CouchDB directly, even though that CouchDB currently runs with no auth of its own; that's a much bigger,
separate problem). (2) The **Keycloak admin console itself** (`/admin/master/console`) is reached with the
realm-`master` `admin` user, not `superadmin` — `superadmin` is a `datastack`-realm user account, not a
Keycloak administrator. (3) **Trino** and the oauth2-proxy-fronted UIs (Spark UI, Spark History, SeaweedFS
Admin, Swarmpit's outer gate, Traefik dashboard) have no per-user RBAC at all — any authenticated realm user,
`superadmin` included, gets the same access; there's no "admin" distinction to grant there.

## Spark → SeaweedFS (S3A): the working recipe and its traps

The stack ships hadoop-aws-3.3.4 + aws-java-sdk-bundle in the Spark image, and `spark-defaults.conf` points
S3A at SeaweedFS (`seaweedfs-filer-1:8333`, path-style, ssl off). Getting a write to actually land took four
fixes — all real, all non-obvious:

1. **SeaweedFS S3 needs identities configured or it rejects signed requests.** The filers ran `-s3` with no
   `-s3.config`; a signed PUT then fails with *"Signed request requires setting up SeaweedFS S3
   authentication"*. Fix: `config/seaweedfs/s3config.json` (identity with the same access/secret keys as
   `spark-defaults.conf`) mounted into all three filers + `-s3.config=...` on their commands
   (`stacks/05-seaweedfs-stack.yml`).
2. **The default committer fails on SeaweedFS.** `FileOutputCommitter` (v1 **and** v2) finalizes by
   **renaming** = S3 server-side **COPY**, which SeaweedFS rejects: *"Copy Source must mention the source
   bucket and key"*. Symptom without fail-fast is a **silent multi-minute hang** (S3A retry backoff), not an
   error. Fix: use the **S3A magic committer**, which writes straight to the destination via multipart (no
   rename): drop `spark-hadoop-cloud_2.12-3.5.6.jar` on the classpath (not in the base image) and set
   `spark.hadoop.fs.s3a.committer.name=magic`, `...committer.magic.enabled=true`,
   `spark.sql.sources.commitProtocolClass=org.apache.spark.internal.io.cloud.PathOutputCommitProtocol`,
   `spark.sql.parquet.output.committer.class=org.apache.spark.internal.io.cloud.BindingParquetOutputCommitter`.
   Validated: 2000-row write landed in ~8s. (TODO: make the jar persistent on all Spark nodes — mount via the
   stack or bake a custom image — before enabling the committer in `spark-defaults.conf`.)
3. **Bulk delete is unsupported** — set `spark.hadoop.fs.s3a.multiobjectdelete.enable=false` (already in
   `spark-defaults.conf`) or cleanup `DeleteObjects` returns `InternalError`.
3b. **`fs.s3a.directory.marker.retention=keep` is mandatory for any real-volume write** (already in
   `spark-defaults.conf`). Default S3A deletes "fake directory" markers of parent paths after *every*
   file write (`deleteUnnecessaryFakeDirectories`); SeaweedFS answers those deletes with `500 InternalError`,
   so S3A enters exponential retry-backoff and the write **hangs for minutes with no error** (thread dump:
   `finishedWrite -> deleteUnnecessaryFakeDirectories -> deleteObject -> pauseBeforeRetry`). A small write
   (the 2000-row smoke) hides it; it only bites at volume. `keep` stops S3A from ever deleting markers —
   the 41M-row / 88-file landing job dropped from 12+ min (hung) to **13.5s**. Diagnose S3A "silent hangs"
   with a thread dump (`kill -3 <driver-pid>`; JRE has no `jstack`), not by staring at logs — the driver is
   busy in a retry loop, so nothing new gets logged.
4. **Diagnosing S3A hangs**: add `spark.hadoop.fs.s3a.attempts.maximum=1` + `...retry.limit=1` +
   `...connection.timeout=8000` to make the real S3 error surface immediately instead of backing off for
   minutes.

5. **S3 versioning on a bucket breaks EVERY Spark write to it (SeaweedFS 4.37).** If anyone enables (and
   even later suspends) versioning on a bucket — e.g. via the SeaweedFS Admin UI — every DeleteObject on
   that bucket goes through the filer's `deleteVersionedObject` path, which has a path bug (it tries
   `UpdateEntry` on `/buckets/<bucket>/<last-segment>` instead of the full key path) and answers
   **500 InternalError**. Since the magic committer *deletes* the `__magic` dir on commit and
   `mode("overwrite")` deletes the destination, every Spark job fails with
   `AWSStatus500Exception: Remove S3 Dir Markers on <path>` (or hangs in retry) and leaves
   `__magic`/`*.versions` debris behind — even on a fresh path. Diagnosis: the filer log shows
   `deleteVersionedObject: failed to delete null version ... no entry is found`, and the bucket carries the
   `Seaweed-X-Amz-Versioning` extended attribute
   (`wget -qO- 'http://127.0.0.1:8888/buckets/<b>/?metadata=true'` inside the filer container; base64 value
   `U3VzcGVuZGVk` = "Suspended"). `s3.bucket.versioning` in `weed shell` only toggles Enabled/Suspended —
   it cannot turn versioning off. Fix: remove the attribute via the filer tagging API
   (`curl -X DELETE 'http://127.0.0.1:8888/buckets/<b>?tagging=X-Amz-Versioning'`) and clean the debris
   with `fs.rm -r`. Do **NOT** recreate the bucket to "reset" it — `s3.bucket.delete` deletes the physical
   collection (the data) with it. This took down the `landing` bucket on 2026-07-02/03 (faker notebooks +
   `_fresh_*` smoke tests).

Two more traps hit while running jobs:
- **`docker exec ... spark-submit` fails Hadoop UGI login** (`invalid null input: name`) because the exec
  bypasses the bitnami entrypoint and uid 1001 has no `/etc/passwd` entry. Run the driver as a user that
  *does* (`docker exec -u 0`) — a JupyterHub `jovyan` (uid 1000) notebook is unaffected. Also set
  `HOME`/`spark.jars.ivy` to a writable dir.
- **A local input path is not visible to executors on other nodes** (`spark_worker_data` is a per-node
  volume). Download on the driver, then **stage the file into SeaweedFS** (`fs.copyFromLocalFile` to
  `s3a://...`) and have Spark read from `s3a://` so every executor sees it. (See the v3 landing script.)

### Magic committer: DONE (2026-07-02) — how it's wired now

"Option A: SeaweedFS + magic committer" is finished and validated end-to-end (smoke job: 2000-row Parquet
write via defaults in ~11s, read-back OK, event log landed in `s3a://spark-logs/events/`):

- `config/spark/jars/spark-hadoop-cloud_2.12-3.5.6.jar` is bind-mounted into spark-master and both workers
  (`stacks/06-datastack.yml`) — `07-sync-config.sh` now also ships the jar to node-2/node-3.
- The magic committer + `PathOutputCommitProtocol` confs are enabled in `config/spark/spark-defaults.conf`.
- `spark.eventLog.dir`/`spark.history.fs.logDirectory` point at `s3a://spark-logs/events/` (a **subpath** —
  bucket root throws "path must be absolute") and eventLog is **on** by default. The `spark-logs` and
  `warehouse` buckets exist; `events/` was created with `fs.mkdir` in `weed shell` (Spark errors
  `FileNotFoundException: s3a://spark-logs/events` at startup if the directory doesn't pre-exist).
- Changing only a bind-mounted config file does **not** restart a Swarm service (the spec is unchanged) —
  after editing `spark-defaults.conf`, `docker service update --force datastack_spark-history` (and any
  other service that must re-read it).

`jobs/landing-beneficios-v3.py` is done and validated (2026-07-02): staging pattern (download ZIP on driver
→ `fs.copyFromLocalFile` to `s3a://landing/pda/_staging/` → read from `s3a://` → write Parquet), **41.5M
rows / 88 files written in ~13.5s**, submitted with `docker exec -u 0` (UGI passwd trap). Two lessons from
running it at volume:
- **No `coalesce(N)` before a large write.** v3's first cut had `coalesce(defaultParallelism)`, and
  `defaultParallelism` read *before executors register* returns 2 → the whole 11.6 GB funneled through 2
  tasks on one executor, 6 of 8 cores idle, and dynamic allocation never scaled up (only 2 pending tasks).
  Removed → the CSV's natural ~88 read partitions (128 MB each) all write in parallel.
- **Killing a magic-committer job mid-write leaves `__magic`/`__magic.versions` dirs + orphaned multipart
  uploads** under the destination. The next run's `mode("overwrite")` then hangs/500s trying to delete them.
  Clean up before re-running: `weed shell` → `fs.rm -r /buckets/<bucket>/<path>` and `s3.clean.uploads
  -timeAgo 1m`.

### Delta Lake jobs (bronze+): `--packages` + Hive-metastore gotcha

`jobs/bronze-beneficios-v2.py` reads the landing Parquet and writes a **Delta Lake** table at
`s3a://bronze/pda/beneficios-emitidos/` (validated 2026-07-02: 41.5M rows, casts `Decimal(12,2)`/`Date`
clean with 0 nulls, ACID history with 2 versions, Time Travel working).

- **Delta is NOT in the `bitnami/spark:3.5` image.** Submit with `--packages io.delta:delta-spark_2.12:3.2.0`
  (compatible with Spark 3.5.6). It resolves on node-1 (has egress) and the driver ships the jars to
  executors, so node-2/node-3 don't need internet. Durable alternative (like `spark-hadoop-cloud`): download
  the 3 jars (`delta-spark_2.12`, `delta-storage`, `antlr4-runtime`) and bind-mount them.
- **Don't use `spark.sql("DESCRIBE HISTORY ...")` (or any `spark.sql` touching the catalog) in a Spark job
  here.** `spark-defaults.conf` sets `spark.sql.catalogImplementation=hive` +
  `spark.sql.hive.metastore.version=3.1.3` but **no** `spark.sql.hive.metastore.jars`, so the first SQL that
  initializes the Hive client dies with *"Builtin jars can only be used when hive execution version == hive
  metastore version. Execution: 2.3.9 != Metastore: 3.1.3"*. DataFrame-API writes (`.save()`, Delta writes)
  never touch Hive, which is why landing/bronze writes work. For Delta history use the API:
  `DeltaTable.forPath(spark, path).history()`. **Latent cluster bug**: to actually use Spark SQL against the
  external HMS 3.1.3, add `spark.sql.hive.metastore.jars=maven` (or a jars path) — deferred, since nothing
  needs the Hive catalog from Spark yet (Trino is the SQL engine over the metastore).
- **No `coalesce(defaultParallelism)` before the write** — same trap as the landing job (reads 2 before
  executors register). Bronze write dropped from 108s (coalesce 2) to 42s without it; Spark's Parquet read
  repacks the 88 landing files into ~11 partitions of 128 MB, so the output is 11 healthy Delta files.
- **Delta overwrite is safe to re-run** unlike the raw-S3A overwrite: `replaceWhere` marks old files removed
  in the transaction log (physical delete deferred to `VACUUM`), so no immediate delete → no SeaweedFS
  delete-storm, and no `__magic` orphans to clean.
- **If a job needs `spark.sql()` (temp views/CTEs), set `spark.sql.catalogImplementation=in-memory` in its
  SparkSession builder** — done in `jobs/gold-beneficios-v2.py`. Temp views don't need the metastore, and
  the in-memory catalog never initializes the broken Hive client. `VACUUM` also has an API form:
  `DeltaTable.forPath(spark, path).vacuum(hours)` (used in `jobs/silver-beneficios-v2.py`).

The full medallion chain is validated end-to-end (2026-07-02), all reading/writing Delta on SeaweedFS:
`landing` (Parquet, 41.5M rows) → `bronze` (typed, 17 cols) → `prata` (silver: cleaned/parsed, 29 cols —
sg_uf map, banco/município/meio-pagamento decomposed, flags) → `ouro` (gold: fat_uf 135, fat_especie 65,
fat_banco 21, kpis_nacionais 1). Silver/gold jobs: `jobs/silver-beneficios-v2.py`, `jobs/gold-beneficios-v2.py`.
Recurring traps fixed in all of them: mojibake in pasted sources (accented column names/dict keys must be
real UTF-8 — a wrong `UF_PARA_SIGLA` key silently NULLs `sg_uf` for every accented state), no
`coalesce(defaultParallelism)` before writes, no `spark.sql()` against the Hive catalog.

### node-2/node-3 have no egress: ship images with `docker save | ssh docker load`

Until the NAT Gateway exists, a new/updated image can't be pulled on node-2/node-3 and their tasks sit in
`Rejected ("No such image")`. Working fix from node-1: `docker save <image> | ssh root@<node> docker load`.
Two traps (engine uses the **containerd image store**):
- If node-1 holds the image as a digest-qualified reference (`docker images` shows `repo:tag@sha256:...`),
  `docker save repo:tag` fails with "No such image" — save `repo:tag@sha256:<digest>` instead, then
  `docker tag <id> repo:tag` on the target node.
- With the containerd store the loaded image's ID **is** the manifest digest, so digest-pinned Swarm specs
  match without `--resolve-image never`; tasks recover on the next restart attempt.

### Placeholder secrets are LIVE in the SSO path (known issue, deliberate until rotated)

`ingress_sso-seaweedfs` (oauth2-proxy) runs with the literal `--client-secret=oauth2-proxy-secret-CHANGE-ME`
and `--cookie-secret=datastack_sso_cookie_secret_32by`, and Keycloak runs with the placeholder DB/admin
passwords from `stacks/11-ingress.yml` — all of which are public in this repo. Only the Trino
`config.properties` in `/opt/datastack` (client-secret + internal shared-secret) got real values; those live
**only** on the nodes, never in git. Rotating the SSO secrets means: new secret on the Keycloak client +
matching `--client-secret` (service update or a real secrets mechanism), new random 32-byte cookie secret.
The Security Group (single allowed source IP) is currently the only thing making this tolerable.

## Airflow 3.0.6 orchestrates the Spark jobs — `stacks/12-airflow.yml`

Airflow 3.0.6 (custom local image `datastack/airflow:3.0-oidc`, `config/airflow/Dockerfile`) runs as the
`airflow` stack: `airflow-apiserver`, `airflow-scheduler` (LocalExecutor — tasks run here),
`airflow-dag-processor` (mandatory separate component in Airflow 3), `airflow-triggerer`. All pinned to
node-1, no host ports — UI only via `https://airflow.<eip>.sslip.io` (Traefik router `airflow`).
Metadata DB is the `airflow` database in the containerized Postgres. One-off init (already run; needed
again only on a fresh DB): `airflow db migrate` + `airflow fab-db migrate` via `docker run --rm`.

- **SSO**: FAB auth manager (`AIRFLOW__CORE__AUTH_MANAGER=...FabAuthManager`) + `AUTH_OAUTH` in
  `config/airflow/webserver_config.py`, same hairpin split as Superset. Two Airflow-3 quirks, both hit:
  the OAuth callback is prefixed with `/auth/` (`/auth/oauth-authorized/keycloak` — the Keycloak client
  `airflow` lists exactly that), and `[fab] session_backend` must be `securecookie` — the `database`
  backend 500s because neither `db migrate` nor `fab-db migrate` creates the `session` table in Airflow 3.
  Realm role `airflow_admin` → Airflow `Admin` via `AUTH_ROLES_MAPPING`; new users register as `Viewer`.
- **How DAGs submit Spark jobs** (`dags/medallion_beneficios.py`, mounted from `/opt/datastack/dags`):
  the scheduler runs as root with `/var/run/docker.sock` + docker CLI and does
  `docker exec -u 0 <spark-master-1-cid> ... bin/spark-submit --master
  spark://spark-master-1:7077,spark-master-2:7077,spark-master-3:7077 /opt/datastack/jobs/<job>.py` — same
  proven pattern as `scripts/run-spark-job.sh` (UGI `-u 0` trap, `HOME=/root`, `spark.jars.ivy`), minus the
  `docker cp`: spark-master-1 now bind-mounts `/opt/datastack/jobs` read-only, so jobs are referenced by
  path. Delta jobs (bronze/silver/gold) still need `--packages io.delta:delta-spark_2.12:3.2.0`; landing
  doesn't. The DAG is `schedule=None` (manual trigger) — set a cron there when the pipeline should run
  unattended. Validated 2026-07-03 by
  really running bronze → silver → gold via `airflow tasks test` (landing skipped: identical mechanism,
  would just re-download the 11GB ZIP).
- **Shared code folder `/data/shared`** (host node-1, sticky `1777` because uids differ): mounted as
  `/opt/shared` in spark-master and airflow-scheduler (rw; ro in dag-processor) and `/home/jovyan/shared`
  in Jupyter notebook containers (DockerSpawner volume). It is for CODE (.py jobs, notebooks), never data
  — node-2/3 executors can't see node-1's disk; data always goes through `s3a://`. Survives everything
  (bind mount on the fstab-mounted `/dev/vdb` data disk; `01-base-setup.sh` recreates it on a VM rebuild).
  Gotcha: an **already-running notebook container doesn't gain a newly added mount** — DockerSpawner
  volumes apply on the next spawn, and the hub itself only re-reads `jupyterhub_config.py` on restart
  (`docker service update --force apps_jupyterhub`; config-only changes don't alter the Swarm spec).
