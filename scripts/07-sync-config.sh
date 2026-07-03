#!/bin/bash
set -e
# ============================================================
# Script 07: Sincroniza config/ do repositorio para node-2 e node-3
# Execute SOMENTE no node-1, com o repositorio clonado em /opt/datastack
#
# As stacks (06-datastack.yml) fazem bind mount de /opt/datastack/config/...
# Como bind mounts do Swarm sao locais a cada no, essa pasta precisa
# existir identica em node-1, node-2 e node-3 antes do deploy.
#
# PRE-REQUISITO: rode scripts/render-to-opt.sh ANTES deste script. Este
# script so copia bytes (rsync puro, sem substituicao) - ele sincroniza o
# lado node-1 de config/, que so tem valores reais (placeholders resolvidos
# + segredos reais do Trino) depois do render. Rodar isto direto apos um
# `git pull`/`cp` cru replicaria placeholders literais para node-2/node-3.
# ============================================================

REPO_DIR="/opt/datastack"
NODES=(node-2 node-3)

echo "=== Sincronizando ${REPO_DIR}/config para ${NODES[*]} ==="

for n in "${NODES[@]}"; do
    ssh "root@$n" "mkdir -p ${REPO_DIR}"
    rsync -av --delete "${REPO_DIR}/config/" "root@$n:${REPO_DIR}/config/"
done

echo "=== Sincronizacao concluida ==="
