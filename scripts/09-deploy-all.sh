#!/bin/bash
set -e
# Script 09: Deploy de todas as stacks - Execute no node-1

echo "=== Iniciando deploy da stack completa ==="
cd /opt/stacks

# Copia os arquivos de stack do repositorio
cp /opt/datastack/stacks/*.yml /opt/stacks/

# 1. SeaweedFS
echo "--- Deployando SeaweedFS ---"
docker stack deploy -c 05-seaweedfs-stack.yml seaweedfs
echo "Aguardando SeaweedFS iniciar (30s)..."
sleep 30

# 2. Data Stack (Hive + Spark + Trino)
echo "--- Deployando Data Stack ---"
docker stack deploy -c 06-datastack.yml datastack
echo "Aguardando datastack iniciar (30s)..."
sleep 30

# 3. Apps (Redis + JupyterHub + Superset + Portainer)
echo "--- Deployando Apps ---"
docker stack deploy -c 08-apps-stack.yml apps
echo "Aguardando apps iniciar (30s)..."
sleep 30

# 4. Swarmpit (gerenciamento do Swarm)
echo "--- Deployando Swarmpit ---"
docker stack deploy -c 10-swarmpit-stack.yml swarmpit
echo "Aguardando Swarmpit iniciar (30s)..."
sleep 30

# 5. Inicializa o Superset
# Filtro ANCORADO (^apps_superset\.): "name=apps_superset" sem ancora e um
# substring match que tambem bate em "apps_superset-worker" - com head -1 a
# escolha entre os dois containers e indeterminada. Se pegar o worker (que
# pode estar crash-looping, ver CLAUDE.md) o docker exec falha e o `set -e`
# aborta o script antes de completar db upgrade/create-admin/init, deixando
# o Superset com schema vazio e zero usuarios (500 em /superset/welcome/).
echo "--- Inicializando Superset ---"
SUPERSET_CONTAINER=$(docker ps -q -f "name=^apps_superset\." | head -1)
if [ -n "$SUPERSET_CONTAINER" ]; then
    docker exec $SUPERSET_CONTAINER superset db upgrade
    docker exec $SUPERSET_CONTAINER superset fab create-admin         --username admin         --firstname Admin         --lastname User         --email admin@datastack.local         --password admin123
    docker exec $SUPERSET_CONTAINER superset init
fi

echo ""
echo "============================================="
echo "=== Stack implantada com sucesso! ==="
echo "============================================="
echo "Portainer:    http://<ip-node-1>:9000"
echo "Swarmpit:     http://<ip-node-1>:888"
echo "Spark UI:     http://<ip-node-1>:8090"
echo "Trino UI:     http://<ip-node-1>:8080"
echo "JupyterHub:   http://<ip-node-1>:8000"
echo "Superset:     http://<ip-node-1>:8088"
echo "SeaweedFS UI: http://<ip-node-1>:9333"
echo "S3 API:       http://<ip-node-1>:8333"
echo "============================================="
docker stack ls
docker service ls
