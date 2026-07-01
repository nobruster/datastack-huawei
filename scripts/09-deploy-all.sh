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

# 4. Inicializa o Superset
echo "--- Inicializando Superset ---"
SUPERSET_CONTAINER=$(docker ps -q -f name=apps_superset | head -1)
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
echo "Spark UI:     http://<ip-node-1>:8090"
echo "Trino UI:     http://<ip-node-1>:8080"
echo "JupyterHub:   http://<ip-node-1>:8000"
echo "Superset:     http://<ip-node-1>:8088"
echo "SeaweedFS UI: http://<ip-node-1>:9333"
echo "S3 API:       http://<ip-node-1>:8333"
echo "============================================="
docker stack ls
docker service ls
