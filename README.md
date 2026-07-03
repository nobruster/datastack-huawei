# DataStack Huawei Cloud

Stack de processamento de dados rodando em **Docker Swarm** na **Huawei Cloud HCSO (Dataprev Gov-Cloud)**.

## Infraestrutura

- **3 VMs** c9.8xlarge.4 (32 vCPU / 128 GB RAM / 3 TB disco)
- **Região:** LA-Sao Paulo-Brazil Dataprev Gov-Cloud (la-south-6001)
- **Orquestração:** Docker Swarm (3 managers)
- **Rede privada:** <subnet-privada>
- **EIP node-1:** <eip>

## IPs dos Nós

| Nó | IP Privado | EIP | Papel |
|---|---|---|---|
| node-1 | <ip-node-1> | <eip> | Swarm manager (leader), PostgreSQL, Hive Metastore, Spark Master-1 (HA/ZK), Spark History Server, Trino Coordinator, Redis, JupyterHub, Superset, Portainer, Swarmpit, Airflow, Traefik, Keycloak, ZooKeeper (zk-1) |
| node-2 | <ip-node-2> | - | Swarm manager, Spark Master-2 (HA/ZK), Spark Worker, Trino Worker, SeaweedFS (master+volume+filer), ZooKeeper (zk-2) |
| node-3 | <ip-node-3> | - | Swarm manager, Spark Master-3 (HA/ZK), Spark Worker, Trino Worker, SeaweedFS (master+volume+filer), ZooKeeper (zk-3) |

## Componentes da Stack e Portas

Todos os serviços rodam em container Docker (nenhum roda nativo no host). **O único ingresso HTTP externo é
o 443 do Traefik**, sempre com SSO/OAuth — as portas cruas de UI foram fechadas (ver "Ingress único + SSO"
abaixo). A coluna "Acesso externo" indica o hostname `sslip.io` (via Traefik). Portas listadas sem hostname
são só para comunicação container-a-container via nome de serviço do Swarm, ou protocolos de fio ainda
publicados no host e protegidos pelo Security Group.

| Serviço | Versão | Porta(s) internas / host | Acesso externo (Traefik+SSO) | Nó | Stack |
|---|---|---|---|---|---|
| PostgreSQL | 15 | 5432 (interno) | — | node-1 | datastack |
| Hive Metastore | 3.1.3 | 9083 (host, SG) | — | node-1 | datastack |
| Apache Spark Master (HA, 3x) | 3.5 | 8090 (UI, interno), 7077 (RPC, interno) | `spark.<eip>.sslip.io` (LB nos 3) | node-1, node-2, node-3 | datastack |
| Apache Spark Worker | 3.5 | 8091/8092 (interno) | — (fechado, baixo valor) | node-2, node-3 | datastack |
| ZooKeeper (ensemble, 3x) | 3.9 | 2181/2888/3888 (interno) | — | node-1, node-2, node-3 | zookeeper |
| Spark History Server | 3.5 | 18081 (interno) | `spark-history.<eip>.sslip.io` | node-1 | datastack |
| Trino Coordinator | 435 | 8080 (interno) | `trino.<eip>.sslip.io` (OAuth2 nativo) | node-1 | datastack |
| Trino Worker | 435 | 8080 (interno) | — | node-2, node-3 | datastack |
| SeaweedFS Master | 3.65 | 9333 (interno), 19333 (host, gRPC) | `seaweedfs-master.<eip>.sslip.io` | todos | seaweedfs |
| SeaweedFS Volume | 3.65 | 8081, 18080 (host, SG) | — | todos | seaweedfs |
| SeaweedFS Filer (+ S3 API) | 3.65 | 8888 (interno), 8333 (S3, host) | `seaweedfs-filer.<eip>.sslip.io` | todos | seaweedfs |
| SeaweedFS Admin (dashboard) | 3.95 | 23646 (interno) | `seaweedfs.<eip>.sslip.io` | node-1 | seaweedfs |
| Redis | 7 | 6379 (interno) | — | node-1 | apps |
| JupyterHub | 4.1 | 8000 (interno) | `jupyter.<eip>.sslip.io` (OIDC nativo) | node-1 | apps |
| Apache Superset | 3.1.3-oidc | 8088 (interno) | `superset.<eip>.sslip.io` (OIDC nativo) | node-1 | apps |
| Portainer | 2.20.3 | 9000 (interno) † | `portainer.<eip>.sslip.io` (OIDC nativo) | node-1 | apps |
| Portainer Agent | 2.20.3 | 9001 (host) | — | todos | apps |
| Swarmpit App | 1.10 | 8080 (interno) | `swarmpit.<eip>.sslip.io` ‡ | node-1 | swarmpit |
| Swarmpit Agent | latest | - | — | todos | swarmpit |
| Swarmpit DB (CouchDB) | 2.3.0 | 5984 (interno) | — | node-1 | swarmpit |
| Swarmpit InfluxDB | 1.8 | 8086 (interno) | — | node-1 | swarmpit |
| Apache Airflow 3.0.6 | 3.0.6-oidc | 8080 (api-server, interno) | `airflow.<eip>.sslip.io` (OIDC nativo) | node-1 | airflow |
| Traefik (ingress) | v3.1 | 🌐 80, 🌐 443 (host) | `traefik.<eip>.sslip.io/dashboard/` (SSO) | node-1 | ingress |
| Keycloak (IdP/SSO) | 25.0 | 8080 (interno) | `keycloak.<eip>.sslip.io` | node-1 | ingress |
| oauth2-proxy (`sso-auth`, forward-auth único) | v7.6.0 | 4180 (interno) | — (middleware do Traefik) | node-1 | ingress |

`<eip>` = `<eip>`. † Portainer: OAuth OIDC via Keycloak ativo e validado; 9000/9443 fechadas —
acesso só via Traefik. ‡ Swarmpit tem o gate SSO do Traefik **mais** o
login interno próprio (login duplo — limitação da ferramenta). Spark RPC (7077) não é mais publicado no
host desde a HA via ZooKeeper (3 masters de mesma porta exigiriam `mode: host`; acesso RPC agora é sempre
por nome de serviço no overlay) — não há client externo direto no protocolo Spark RPC, só via container.
Portas ainda publicadas no host (9083 Hive Thrift, 8081/18080 e 8333/19333 SeaweedFS, 9001 Portainer Agent)
são protocolos de fio /
S3, sem UI de navegador — proteção é rede overlay + Security Group.

## Ingress único + SSO (Traefik + Keycloak)

O cluster tem **um único ponto de entrada HTTP**: o Traefik (443 no node-1), roteando por hostname via
`sslip.io` (`<svc>.<eip>.sslip.io` resolve para o EIP sem DNS próprio). Todas as UIs de navegador
ficam atrás dele com SSO; **as portas cruas de UI (8090, 18081, 8080, 8000, 8088, 888, 8888, 9333, 6379...)
foram removidas da publicação no host** — o acesso é só via 443 autenticado. Três mecanismos de auth
convivem:

1. **OIDC nativo** (o próprio app fala com o Keycloak): Trino, JupyterHub, Superset, Airflow e Portainer.
   Cada um faz seu fluxo OAuth2/OIDC; o Traefik só termina TLS e encaminha.
2. **oauth2-proxy em modo ForwardAuth** (`sso-auth`, **um único** proxy p/ todo o cluster): protege as UIs
   que não têm OIDC próprio — Spark Master, Spark History, SeaweedFS Filer/Master, SeaweedFS Admin, Swarmpit
   e o dashboard do Traefik. Um proxy / um client Keycloak / um cookie (`_oauth2_proxy` no domínio
   `.sslip.io`) ⇒ **SSO real**: loga uma vez, vale para todos esses hosts. Adicionar uma UI nova é um router
   no `dynamic.yml` + `middlewares: [sso-auth]`.
3. **Keycloak** é o IdP (realm `datastack`). Padrão **hairpin** obrigatório (um container não alcança o EIP):
   o browser usa a URL externa `https://keycloak...sslip.io`; o back-channel (token/jwks/userinfo, chamado
   pelo container) usa `http://keycloak:8080` interno. Ver CLAUDE.md ("SSO / Ingress") para detalhes.

### Identidade única: `superadmin`

O usuário **`superadmin`** (realm `datastack`) é a conta administrativa única para todas as UIs. Ele carrega
as realm roles `user`, `superset_admin`, `airflow_admin`, que viram `Admin` em cada ferramenta via
`AUTH_ROLES_MAPPING` (Superset, Airflow) e está em `admin_users` do JupyterHub. A senha do `superadmin`
**não é versionada** (vive só no Keycloak). Exceções: **Swarmpit** mantém login interno próprio por cima do
gate SSO (login duplo); o **console admin do Keycloak** (`/admin/master/console`) usa o `admin` do realm
`master`, não o `superadmin`; **Trino** e as UIs atrás do forward-auth não têm RBAC por usuário — qualquer
usuário autenticado do realm tem o mesmo acesso.

## Orquestração — Apache Airflow 3.0

O **Airflow 3.0.6** (stack `airflow`, imagem local `datastack/airflow:3.0-oidc`) orquestra os jobs Spark.
Componentes: `api-server`, `scheduler` (LocalExecutor — os tasks rodam aqui), `dag-processor` e `triggerer`,
todos pinados no node-1, **sem porta publicada no host** (acesso só via `airflow.<eip>.sslip.io`).
Metadata DB é o banco `airflow` no Postgres containerizado. Login via Keycloak (role `airflow_admin` → Admin;
demais usuários entram como Viewer).

O DAG `dags/medallion_beneficios.py` roda a cadeia `landing → bronze → prata → ouro`: cada task faz
`docker exec` no container do spark-master (via socket do Docker montado no scheduler) e submete o job
correspondente de `/opt/datastack/jobs` — **sem `docker cp`**, porque o spark-master agora monta esse
diretório read-only. Está com `schedule=None` (trigger manual); troque por um cron no DAG para rodar
sozinho. Ver CLAUDE.md ("Airflow 3.0.6 orchestrates the Spark jobs") para os detalhes.

Em toda execução real (não `tasks test`), o supervisor de cada task fala com a Execution API pela URL
**interna** (`AIRFLOW__CORE__EXECUTION_API_SERVER_URL`) — mesmo padrão hairpin do resto do cluster; detalhes
e o bug que isso corrigiu estão no CLAUDE.md.

## Pasta compartilhada de código — `/data/shared`

Para facilitar desenvolvimento e execução de jobs, há uma pasta compartilhada entre as três ferramentas que
manipulam código:

| Ferramenta | Ponto de montagem |
|---|---|
| Jupyter (notebooks) | `/home/jovyan/shared` |
| Spark Master-1 (node-1 — único dos 3 masters com esse mount; é o nó de submissão) | `/opt/shared` |
| Airflow (scheduler) | `/opt/shared` |

É o mesmo diretório `/data/shared` no host node-1 (sticky `1777`, porque os UIDs diferem entre as
ferramentas), no disco de dados de 3TB. **É para CÓDIGO** (`.py`, notebooks), **nunca para dados** — os
executores em node-2/node-3 não enxergam o disco local do node-1; dados sempre trafegam via `s3a://`
(SeaweedFS). Sobrevive a restart de container, redeploy de stack, rebuild de imagem e reboot do node-1 (é
bind mount no disco montado via `/etc/fstab`; o `01-base-setup.sh` recria a pasta num rebuild de VM).
Ressalva: um **container de notebook já aberto não ganha o mount novo** — vale a partir do próximo spawn.

## Estrutura do Repositório

```
datastack-huawei/
├── README.md
├── CLAUDE.md                     # Guia detalhado de arquitetura, invariantes e troubleshooting
├── scripts/
│   ├── 00-ssh-setup.sh           # Configura SSH sem senha do node-1 para node-2/node-3
│   ├── 01-base-setup.sh          # Setup base (Docker, disco, hosts) - todos os nos
│   ├── 02-swarm-init.sh          # Inicializa Docker Swarm no node-1
│   ├── 03-swarm-networks.sh      # Cria rede overlay e labels (role, name)
│   ├── 07-sync-config.sh         # Sincroniza config/ para node-2/node-3 (bind mounts)
│   ├── 09-deploy-all.sh          # Deploy completo de todas as stacks
│   └── run-spark-job.sh          # Roda um job de jobs/ no container spark-master (ver uso abaixo)
├── stacks/
│   ├── 05-seaweedfs-stack.yml    # SeaweedFS distribuido (Masters + Volumes + Filers)
│   ├── 06-datastack.yml          # PostgreSQL + Hive Metastore + Spark (+ History Server) + Trino
│   ├── 08-apps-stack.yml         # Redis + JupyterHub + Superset + Portainer
│   ├── 10-swarmpit-stack.yml     # Swarmpit (gerenciamento do Swarm)
│   ├── 11-ingress.yml            # Traefik (ingress/TLS) + Keycloak (SSO) + oauth2-proxy (forward-auth)
│   ├── 12-airflow.yml            # Apache Airflow 3.0 (api-server + scheduler + dag-processor + triggerer)
│   └── 13-zookeeper.yml          # Ensemble ZooKeeper (3x) - recovery backend do Spark Master HA
├── jobs/                         # Pipeline medallion (PySpark), landing -> bronze -> silver -> gold
│   ├── landing-beneficios-v3.py  # Ingestao raw (staging s3a:// + magic committer)
│   ├── bronze-beneficios-v2.py   # Tipagem/Delta Lake sobre a landing
│   ├── silver-beneficios-v2.py   # Limpeza, parsing, enriquecimento
│   └── gold-beneficios-v2.py     # Tabelas fato/KPI prontas para BI (fat_uf, fat_especie, fat_banco, kpis)
├── dags/                         # DAGs do Airflow (montado em /opt/airflow/dags)
│   └── medallion_beneficios.py   # Orquestra landing -> bronze -> prata -> ouro via docker exec no spark-master
└── config/
    ├── postgres/
    │   └── init.sql              # Cria databases/usuarios hive, superset, keycloak, airflow (1a inicializacao)
    ├── hive/
    │   └── core-site.xml         # fs.s3a.* do Hive Metastore (suporte a S3A/SeaweedFS)
    ├── trino/
    │   ├── coordinator/          # Config do Trino Coordinator (catalog/ hive + postgresql + delta)
    │   └── worker/               # Config dos Trino Workers (mesmos catalogos do coordinator)
    ├── spark/
    │   ├── spark-defaults.conf   # Configuracoes do Spark (S3A/SeaweedFS, magic committer)
    │   └── jars/                 # spark-hadoop-cloud (commiter S3A magic, nao vem na imagem bitnami)
    ├── seaweedfs/                # s3config.json (identidades S3), security.toml, tls/ (admin)
    ├── superset/
    │   ├── Dockerfile            # datastack/superset:3.1.3-oidc (Authlib p/ AUTH_OAUTH)
    │   └── superset_config.py    # Redis (cache/Celery), timeouts, feature flags, SSO Keycloak
    ├── jupyterhub/
    │   ├── Dockerfile            # datastack/jupyterhub:4.1-oidc (oauthenticator + dockerspawner)
    │   └── jupyterhub_config.py  # DockerSpawner + OIDC (GenericOAuthenticator via Keycloak) + pasta shared
    ├── airflow/
    │   ├── Dockerfile            # datastack/airflow:3.0-oidc (authlib + provider FAB + docker CLI)
    │   └── webserver_config.py   # FAB auth manager + AUTH_OAUTH (SSO Keycloak, role airflow_admin -> Admin)
    ├── traefik/                  # traefik.yml + dynamic/ (routers + middleware sso-auth) + tls/ (self-signed)
    └── keycloak/
        └── realm-datastack.json  # Realm "datastack": clients (trino, oauth2-proxy, superset, airflow, portainer), usuarios
```

## Pipeline de dados (medallion) - `jobs/`

Jobs PySpark que levam o dado de bruto a analitico, todos gravando Delta Lake em `s3a://` (SeaweedFS):
`landing` (raw, staged do site de origem) → `bronze` (tipado) → `prata`/silver (limpo/enriquecido) →
`ouro`/gold (tabelas fato + KPIs). Rodar com o wrapper (resolve o container Swarm do spark-master, copia o
job e submete como uid 0):

```bash
scripts/run-spark-job.sh jobs/landing-beneficios-v3.py
scripts/run-spark-job.sh jobs/bronze-beneficios-v2.py io.delta:delta-spark_2.12:3.2.0
scripts/run-spark-job.sh jobs/silver-beneficios-v2.py io.delta:delta-spark_2.12:3.2.0
scripts/run-spark-job.sh jobs/gold-beneficios-v2.py io.delta:delta-spark_2.12:3.2.0
```

Ou orquestre a cadeia inteira pelo **Airflow** (DAG `medallion_beneficios`, trigger manual na UI
`airflow.<eip>.sslip.io` ou `airflow dags trigger medallion_beneficios`) — mesmo mecanismo de
submissão, encadeando as 4 camadas. Ver "Orquestração — Apache Airflow 3.0" acima.

As tabelas gold só aparecem como SQL depois de registradas no catálogo `delta` do Trino (não são
`saveAsTable`, então não entram no Hive Metastore sozinhas) — ver "Consultando via Trino" abaixo. Detalhes,
armadilhas e o histórico de cada fix estão no CLAUDE.md.

## Consultando via Trino (DBeaver ou outro client JDBC)

O Trino tem **3 catálogos**: `postgresql` (bancos operacionais), `hive` e `delta` (Delta Lake — é onde estão
landing/bronze/prata/ouro). Autenticação é **OAuth2 obrigatório via Keycloak** (sem usuário/senha direto).

- **Endpoint**: `jdbc:trino://trino.<eip>.sslip.io:443/delta` (não use o IP interno nem o nome de
  serviço — o client roda fora do cluster).
- **Driver properties**: `SSL=true`, `externalAuthentication=true` (abre o navegador pro login Keycloak).
- **Certificado self-signed**: importe `config/traefik/tls/ingress.crt` num truststore (não precisa ser o
  truststore global do client) e aponte via `SSLTrustStorePath`/`SSLTrustStorePassword`, com
  `SSLVerification=FULL`.
- **Anexar uma tabela Delta já existente** (ex. as do gold) ao catálogo, sem reescrever nada:
  ```sql
  CREATE SCHEMA IF NOT EXISTS delta.ouro WITH (location = 's3a://ouro/pda/beneficios-emitidos/');
  CALL delta.system.register_table(schema_name => 'ouro', table_name => 'fat_uf',
      table_location => 's3a://ouro/pda/beneficios-emitidos/fat_uf');
  -- repita por tabela (fat_especie, fat_banco, kpis_nacionais)
  ```

Esquema tem que ser **`s3a://`** (não `s3://`) — ver CLAUDE.md para o porquê e outras armadilhas de S3/HMS.

O repositório deve ser clonado em **`/opt/datastack`** em node-1 — `09-deploy-all.sh` e `07-sync-config.sh`
assumem esse caminho.

## PostgreSQL é containerizado

Não existe mais serviço nativo/instalado no host — todo o stack roda em Docker. PostgreSQL é o serviço
`postgres` dentro de `06-datastack.yml` (dados em `/data/postgres`, no disco de 3TB). Isso é proposital, não
só estilo: um Postgres nativo no host só seria alcançável por containers rodando no **mesmo** nó (via
`docker_gwbridge`, que não atravessa nós) — inviável para `trino-worker-node2`/`node3`, que carregam todos
os catálogos configurados (incluindo `postgresql`) rodando em node-2/node-3. Como serviço do Swarm, fica
acessível como `postgres:5432` de qualquer nó.

## Deploy - Ordem de Execucao

```bash
# 1. Somente no node-1 (node-2/node-3 ja com acesso via senha/chave da nuvem)
bash scripts/00-ssh-setup.sh

# 2. Em TODOS os nos (node-1, node-2, node-3)
bash scripts/01-base-setup.sh

# 3. Somente no node-1
bash scripts/02-swarm-init.sh
# Copie o token de MANAGER gerado e execute em node-2 e node-3 (cluster com 3 managers):
docker swarm join --token <TOKEN> <ip-node-1>:2377

# 4. Somente no node-1
bash scripts/03-swarm-networks.sh
bash scripts/07-sync-config.sh
bash scripts/09-deploy-all.sh
```

O `09-deploy-all.sh` faz deploy das stacks `05/06/08/10`. As stacks `11-ingress` (Traefik+Keycloak+SSO),
`12-airflow` (Airflow) e `13-zookeeper` (ensemble ZooKeeper, recovery backend do Spark Master HA) são
deployadas à parte. **`zookeeper` precisa subir antes de `06-datastack.yml`** (os `spark-master-1/2/3` usam
o ensemble para eleição de líder via `recoveryMode=ZOOKEEPER`) — na prática os masters retentam a conexão
ao ZK, então uma inversão momentânea não é fatal, mas suba o ZK primeiro e valide o ensemble antes de
(re)deployar o datastack:

```bash
docker stack deploy -c /opt/datastack/stacks/13-zookeeper.yml zookeeper
docker stack deploy -c /opt/datastack/stacks/11-ingress.yml ingress
docker stack deploy -c /opt/datastack/stacks/12-airflow.yml airflow
```

A imagem `datastack/airflow:3.0-oidc` é buildada localmente (`docker build -t datastack/airflow:3.0-oidc
config/airflow/`, como as imagens `-oidc` de Superset/JupyterHub), e num banco novo o Airflow precisa de
`airflow db migrate` + `airflow fab-db migrate` uma vez. Ver CLAUDE.md para os detalhes.

## Swarmpit - detalhes importantes

**Nomes de serviço são obrigatórios: `app`, `db`, `influxdb`, `agent` (sem prefixo).** O `swarmpit/agent`
tem o endpoint de eventos/healthcheck **hardcoded** para `http://app:8080/...`. Se os serviços forem
nomeados diferente (ex: `swarmpit-app`), o agent fica preso para sempre em `Waiting for Swarmpit...`, nunca
envia stats, e **nenhum erro aparece em lugar nenhum** — o dashboard só fica com CPU/Memória/Disco em
"Loading" silenciosamente. `stacks/10-swarmpit-stack.yml` já usa os nomes corretos; não renomeie os
serviços nesse arquivo.

**Versão do app fixada em `1.10` (não usar `:latest`).** `swarmpit/swarmpit:latest` (a partir da
`1.11-SNAPSHOT`, commit `d78d3e43` "harden auth", abril/2026) passou a exigir autenticação em `POST
/events`. O `swarmpit/agent` oficial no Docker Hub está parado desde 2019 e nunca envia token nessa
chamada — resultado: `401 Unauthorized {"error":"Authentication failed"}` (confirmado via captura de
tráfego) e o mesmo sintoma de dashboard vazio acima. `1.10` é a véspera da mudança, ainda trata `POST
/events` como acesso livre. Não subir para `:latest` sem checar se o agent passou a autenticar.

**`DOCKER_API_VERSION=1.44` no agent.** O engine instalado (29.6.1) exige API mínima 1.40; a versão antiga
usada em exemplos legados (`1.35`) faz o agent entrar em crash-loop (`panic: Event collector is broken`).

## Regra geral: nomes de serviço do Swarm, nunca hostname de VM ou IP real

Toda string de conexão entre serviços (Postgres, Hive Metastore, S3, Spark master, discovery URI do Trino,
etc) deve usar o **nome do serviço no Swarm** (`postgres`, `spark-master`, `hive-metastore`,
`seaweedfs-filer-1`, `trino-coordinator`...) — nunca:

- **hostname de VM** (`node-1`, `node-2`, `node-3`): o DNS interno da rede overlay só resolve nomes de
  serviço, não hostname de VM. Resultado: `no such host` e crash-loop.
- **IP real da VM** (`<ip-node-1>`, etc): containers não têm rota nenhuma até a rede real das VMs — só
  até a rede overlay e o `docker_gwbridge` do próprio nó (que não atravessa nós). A conexão simplesmente
  nunca chega no destino (timeout/reset).

Única exceção: endpoints acessados de **fora** do cluster por IP/porta ainda publicados no host (ex: a S3
API em `http://<ip-node-1>:8333`, ou um client S3/JDBC) — aí o IP real é exatamente o que se quer. As UIs
de navegador não entram mais nessa exceção: passaram todas para o Traefik (`<svc>.<eip>.sslip.io`).

## Labels dos nós (Swarm)

`03-swarm-networks.sh` define o label `name` em cada nó, usado pelas `placement constraints` das stacks (`node.labels.name == node-1`, etc). Sem esse label os serviços ficam presos em `Pending`.

## Problemas conhecidos / Troubleshooting

**`01-base-setup.sh` trava no `apt-get upgrade`.** Se a VM tiver `sshd_config` ou outro conffile já
modificado, o `dpkg` abre um prompt interativo ("What do you want to do about modified configuration
file...") que trava o script indefinidamente quando rodado sem TTY. Se isso acontecer:
```bash
dpkg --force-confold --force-confdef --configure -a
DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::="--force-confold" -o Dpkg::Options::="--force-confdef" upgrade
```
Considere já rodar `01-base-setup.sh` com `DEBIAN_FRONTEND=noninteractive` e essas mesmas flags de `apt-get`
para evitar o problema.

**node-2 e node-3 não têm saída para internet por padrão.** Só node-1 tem EIP; node-2/node-3 são
private-only. Sem isso, `apt-get`/`docker pull` nesses nós dão timeout. Fix correto: criar um **NAT Gateway**
na VPC da Huawei Cloud com regra SNAT para a subnet `<subnet-privada>` (console: NAT Gateway > Public NAT
Gateway > regra SNAT). Não usar node-1 como NAT improvisado via iptables/`ip route` — esbarra no
"Source/Destination Check" da Huawei Cloud e é um workaround frágil, não uma solução.

**Se algum nó específico (ex: node-3) continuar sem egress mesmo com o NAT Gateway ativo:** compare os
Security Groups dos nós — um nó com Security Group diferente/mais restritivo pode ficar de fora mesmo com a
regra SNAT correta na subnet inteira. Nesse caso, serviços `global` (que precisam rodar nos 3 nós, ex:
`swarmpit_agent`, `portainer-agent`) ficam presos com réplica faltando por falha de `docker pull` nesse nó
especificamente — dá pra contornar levando a imagem via `docker save` no node-1 + `scp` + `docker load` +
`docker tag` no nó afetado, mas isso é só paliativo até o Security Group ser corrigido.

**PostgreSQL: `authentication type 10 is not supported`.** PostgreSQL 15 usa SCRAM-SHA-256 por padrão, mas
o driver JDBC embutido no Hive Metastore 3.1.3 é antigo demais para suportar isso. O serviço `postgres` em
`06-datastack.yml` já roda com `-c password_encryption=md5`; se você recriar o volume `/data/postgres` do
zero, esse flag precisa continuar lá (ou os usuários criados via `init.sql` voltam a usar SCRAM).

**`bitnami/spark` não existe mais no Docker Hub** (`docker pull` retorna "not found", não erro de
rede/autenticação). A Broadcom/VMware descontinuou as imagens gratuitas `bitnami/*` em 2025. A stack já usa
`bitnamilegacy/spark:3.5` (espelho congelado, mesma tag). Se isso voltar a acontecer com qualquer imagem
`bitnami/*`, procure o equivalente em `bitnamilegacy/<nome>` antes de assumir outro problema.

**Trino 435 rejeita propriedades que exemplos/docs antigos ainda mostram — erro fatal, não warning.**
`config.properties` não pode ter `query.max-total-memory-per-node` (obsoleta) nem `node.data-dir` duplicado
(já fica em `node.properties`); `catalog/hive.properties` não pode ter `hive.s3select-pushdown.enabled`
(obsoleta). Qualquer propriedade não reconhecida derruba o processo inteiro na inicialização.

**Trino: `AccessDeniedException` ao criar `spiller-spill-path`.** O container roda como uid 1000; o path de
spill precisa estar dentro de um volume já montado e gravável, ex: `/data/trino/spill` — não
`/mnt/data/trino-spill` (path solto no filesystem do container, sem permissão para uid 1000 criar).

**node-2/node-3: Spark Worker + Trino Worker brigando por memória no mesmo nó de 128GB.** Os dois foram
originalmente dimensionados como se cada um tivesse um nó de 128GB só pra ele (`SPARK_WORKER_MEMORY=90G` +
Trino `-Xmx100G`, reserva Docker de 80G cada) — juntos passam de 160G reservados num nó de 128GB, e o Swarm
deixa um dos dois preso em `Pending` ("insufficient resources"). Ajustado para caber os dois: Spark Worker
`SPARK_WORKER_MEMORY=40G` / limite Docker 48G / reserva 32G; Trino Worker `-Xmx40G` / limite 48G / reserva
32G — sobra espaço pro SeaweedFS (master+volume+filer) e o SO no mesmo nó.

**Superset: `superset_config.py` nunca era montado no container.** `stacks/08-apps-stack.yml` setava
`PYTHONPATH=/app/pythonpath` mas não montava o arquivo ali — Superset rodava só com config padrão (Celery
usando SQLite local em vez de Redis). Corrigido com bind mount de
`config/superset/superset_config.py:/app/pythonpath/superset_config.py:ro` em `superset` e
`superset-worker`.

**Superset: 500 em `/superset/welcome/` (`relation "user_attribute" does not exist`).** O schema do banco
nunca foi migrado — só existiam as 8 tabelas de auth (`ab_*`) do Flask-AppBuilder, sem nenhum usuário
criado. Causa raiz em `09-deploy-all.sh`: `docker ps -q -f name=apps_superset | head -1` é um filtro por
substring que também casa com `apps_superset-worker`; com `head -1` a escolha entre os dois containers é
indeterminada — se pegasse o worker, o `docker exec` falhava e o `set -e` abortava o script antes de rodar
`db upgrade`/`create-admin`/`init`. Corrigido para o filtro ancorado `-f "name=^apps_superset\."`. Se isso
acontecer de novo num cluster novo, rode manualmente: `superset db upgrade && superset fab create-admin
... && superset init` dentro do container `apps_superset` (não o `-worker`).

**SeaweedFS: portas de serviços replicados (master/volume/filer, 3 réplicas cada) em modo `ingress`
colidem entre si.** Publicar a mesma porta (`"9333:9333"`, etc) em 3 serviços diferentes falha com `port
already in use ... as an ingress port`, mesmo cada um estando pinado a um nó diferente — portas publicadas
no modo padrão do Swarm são globais ao cluster, não por nó. `stacks/05-seaweedfs-stack.yml` já publica
essas portas com `mode: host` (liga direto na interface do nó, sem passar pela routing mesh).

**SeaweedFS Volume (porta 8080) colide com Trino Coordinator (porta 8080) no node-1.** Ambos os serviços
podem cair no mesmo nó com `mode: host`; a stack já usa **8081** para o SeaweedFS Volume (`-port=8081` no
comando e no mapeamento de porta) para evitar o conflito.

**SeaweedFS: comandos usavam hostnames das VMs (`node-1`, `node-2`, `node-3`) em `-ip=`, `-peers=`,
`-mserver=`, `-master=`.** A rede overlay do Swarm só resolve nomes de **serviço** via DNS interno
(`seaweedfs-master-1`, etc) — hostnames de VM não resolvem lá dentro, causando `lookup node-1: no such
host` e crash-loop do master. A stack já usa os nomes de serviço corretos.

**SeaweedFS Master: `bind: cannot assign requested address` mesmo com o nome de serviço certo.** O master
faz `-ip=<proprio-nome-de-servico>` e tenta abrir socket nesse endereço — mas a resolução DNS padrão do
Swarm (`endpoint_mode: vip`) devolve o **VIP do serviço**, não o IP real da tarefa, e não dá pra fazer bind
nele. Os 3 serviços de master já têm `endpoint_mode: dnsrr` no `deploy:`, que faz o DNS devolver o IP real
da tarefa.

**SeaweedFS Volume: `bind source path does not exist: /mnt/data/seaweedfs`.** A stack original apontava
para um caminho que nunca é criado; o disco de 3TB real fica em `/data/seaweedfs/volume` (criado por
`01-base-setup.sh`). Já corrigido no bind mount.

**SeaweedFS: portas (9333/8333/8888/8081) recusam conexão pelo host/EIP (`ERR_CONNECTION_REFUSED`) apesar
do serviço estar UP.** Com `mode: host`, o docker-proxy encaminha a porta do host para o container pela
interface `docker_gwbridge` (172.18.x), mas o `-ip=seaweedfs-master-N` faz o SeaweedFS escutar **só no IP
da rede overlay** (10.0.3.x), nunca em 0.0.0.0 — então o encaminhamento nunca alcança o processo. Todos os
comandos (master/volume/filer) já incluem `-ip.bind=0.0.0.0` para escutar em todas as interfaces. (O
acesso via EIP ainda depende do Security Group liberar essas portas.)

**Portainer Agent (modo `global`) com o mesmo bug de porta em `ingress` do SeaweedFS.** Mesma causa e
mesmo fix: porta 9001 publicada com `mode: host`.

**SeaweedFS Admin: `404 page not found` ao clicar num bucket.** O dashboard é **ALPHA** e tem links
internos quebrados: o nome do bucket aponta para `/s3/buckets/<nome>` (rota inexistente). As rotas que
funcionam usam o prefixo `/object-store/`: detalhe do bucket em `/object-store/buckets/<nome>`, conteúdo no
File Browser em `/files?path=/buckets/<nome>`, lista em `/object-store/buckets`. O bucket em si é criado
normalmente (confirmável via S3 API `GET http://<ip-node-1>:8333/` ou no filer em `/buckets/<nome>`) — é
só o link da UI que está errado, não dá pra corrigir sem mexer no binário upstream.

**SeaweedFS Admin: cookie de sessão `Secure` (histórico).** O `weed admin` marca o cookie como **`Secure`**
(só trafega por HTTPS) — o que quebrava o acesso por IP em HTTP puro. Isso deixou de ser um problema: o
acesso agora é sempre por `https://seaweedfs.<eip>.sslip.io` (Traefik termina TLS), então o cookie
Secure trafega normalmente. A porta 23646 não é mais publicada no host.

## Alta disponibilidade

- **Cluster Swarm:** 3 managers em Raft — tolera a queda de 1 nó sem perder o quorum (`docker node ls` continua respondendo, novos serviços podem ser agendados nos nós restantes).
- **Spark Master agora É HA (2026-07-04):** 3 instâncias (`spark-master-1/2/3`, uma por nó) com
  `recoveryMode=ZOOKEEPER` (`spark.deploy.recoveryMode`) contra um ensemble ZooKeeper dedicado de 3 nós
  (`stacks/13-zookeeper.yml`, `zk-1/2/3`, um por nó). Failover testado (~40s: derruba o container líder,
  um standby assume, workers e clientes reconectam sozinhos) — jobs em execução sobrevivem (executores já
  registrados continuam rodando, independente do master). Todo cliente Spark (drivers, notebooks, Airflow,
  scripts) usa a URL multi-master `spark://spark-master-1:7077,spark-master-2:7077,spark-master-3:7077`; o
  serviço antigo singular `spark-master` **não existe mais**. Detalhes completos (por que ZK precisa de
  `endpoint_mode: dnsrr`, por que a config fica em `SPARK_MASTER_OPTS` e não em `spark-defaults.conf`, etc)
  em CLAUDE.md ("Spark Master HA via ZooKeeper").
- **Serviços ainda fixos por nó (sem HA):** PostgreSQL, Hive Metastore, Trino Coordinator e Redis seguem
  pinados no node-1 via placement constraint — se o node-1 cair, esses serviços específicos ficam
  indisponíveis até o node-1 voltar (não há standby/replica configurada). node-1 também segue sendo ponto
  único para Traefik (ingress único), Keycloak (único IdP) e Airflow (scheduler/dag-processor/triggerer).
  Isso é uma limitação de arquitetura atual, não um bug — um Postgres em cluster (replicação + failover) foi
  avaliado e propositalmente adiado como projeto separado.

## Servicos apos deploy

Todos os acessos de navegador são via Traefik (HTTPS/443) com login pelo Keycloak (usuário `superadmin` —
ver "Identidade única"). As antigas URLs por IP:porta cru **não existem mais** (portas fechadas). Login
único: autenticado num dos hosts do forward-auth, os demais desse grupo não pedem login de novo.

| Servico | URL | Auth |
|---|---|---|
| Keycloak (IdP; console admin usa `admin` do realm master) | https://keycloak.<eip>.sslip.io | — |
| Trino | https://trino.<eip>.sslip.io | OIDC nativo (OAuth2) |
| JupyterHub | https://jupyter.<eip>.sslip.io | OIDC nativo |
| Superset | https://superset.<eip>.sslip.io | OIDC nativo |
| Airflow | https://airflow.<eip>.sslip.io | OIDC nativo |
| Portainer | https://portainer.<eip>.sslip.io | OIDC nativo † |
| Spark Master UI (HA, LB nos 3 masters — ver "Alta disponibilidade") | https://spark.<eip>.sslip.io | SSO (forward-auth) |
| Spark History Server | https://spark-history.<eip>.sslip.io | SSO (forward-auth) |
| SeaweedFS Admin (dashboard) | https://seaweedfs.<eip>.sslip.io | SSO (forward-auth) |
| SeaweedFS Filer UI | https://seaweedfs-filer.<eip>.sslip.io | SSO (forward-auth) |
| SeaweedFS Master UI | https://seaweedfs-master.<eip>.sslip.io | SSO (forward-auth) |
| Swarmpit | https://swarmpit.<eip>.sslip.io | SSO (forward-auth) + login interno ‡ |
| Traefik Dashboard (read-only) | https://traefik.<eip>.sslip.io/dashboard/ | SSO (forward-auth) |
| S3 API (não-navegador; assinatura S3) | http://<ip-node-1>:8333 | chave/segredo S3 |

† Portainer: login OIDC via Keycloak (`superadmin` entra via `preferred_username`). Com OAuth ativo, o
Portainer CE só permite login interno do admin inicial (`admin`) — fallback anti-lockout. ‡ Swarmpit exige
login interno próprio além do gate SSO (login duplo).

TLS nas URLs `sslip.io` é **self-signed** (o navegador avisa; prossiga). `<svc>.<eip>.sslip.io`
resolve para o EIP sem DNS próprio — ver CLAUDE.md ("SSO / Ingress") para o padrão de hairpin (browser usa a
URL externa; chamadas internas de um serviço para o Keycloak usam `http://keycloak:8080`).

## Pendências

- **Swarmpit**: mantém login interno próprio (login duplo) e seu CouchDB roda sem autenticação própria
  (`COUCHDB_USER`/`PASSWORD` nunca definidos) — não publicado no host, mas vale endurecer numa próxima etapa.
- **Segredos placeholder vivos**: os `_CHANGE_ME` do repo público (client-secret do oauth2-proxy, cookie
  secret, senhas de DB/admin) estão em uso real; o Security Group (origem única) é o que torna isso
  tolerável. Rotacionar antes de abrir o acesso.

## Storage - SeaweedFS

- **Replicacao:** 010 (2 copias em racks diferentes)
- **Capacidade raw:** ~9 TB (3 nos x 3 TB)
- **Capacidade usavel:** ~4.5 TB com replicacao 2x
- **S3 endpoint:** http://<ip-node-1>:8333 externo, ou `http://seaweedfs-filer-1:8333` de dentro de outro
  container no `datastack-net` (sem autenticacao na rede interna)
- **Admin Dashboard:** https://seaweedfs.<eip>.sslip.io (via Traefik + SSO forward-auth) — UI de
  administracao (volumes, buckets do Object Store, users/policies do S3, file browser, metricas, logs). O
  login agora é o SSO do Keycloak; o `-adminPassword` interno do `weed admin` (placeholder
  `seaweedfs_admin_CHANGE_ME` em `stacks/05-seaweedfs-stack.yml`) segue existindo como camada da própria
  ferramenta, mas o acesso externo é gated pelo oauth2-proxy. A porta 23646 **não é mais publicada no host**.
  O subcomando `admin` so existe a partir do SeaweedFS 3.80 — por isso esse serviço usa a imagem `3.95`
  enquanto o resto do cluster segue na `3.65` (o admin fala com os masters via gRPC, compativel).

  **Antes do deploy**, gere o par de certificados TLS do admin (nao vao versionados; ver `.gitignore`):
  ```bash
  mkdir -p config/seaweedfs/tls && cd config/seaweedfs/tls
  openssl req -x509 -newkey rsa:2048 -nodes -keyout admin.key -out admin.crt -days 3650 \
    -subj "/CN=seaweedfs-admin" \
    -addext "subjectAltName=IP:<ip-node-1>,IP:<eip>,DNS:localhost"
  ```

## Acesso SSH

```bash
# Acesso externo (via EIP)
ssh root@<eip>

# De dentro do node-1, acesso aos outros nos
ssh root@<ip-node-2>   # node-2
ssh root@<ip-node-3>  # node-3
```

## VS Code Remote SSH

Adicione ao ~/.ssh/config:

```
Host node-1
    HostName <eip>
    User root

Host node-2
    HostName <ip-node-2>
    User root
    ProxyJump node-1

Host node-3
    HostName <ip-node-3>
    User root
    ProxyJump node-1
```
