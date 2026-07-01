#!/bin/bash
set -e
# Script 03: Configura redes e labels do Swarm - Execute no node-1

echo "=== Configurando redes e labels do Swarm ==="

# Cria rede overlay para a stack
docker network create --driver overlay --attachable datastack-net 2>/dev/null || echo "Rede ja existe"

# Labels nos nos para placement constraints (usado por node.labels.name nas stacks)
docker node update --label-add role=master --label-add name=node-1 node-1
docker node update --label-add role=worker --label-add name=node-2 node-2
docker node update --label-add role=worker --label-add name=node-3 node-3

# Cria diretorios para stacks
mkdir -p /opt/stacks

echo "=== Configuracao concluida ==="
docker network ls | grep datastack
docker node ls
