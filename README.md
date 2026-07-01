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
| node-1 | <ip-node-1> | <eip> | Spark Master, Trino Coordinator, Hive, PostgreSQL, JupyterHub, Superset, Redis, Portainer, Swarmpit |
| node-2 | <ip-node-2> | - | Spark Worker, Trino Worker, SeaweedFS |
| node-3 | <ip-node-3> | - | Spark Worker, Trino Worker, SeaweedFS |

## Componentes da Stack

| Serviço | Versão | Porta | Nó |
|---|---|---|---|
| Apache Spark Master | 3.5 | 8090, 7077 | node-1 |
| Apache Spark Worker | 3.5 | 8091, 8092 | node-2, node-3 |
| Trino Coordinator | 435 | 8080 | node-1 |
| Trino Worker | 435 | 8080 | node-2, node-3 |
| Hive Metastore | 3.1.3 | 9083 | node-1 |
| PostgreSQL | 15 | 5432 | node-1 (local) |
| JupyterHub | 4.1 | 8000 | node-1 |
| Apache Superset | 3.1.3 | 8088 | node-1 |
| Redis | 7 | 6379 | node-1 |
| SeaweedFS | 3.65 | 9333/8333/8888 | todos |
| Portainer | 2.20.3 | 9443 | node-1 |
| Swarmpit | 1.10 (app) / latest (agent) | 888 | node-1 (app), todos (agent) |

## Estrutura do Repositório

```
datastack-huawei/
├── README.md
├── scripts/
│   ├── 00-ssh-setup.sh           # Configura SSH sem senha do node-1 para node-2/node-3
│   ├── 01-base-setup.sh          # Setup base (Docker, disco, hosts) - todos os nos
│   ├── 02-swarm-init.sh          # Inicializa Docker Swarm no node-1
│   ├── 03-swarm-networks.sh      # Cria rede overlay e labels (role, name)
│   ├── 04-postgresql.sh          # Instala e configura PostgreSQL no node-1
│   ├── 07-sync-config.sh         # Sincroniza config/ para node-2/node-3 (bind mounts)
│   └── 09-deploy-all.sh          # Deploy completo de todas as stacks
├── stacks/
│   ├── 05-seaweedfs-stack.yml    # SeaweedFS distribuido (Masters + Volumes + Filers)
│   ├── 06-datastack.yml          # Hive + Spark + Trino
│   ├── 08-apps-stack.yml         # Redis + JupyterHub + Superset + Portainer
│   └── 10-swarmpit-stack.yml     # Swarmpit (gerenciamento do Swarm)
└── config/
    ├── trino/
    │   ├── coordinator/          # Configuracoes do Trino Coordinator (inclui catalog/ hive + postgresql)
    │   └── worker/               # Configuracoes dos Trino Workers (inclui catalog/ hive + postgresql)
    └── spark/
        └── spark-defaults.conf   # Configuracoes do Spark (S3/SeaweedFS)
```

O repositório deve ser clonado em **`/opt/datastack`** em node-1 — `09-deploy-all.sh` e `07-sync-config.sh`
assumem esse caminho.

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
bash scripts/04-postgresql.sh
bash scripts/07-sync-config.sh
bash scripts/09-deploy-all.sh
```

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

**`04-postgresql.sh` falha com "Unable to locate package postgresql-15".** Ubuntu 22.04 (jammy) só tem
PostgreSQL 14 nos repositórios padrão. O script já adiciona o repositório oficial PGDG
(`apt.postgresql.org`) automaticamente antes de instalar — se isso ainda falhar, confirme que o node tem
acesso a `apt.postgresql.org` (mesma questão de NAT Gateway acima).

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

**Portainer Agent (modo `global`) com o mesmo bug de porta em `ingress` do SeaweedFS.** Mesma causa e
mesmo fix: porta 9001 publicada com `mode: host`.

## Alta disponibilidade

- **Cluster Swarm:** 3 managers em Raft — tolera a queda de 1 nó sem perder o quorum (`docker node ls` continua respondendo, novos serviços podem ser agendados nos nós restantes).
- **Serviços fixos por nó:** Spark Master, Trino Coordinator, Hive Metastore, PostgreSQL e Redis são pinados no node-1 via placement constraint — se o node-1 cair, esses serviços especificos ficam indisponíveis até o node-1 voltar (não há standby/replica configurada). Isso é uma limitação de arquitetura atual, não um bug.

## Servicos apos deploy

| Servico | URL |
|---|---|
| Portainer | https://<ip-node-1>:9443 |
| Swarmpit | http://<ip-node-1>:888 |
| Spark UI | http://<ip-node-1>:8090 |
| Trino UI | http://<ip-node-1>:8080 |
| JupyterHub | http://<ip-node-1>:8000 |
| Superset | http://<ip-node-1>:8088 |
| SeaweedFS UI | http://<ip-node-1>:9333 |
| S3 API | http://<ip-node-1>:8333 |

## Storage - SeaweedFS

- **Replicacao:** 010 (2 copias em racks diferentes)
- **Capacidade raw:** ~9 TB (3 nos x 3 TB)
- **Capacidade usavel:** ~4.5 TB com replicacao 2x
- **S3 endpoint:** http://node-1:8333 (sem autenticacao na rede interna)

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
