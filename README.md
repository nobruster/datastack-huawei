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
| node-1 | <ip-node-1> | <eip> | Spark Master, Trino Coordinator, Hive, PostgreSQL, JupyterHub, Superset, Redis, Portainer |
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
| Portainer | 2.20.3 | 9000 | node-1 |

## Estrutura do Repositório

```
datastack-huawei/
├── README.md
├── scripts/
│   ├── 01-base-setup.sh          # Setup base (Docker, disco, hosts) - todos os nos
│   ├── 02-swarm-init.sh          # Inicializa Docker Swarm no node-1
│   ├── 03-swarm-networks.sh      # Cria rede overlay e labels
│   ├── 04-postgresql.sh          # Instala e configura PostgreSQL no node-1
│   ├── 07-trino-config.sh        # Configura Trino (coordinator + workers)
│   └── 09-deploy-all.sh          # Deploy completo de todas as stacks
├── stacks/
│   ├── 05-seaweedfs-stack.yml    # SeaweedFS distribuido (Masters + Volumes + Filers)
│   ├── 06-datastack.yml          # Hive + Spark + Trino
│   └── 08-apps-stack.yml         # Redis + JupyterHub + Superset + Portainer
└── config/
    ├── trino/
    │   ├── coordinator/          # Configuracoes do Trino Coordinator
    │   └── worker/               # Configuracoes dos Trino Workers
    └── spark/
        └── spark-defaults.conf   # Configuracoes do Spark (S3/SeaweedFS)
```

## Deploy - Ordem de Execucao

```bash
# 1. Em TODOS os nos (node-1, node-2, node-3)
bash scripts/01-base-setup.sh

# 2. Somente no node-1
bash scripts/02-swarm-init.sh
# Copie o token gerado e execute em node-2 e node-3:
docker swarm join --token <TOKEN> <ip-node-1>:2377

# 3. Somente no node-1
bash scripts/03-swarm-networks.sh
bash scripts/04-postgresql.sh
bash scripts/07-trino-config.sh
bash scripts/09-deploy-all.sh
```

## Servicos apos deploy

| Servico | URL |
|---|---|
| Portainer | http://<ip-node-1>:9000 |
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
