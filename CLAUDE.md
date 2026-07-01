# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Infrastructure-as-code for a data processing stack (Spark, Trino, Hive, PostgreSQL, SeaweedFS, JupyterHub,
Superset) running on **Docker Swarm** across 3 VMs on Huawei Cloud (Dataprev Gov-Cloud). There is no
application source code, build system, linter, or test suite here — the repo is shell scripts, Docker
stack YAML files, and service config files that get deployed to real VMs over SSH.

"Running the codebase" means executing scripts against the actual nodes, not running anything locally.

## Topology

| Node | Private IP | EIP | Role |
|---|---|---|---|
| node-1 | <ip-node-1> | <eip> | Swarm manager (leader), Spark Master, Trino Coordinator, Hive Metastore, PostgreSQL, Redis, JupyterHub, Superset, Portainer, Swarmpit |
| node-2 | <ip-node-2> | none | Swarm manager, Spark Worker, Trino Worker, SeaweedFS |
| node-3 | <ip-node-3> | none | Swarm manager, Spark Worker, Trino Worker, SeaweedFS |

Swarm runs with **3 managers** (Raft quorum, tolerates 1 node loss) — not 1 manager + 2 workers. Only
node-1 has a public EIP; node-2/node-3 are private-only and have **no internet egress by default**. The
correct fix is a Huawei Cloud **NAT Gateway** (SNAT rule for the `<subnet-privada>` subnet) configured in the
VPC console. Do not route node-2/node-3 traffic through node-1 via ad-hoc iptables MASQUERADE + route
changes — it requires disabling "Source/Destination Check" on node-1's NIC (a real security control) and is
fragile; treat it as a last-resort temporary measure, never a standing configuration. If one specific node
still has no egress after the NAT Gateway is up, its Security Group is the next thing to check — nodes can
end up in a different/more restrictive group than their siblings.

## Deploy order

Scripts in `scripts/` and stacks in `stacks/` share one numbering sequence across both directories,
reflecting execution order, not file type:

```
00-ssh-setup.sh        node-1 only: generates SSH keypair, sets up passwordless access to node-2/node-3
01-base-setup.sh       ALL nodes: Docker install, formats/mounts the 3TB data disk at /data, sysctl tuning
02-swarm-init.sh       node-1 only: docker swarm init, prints the MANAGER join token
                       -> run `docker swarm join --token <TOKEN> <ip-node-1>:2377` on node-2 and node-3
03-swarm-networks.sh   node-1 only: creates the `datastack-net` overlay network, sets node labels
04-postgresql.sh       node-1 only: adds the PGDG apt repo (Ubuntu 22.04 default repos only have PG14),
                       installs PostgreSQL 15 natively (not containerized), creates hive/superset DBs
05-seaweedfs-stack.yml stack: SeaweedFS masters/volumes/filers (S3 storage layer)
06-datastack.yml       stack: Hive Metastore + Spark + Trino
07-sync-config.sh      node-1 only: rsyncs config/ to node-2 and node-3 (see "Bind mounts" below)
08-apps-stack.yml      stack: Redis + JupyterHub + Superset + Portainer
09-deploy-all.sh       node-1 only: docker stack deploy for 05/06/08/10 in order, then initializes Superset
10-swarmpit-stack.yml  stack: Swarmpit (Swarm management UI)
```

`09-deploy-all.sh` expects the repo checked out at `/opt/datastack` on node-1 (it does
`cp /opt/datastack/stacks/*.yml /opt/stacks/`).

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
  paths under `/opt/datastack/config/...` (Trino coordinator/worker configs, `spark-defaults.conf`). Since
  those services are constrained to specific nodes, the config directory must exist identically on
  whichever node the task lands on — Docker Swarm does not distribute bind-mount sources itself. This is
  why `07-sync-config.sh` exists: it rsyncs `/opt/datastack/config/` to node-2 and node-3 after any change
  on node-1, before deploying or redeploying `06-datastack.yml`/`08-apps-stack.yml`.

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
- **SeaweedFS command flags must reference Swarm service DNS names, not VM hostnames.** `-ip=`, `-peers=`,
  `-mserver=`, `-master=` in `05-seaweedfs-stack.yml` need `seaweedfs-master-{1,2,3}`, not `node-{1,2,3}` —
  the overlay network's embedded DNS only resolves service names. Additionally, each master service needs
  `endpoint_mode: dnsrr` in its `deploy:` block: with the default `vip` mode, resolving a master's own
  service name (for its `-ip=` bind) returns the service VIP instead of the task's real IP, and
  `bind: cannot assign requested address` follows. SeaweedFS Volume's data dir bind-mounts to
  `/data/seaweedfs/volume` (created by `01-base-setup.sh`, on the 3TB disk) — not `/mnt/data/seaweedfs`,
  which is never created.
- **SeaweedFS Volume uses port 8081, not SeaweedFS's documented default 8080** — 8080 collides with Trino
  Coordinator when both land on node-1 (both use `mode: host`, so it's a real same-node conflict, not just
  a same-service-replica one).

## Placeholder secrets

Passwords, keys, and secrets throughout `config/` and `stacks/*.yml` are literal placeholders ending in
`_CHANGE_ME` (Postgres users, Superset `SECRET_KEY`, SeaweedFS S3 keys). They are intentionally consistent
across files (e.g. the same `hive_password_CHANGE_ME` appears in `scripts/04-postgresql.sh`,
`stacks/06-datastack.yml`, and `config/trino/*/catalog/postgresql.properties`) — if you change one for a
real deployment, change all occurrences together via `grep -rl CHANGE_ME`.

## High-availability scope

The Swarm control plane (3 managers) is HA by design. Individual services are **not**: Spark Master, Trino
Coordinator, Hive Metastore, PostgreSQL, and Redis are all pinned to node-1 via placement constraints with
no standby/replica. If node-1 goes down, those services stay down until it returns — this is the current
architecture, not a bug to silently "fix" by removing constraints (removing them would break the bind-mount
assumption above).

## Known gotchas

- **`01-base-setup.sh`'s `apt-get upgrade` can hang forever when run non-interactively.** If a conffile
  (e.g. `/etc/ssh/sshd_config`) was already modified on the image, `dpkg` opens an interactive prompt with
  no TTY to answer it, and the script blocks indefinitely rather than failing. Recovery: `dpkg
  --force-confold --force-confdef --configure -a`, then re-run `apt-get` with
  `DEBIAN_FRONTEND=noninteractive -o Dpkg::Options::="--force-confold" -o Dpkg::Options::="--force-confdef"`.
  Prefer running the script with those flags from the start on unfamiliar images.

## Validating cluster state

No test suite — validation is inspecting live Swarm state:

```bash
docker node ls                                   # cluster membership, manager status
docker node inspect <node> --format '{{.Spec.Labels}}'   # confirm name/role labels
docker network ls | grep datastack               # confirm datastack-net exists
docker stack ps <stack-name>                      # per-service task state/errors for a deployed stack
docker service logs <service-name>                # logs for a specific service
```
