# Distribuição de serviços entre nós (placement/topologia)

> **Estudo de referência — 2026-07-04, somente-leitura.** Este documento reflete o
> estado **medido** do cluster naquela data (medição ~03:46 UTC, cluster ocioso). Ele
> **não** foi aplicado: nada aqui alterou serviço, stack ou config. É análise, não deploy.
>
> O placement **pode evoluir**; a fonte viva do que está pinado onde é o `CLAUDE.md`
> (seções [Topology](../CLAUDE.md#topology) e
> [High-availability scope](../CLAUDE.md#high-availability-scope)) e os próprios
> `stacks/*.yml`. Se este doc divergir do cluster real no futuro, o `CLAUDE.md`/stacks
> ganham — reavalie antes de citar estes números.

---

## Sumário executivo

O placement atual **não é** um problema de balanceamento de carga — é **concentração de
SPOF**. A separação existente é sã e casa com os *fault domains*:

- **node-1 = plano de controle / ingresso / metadados** — forçado por restrições duras
  (único EIP/egress, bind-mounts por nó, publicação 80/443).
- **node-2 / node-3 = plano de compute** — Spark Worker + Trino Worker.

Três teses centrais:

1. **O placement atual é são.** A assimetria control-plane × compute-plane é proposital e
   correta; não há rebalanceamento de recurso a fazer.
2. **O eixo é SPOF, não recurso.** O desequilíbrio real é de **contagem de serviços e
   risco** (node-1 hospeda 28 tasks — todo o estado e todos os SPOF; node-2/3, 9 cada),
   não de consumo.
3. **Há folga de recurso enorme.** No momento da medição o cluster está ocioso; node-1 usa
   ~13 GiB de 121,7 GiB. Em **reserva declarada**, quem está cheio é node-2/3 (54%), não
   node-1 (22%).

Consequência: quase todas as recomendações são de **HA** (espalhar SPOF), não de
rebalanceamento. E, dadas as restrições, HA verdadeiro do plano node-1 é caro e
parcialmente impossível sem mudar a infraestrutura (NAT Gateway, 2º EIP).

---

## Restrições duras que governam o placement

Estas amarras não são preferência de estilo — são invariantes de infraestrutura/rede que
**determinam** onde cada serviço pode rodar. Detalhes canônicos vivem no `CLAUDE.md`; aqui
só o resumo que amarra o placement:

| Restrição | Efeito no placement | Fonte |
|---|---|---|
| **EIP único / egress só no node-1** | Tudo que precisa de internet (download de tarball/`--packages`, landing de dados externos) ou de ingresso público fica preso ao node-1. | [CLAUDE.md · Topology](../CLAUDE.md#topology) |
| **node-2/3 sem egress até o NAT Gateway** | Nós privados não fazem `docker pull`/`apt`/download; nada que exija egress pode ir para lá **antes** do NAT Gateway (SNAT da `<subnet-privada>`). | [CLAUDE.md · Topology](../CLAUDE.md#topology); [README · Troubleshooting](../README.md#problemas-conhecidos--troubleshooting) |
| **Bind-mounts são por nó, não cluster-wide** | Serviços com config bind-montada de `/opt/datastack/config/...` só sobem no nó onde o diretório existe; mover exige `07-sync-config.sh` no destino. | [CLAUDE.md · Invariants (bind mounts)](../CLAUDE.md#invariants-that-must-stay-consistent-across-files) |
| **Traefik em `mode:host` no manager** | Ingresso único publica 80/443 direto na interface do node-1 (socket Swarm + EIP); não é replicável sem 2º EIP/LB externo. | [CLAUDE.md · SSO / Ingress](../CLAUDE.md#sso--ingress-traefik--keycloak--stacks11-ingressyml) |
| **Postgres containerizado, instância única em node-1** | Bind-mount `/data/postgres` + DB de metadados compartilhado (Hive/Superset/Keycloak/Airflow); cluster HA foi avaliado e adiado. | [CLAUDE.md · PostgreSQL is containerized](../CLAUDE.md#postgresql-is-containerized-not-native) |
| **Orçamento de memória node-2/3 (Spark+Trino Worker no mesmo nó de 128 GB)** | Reservas ajustadas (32G+32G) já ocupam 54% do nó; não sobra folga grande para mais um serviço pesado ali. | [CLAUDE.md · Invariants (node-2/3 budgets)](../CLAUDE.md#invariants-that-must-stay-consistent-across-files) |

---

## Inventário medido (2026-07-04)

### Nós (specs)

Três managers idênticos, Ready/Active, quórum Raft OK. node-1 Leader; node-2/3 Reachable;
engine 29.6.1.

| Nó | NanoCPUs | MemoryBytes | = | Labels |
|---|---|---|---|---|
| node-1 | 32e9 (32 vCPU) | 130694889472 | 121,7 GiB | `name=node-1`, `role=master`, Leader |
| node-2 | 32 vCPU | 130694897664 | 121,7 GiB | `name=node-2`, `role=worker` |
| node-3 | 32 vCPU | 130694893568 | 121,7 GiB | `name=node-3`, `role=worker` |

### Distribuição atual de tasks (running)

- **node-1 (28 tasks):** postgres, hive-metastore, trino-coordinator, spark-master-1,
  spark-history, zk-1, seaweedfs master-1/volume-1/filer-1/admin, redis, jupyterhub (+1
  notebook `jupyter-superadmin` spawnado), superset, superset-worker, portainer (+agent),
  swarmpit app/db/influxdb (+agent), airflow api-server/scheduler/dag-processor/triggerer,
  keycloak, sso-auth, traefik.
- **node-2 (9 tasks):** spark-master-2, spark-worker-node2, trino-worker-node2, zk-2,
  seaweedfs master-2/volume-2/filer-2, portainer-agent, swarmpit-agent.
- **node-3 (9 tasks):** simétrico ao node-2.

### Uso real (docker stats + free -g)

| Nó | RAM usada | buff/cache | disponível | load 1m | Soma containers |
|---|---|---|---|---|---|
| node-1 | ~13 GiB | 37 GiB | 106 GiB | 0.58 | ~12,1 GiB |
| node-2 | ~4 GiB | 14 GiB | 116 GiB | 0.00 | ~4,0 GiB |
| node-3 | ~3 GiB | 10 GiB | 116 GiB | 0.00 | ~3,3 GiB |

Maiores consumidores (com o cluster ocioso): superset-worker 2,91 GiB, trino-coordinator
1,64 GiB (node-1); trino-worker 2,17 / 2,19 GiB (node-2/3); airflow api-server 1,17 GiB.
**Nada perto dos limites.**

### Reservas declaradas vs. limites

Só a *memory reservation* conta para o scheduler do Swarm; **não há reserva de CPU em
nenhum serviço**.

| Serviço | Nó | reservation | limit | JVM/mem |
|---|---|---|---|---|
| postgres | 1 | 4G | 8G | `shared_buffers=2GB`, `effective_cache_size=6GB` |
| hive-metastore | 1 | 2G | 4G | — |
| trino-coordinator | 1 | 16G | 24G | `-Xmx24G`, `query.max-memory-per-node=8GB` |
| spark-history | 1 | 2G | 4G | — |
| spark-master-1/2/3 | 1/2/3 | 1G | 2G | — |
| keycloak | 1 | 1G | 2G | — |
| swarmpit app | 1 | 512M | 1G | — |
| zk-1/2/3 | 1/2/3 | 512M | 1G | `-Xmx768m` |
| spark-worker-node2/3 | 2/3 | 32G | 48G | `SPARK_WORKER_MEMORY=40G` |
| trino-worker-node2/3 | 2/3 | 32G | 48G | `-Xmx40G` |
| airflow api/sched/dag/trig | 1 | — | 2/4/1/1G | só limits |
| redis, jupyterhub, superset(+worker), portainer, seaweedfs\*, traefik, sso-auth, swarmpit db/influx | vários | — | — | sem reserva/limit |

**Reservado por nó (de 121,7 GiB):**

| Nó | Reservado | % do nó |
|---|---|---|
| node-1 | ~27 GiB | **22%** |
| node-2 | ~65,5 GiB | **54%** |
| node-3 | ~65,5 GiB | **54%** |

**Achado-chave:** em **reserva**, quem está cheio é node-2/3 (54%), **não** node-1. O
node-1 tem folga enorme — e é onde vive **todo** o risco. node-1 não tem Spark/Trino
Worker → durante um job pesado o compute satura node-2/3 e o node-1 fica livre para
metadados/ingresso. Assimetria proposital e boa.

---

## Recomendação oficial por ferramenta

Marcação: **[DOC]** = texto literal/direto da documentação oficial; **[OPINIÃO]** =
análise deste estudo sobre como a doc se aplica ao cluster.

Veredito: **cumprido** / **divergência tolerada** / **candidato a mudança**.

### Trino 435 — cumprido
- **[DOC]** "dedicating a machine to only perform coordination work provides the best
  performance on larger clusters"; `node-scheduler.include-coordinator=false`.
- Fonte: <https://trino.io/docs/current/installation/deployment.html>
- **[OPINIÃO]** Já cumprido (confirmado): coordinator dedicado, `include-coordinator=false`.
  **Manter.**

### Spark 3.5 Standalone — cumprido
- **[DOC]** master/worker podem coexistir; a doc não exige master dedicado; o único
  trade-off citado é *spread-vs-consolidate* de drivers.
- Fonte: <https://spark.apache.org/docs/latest/spark-standalone.html>
- **[OPINIÃO]** master é leve (383 MiB, ~0% CPU). 3 masters + workers em node-2/3 +
  History no node-1 está correto. **Manter.**

### ZooKeeper 3.9 — divergência tolerada
- **[DOC forte]** "ZooKeeper's transaction log must be on a dedicated device"; "if
  ZooKeeper has to contend ... performance will suffer markedly"; ensemble ímpar (3 mín, 5
  melhor).
- Fonte: <https://zookeeper.apache.org/doc/r3.9.3/zookeeperAdmin.html>
- **[OPINIÃO]** **Maior divergência doc-vs-realidade:** os 3 ZK dividem o mesmo `/data` com
  SeaweedFS volume + Postgres + Spark. Mas a carga é trivial (só eleição do Spark Master
  HA; 142 MiB, ~0,06% CPU). Disco dedicado é para ZK de alta escrita (Kafka/HBase); aqui é
  desprezível. Aceitável co-locar. Ressalva: sob escrita S3 pesada sustentada no `/data`, o
  fsync do txnlog *pode* competir — risco baixo. Não vale disco dedicado. **Manter (ciente
  da ressalva).**

### SeaweedFS — cumprido (com ressalva de metadados)
- **[DOC]** masters Raft "at least three replicas for HA" (ímpar ✓); volume guarda dado
  local ✓; filer "stateless server" com store externo.
- Fontes: <https://github.com/seaweedfs/seaweedfs/blob/master/README.md> ·
  <https://deepwiki.com/seaweedfs/seaweedfs/1.1-architecture>
- **[OPINIÃO]** masters 3/quórum ✓, volume local ✓. Ressalva: filers provavelmente com
  store embutido por-nó, e os clientes S3 apontam fixo para `seaweedfs-filer-1` (node-1) →
  filer-1 é ponto quente/SPOF do caminho S3, apesar de 3 filers existirem. É tema de
  **metadados de filer**, não de placement — sinalizar, não mexer agora. **Manter.**

### PostgreSQL 15 — cumprido (placement); tuning opcional
- **[DOC]** `shared_buffers` ~25% da RAM "for a dedicated database server"; teto útil ~40%.
- Fonte: <https://www.postgresql.org/docs/15/runtime-config-resource.html>
- **[OPINIÃO]** o nó **não** é dedicado; Postgres aqui é metadados (HMS/Superset/Keycloak/
  Airflow). `shared_buffers=2GB` é conservador mas serve; com 106 GiB livres dá para subir a
  8–16GB se houver contenção, mas **não há sinal** disso. `shared_buffers` é **tuning, não
  topologia**. **Manter placement** (bind-mount `/data/postgres` + DB compartilhado).

### Hive Metastore 3.1.3 — candidato a mudança
- **[DOC]** "The Hive metastore is stateless and thus there can be multiple instances to
  achieve High Availability"; clientes com `hive.metastore.uris` multi-URI, failover
  aleatório.
- Fonte: <https://hive.apache.org/docs/latest/admin/adminmanual-metastore-administration/>
- **[OPINIÃO]** hoje todos os clientes usam a URI única `thrift://hive-metastore:9083` →
  **SPOF de todo schema-op** de Trino/Spark. **Candidato legítimo a HA** (a melhor relação
  ganho/risco do estudo).

### Redis 7 — cumprido (co-location tolerada)
- **[DOC]** configurar `maxmemory`; "Do not run any other memory-intensive processes on the
  Redis node".
- Fonte: <https://redis.io/docs/latest/operate/oss_and_stack/management/admin/>
- **[OPINIÃO]** `maxmemory 4gb allkeys-lru` **já setado**, uso de 4 MiB. A ressalva de
  co-location é violada no papel, mas o cache é leve e irrelevante. **Manter.**

### JupyterHub 4 + DockerSpawner — cumprido (preso por egress)
- **[DOC]** single-user servers sobem "on the same machine running" o Hub; DockerSpawner
  cria containers no host do spawner.
- Fonte: <https://jupyterhub.readthedocs.io/en/stable/reference/technical-overview.html>
- **[OPINIÃO]** notebooks rodam no node-1; o driver Spark em client-mode no notebook precisa
  de egress (baixa tarball Spark 3.5.6 + Ivy). **Preso ao node-1 por egress. Manter.**

### Superset 3.1 — cumprido no placement; gap funcional (celery-beat)
- **[DOC]** produção = web + Celery worker + **Celery beat (scheduler)** + Postgres + Redis,
  escaláveis separadamente.
- Fontes: <https://superset.apache.org/admin-docs/installation/architecture/> ·
  <https://superset.apache.org/docs/configuration/async-queries-celery/>
- **[OPINIÃO]** tem `superset` + `superset-worker`; há `beat_schedule` no config **mas sem
  processo celery beat rodando** → relatórios/alertas agendados **não disparam**. É **gap
  funcional** (não placement). O worker poderia ir para node-2/3, mas node-1 não está
  saturado → baixo valor. **Candidato a mudança (funcional): adicionar celery-beat.**

### Airflow 3.0 LocalExecutor — cumprido
- **[DOC]** "runs tasks by spawning processes ... on the scheduler node" → tasks presas ao
  nó do scheduler; cuidar `parallelism`.
- Fonte: <https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/executor/local.html>
- **[OPINIÃO]** a limitação **não morde**: as tasks são orquestradores finos (`docker exec`
  no spark-master); o compute pesado roda no Spark node-2/3. O scheduler precisa do node-1
  (egress da landing de ~11GB + socket do Docker). CeleryExecutor distribuiria, mas sem
  motivo. **Manter.**

### Keycloak 25 — candidato a mudança (estrutural)
- **[DOC]** "a typical production environment contains two or more Keycloak instances"
  (JGroups+Infinispan, TLS entre nós); DB externo ✓; reverse proxy ✓; hostname admin
  separado.
- Fonte: <https://www.keycloak.org/server/configuration-production>
- **[OPINIÃO]** instância única = **SPOF de toda a autenticação**. É o HA de **maior
  impacto de risco**, mas o mais caro (Infinispan + afinidade de sessão OAuth).
  **Estrutural.**

### Traefik 3 — cumprido (SPOF estrutural aceito)
- **[DOC]** Swarm com `mode:host` + placement constraint; rodar em manager (socket Swarm).
- Fonte: <https://doc.traefik.io/traefik/setup/swarm/>
- **[OPINIÃO]** preso ao node-1 (único EIP, publica 80/443 `mode:host`). **Ingress SPOF
  estrutural. Manter.**

### Portainer / Swarmpit — cumprido
- **[DOC]** management; sem recomendação de topologia relevante.
- **[OPINIÃO]** server/app no node-1, agents `global`. Manter (Swarmpit tem pins de nome de
  serviço — ver [CLAUDE.md · Invariants (Swarmpit)](../CLAUDE.md#invariants-that-must-stay-consistent-across-files)).

---

## Estado atual × recomendado

| Serviço | Nó atual | Recomendado | Justificativa | Fonte |
|---|---|---|---|---|
| Traefik / Keycloak / sso-auth | 1 | Manter 1 (Keycloak +1 réplica HA, estrutural) | EIP/ingress e IdP presos ao node-1; HA Keycloak precisa de Infinispan | Keycloak prod; restrição EIP |
| PostgreSQL | 1 | Manter 1 | bind-mount + DB de metadados compartilhado | PG docs; invariante bind-mount |
| Hive Metastore | 1 | Manter, avaliar 2ª instância (node-2 ou 3) | stateless → HA nativo multi-URI; hoje SPOF | Hive AdminManual |
| Trino coordinator | 1 | Manter (já dedicado) | `include-coordinator=false` já casa | Trino deployment |
| Trino worker ×2 | 2,3 | Manter | compute dedicado; orçamento ajustado | CLAUDE.md |
| Spark master ×3 | 1,2,3 | Manter | HA/ZK feito; master leve | Spark standalone |
| Spark worker ×2 | 2,3 | Manter | compute; node-1 sem worker deixa CPU livre | — |
| Spark history | 1 | Manter | leve; UI via Traefik | — |
| ZooKeeper ×3 | 1,2,3 | Manter (ciente da ressalva de disco) | ensemble ímpar ✓; carga trivial | ZK admin (divergência aceita) |
| SeaweedFS m/v/f ×3 | 1,2,3 | Manter | quórum ímpar + volume local | SeaweedFS arch |
| Redis | 1 | Manter | `maxmemory` ok; cache Superset | Redis admin |
| JupyterHub + notebooks | 1 | Manter | DockerSpawner local; driver precisa egress | JupyterHub overview |
| Superset web + worker | 1 | Manter; **ADICIONAR celery-beat** | beat configurado mas não roda (gap funcional) | Superset arch |
| Airflow (4 svc) | 1 | Manter | tasks orquestradores finos; scheduler precisa egress+socket | Airflow LocalExecutor |
| Portainer / Swarmpit | 1 + agents global | Manter | management, baixo valor mover | — |

**Síntese das leituras da tabela:**

- **(a) Mal-distribuído hoje: pouca coisa.**
  1. Concentração de SPOF no node-1 (Postgres, HMS, Trino-coord, Keycloak, Traefik,
     Airflow, Redis) — o ganho de espalhar é **disponibilidade**; o custo é que as
     restrições impedem mover a maioria.
  2. HMS instância única com clientes de URI única — o **único** SPOF que a doc oficial diz
     ser resolvível trivialmente (stateless + multi-URI); **melhor relação ganho/risco** do
     relatório.
  3. Celery beat do Superset ausente — **gap funcional**.
- **(b) Manter como está:** split control-plane × compute-plane (casa fault domains +
  restrições; não forçar worker no node-1); Trino coordinator dedicado; Spark Master HA /
  ZK / SeaweedFS quórum; Postgres / JupyterHub / Airflow / Traefik / Keycloak presos por
  restrição dura.

---

## Plano priorizado

Nada abaixo foi aplicado. Cada item é uma **tarefa futura própria** (ver "Como executar").

### Quick wins (baixo risco, sem pré-requisito de infra)

| # | Ação | Risco | Pré-requisito | Ganho |
|---|---|---|---|---|
| 1 | **Serviço celery-beat do Superset** (1 svc no node-1) | Baixo | — | Alertas/relatórios agendados passam a disparar (fecha o gap funcional) |
| 2 | **2ª instância Hive Metastore** em node-2/3 + clientes `thrift://hive-metastore-1:9083,thrift://hive-metastore-2:9083` | Baixo-médio (HMS stateless) | `core-site.xml` sincronizado via `07-sync-config`; **sem egress** | Tira o SPOF de schema-ops; **endossado por doc** |
| 3 | **(Opcional) subir `shared_buffers` do Postgres** se houver contenção | Baixo (tuning reversível) | Evidência de contenção (hoje não há) | Mais cache de metadados; margem existe (106 GiB livres) |

### Estruturais (alto valor de HA, alto custo/pré-requisito)

| # | Ação | Risco | Pré-requisito | Ganho |
|---|---|---|---|---|
| 4 | **HA Keycloak** (2ª réplica + Infinispan/JGroups + sticky session no Traefik) | Médio-alto | Afinidade de sessão OAuth; TLS entre nós | Remove o SPOF de autenticação — **o mais impactante** |
| 5 | **Egress node-2/3 via NAT Gateway** (SNAT `<subnet-privada>`, console Huawei) | Baixo (é config de VPC) | Acesso ao console VPC | **Pré-requisito destravador**: hoje cimenta a concentração no node-1; sem ele nada que exija egress pode sair de lá |
| 6 | **Alívio node-1** (só pós-NAT e se houver saturação real — **não há**): mover superset-worker p/ node-2/3 | Baixo | Item 5 | Ganho ~nulo com node-1 em 13/121 GiB |
| 7 | **HA PostgreSQL** (replicação + failover) | Alto | Projeto separado | Adiado no CLAUDE.md — **mantém adiado** |
| 8 | **Ingress HA (Traefik/EIP)** | Alto | 2º EIP ou LB externo Huawei | Fora do escopo dos 3 nós; SPOF estrutural **conscientemente aceito** |

**Ordem lógica:** o item **5 (NAT Gateway)** é o destravador — sem egress em node-2/3,
mover Airflow/Jupyter/qualquer coisa com `--packages`/download é impossível, então ele é
pré-requisito de qualquer flexibilização futura (itens 6+). Os melhores custo/benefício
imediatos são **item 2** (quick win endossado por doc) e, no eixo estrutural, **item 4**.

---

## Trade-offs e conclusão

**O recurso não é o vínculo.** Há folga enorme (node-1 em 13/121 GiB; node-2/3 ociosos). O
vínculo é **topológico**: 3 nós + node-2/3 sem egress + EIP único ⇒ o grupo do node-1
**não tem para onde ir**; HA real depende de **mudar a infra** (NAT Gateway, 2º EIP), não
de rebalancear.

**Onde a doc não recomenda co-location mas os 3 nós obrigam** (assumido conscientemente):

- **ZooKeeper dividindo disco** com SeaweedFS/Postgres/Spark — doc pede disco dedicado;
  carga trivial torna aceitável.
- **Postgres / Redis co-locados** com o resto do plano de controle — doc pede nó dedicado;
  são cargas leves de metadados/cache.
- **Keycloak / HMS single-instance** — doc pede múltiplas instâncias; a carga real medida é
  baixa o suficiente para tolerar, **exceto pelo risco de SPOF** — que é justamente o eixo
  onde vale investir (itens 2 e 4).

**Conclusão:** o placement atual é **bem projetado dentro das amarras**; não há
rebalanceamento de recurso a fazer. Os melhores custo/benefício são o **2º Hive Metastore**
(quick win endossado por doc) e, no estrutural, **HA Keycloak + NAT Gateway** (pré-requisito
de qualquer flexibilização futura). **Postgres-HA** e **Traefik/EIP-HA** ficam
conscientemente **adiados**.

---

## Como executar

**Nada neste documento foi aplicado.** Ele é um estudo somente-leitura; cada mudança do
plano é uma **tarefa futura própria**, a ser avaliada e executada separadamente.

Quando qualquer item **for** implementado, ele quase sempre toca um `stacks/*.yml` ou um
arquivo de `config/`. Nesse caso vale a regra do repo público:

> **Nunca** faça `cp`/`git pull` puro para `/opt/datastack`. O repo usa **placeholders**
> (`<eip>`, `<ip-node-1>`, `<ip-node-2>`, `<ip-node-3>`, `<subnet-privada>`); uma cópia crua
> instala a string literal `<eip>` numa config viva e quebra o serviço. O fluxo obrigatório
> é `scripts/render-to-opt.sh` (com `--check` antes), que substitui os placeholders pelos
> valores reais de `.site.env`.

Ver [CLAUDE.md · Repo is public: placeholders vs. real values (`scripts/render-to-opt.sh`)](../CLAUDE.md#repo-is-public-placeholders-vs-real-values-scriptsrender-to-optsh)
e [CLAUDE.md · Placeholder secrets](../CLAUDE.md#placeholder-secrets). Depois de renderizar,
os itens que mexem em config bind-montada exigem `07-sync-config.sh` (sincroniza para
node-2/3) antes de `docker service update --force` / redeploy da stack.

Este doc **não** é bind-montado e **não** precisa passar por `render-to-opt.sh` — é apenas
repo + git.
