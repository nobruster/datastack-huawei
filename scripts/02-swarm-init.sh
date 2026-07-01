#!/bin/bash
set -e
# ============================================================
# Script 02: Inicializa Docker Swarm - Execute SOMENTE no node-1
# ============================================================

echo "=== Inicializando Docker Swarm no node-1 ==="

# Inicializa o Swarm com o IP privado do node-1
docker swarm init --advertise-addr <ip-node-1>

echo ""
echo "======================================================"
echo "=== Swarm inicializado! ==="
echo "Execute os comandos abaixo em node-2 e node-3:"
echo "------------------------------------------------------"
docker swarm join-token manager | grep 'docker swarm join'
echo "======================================================"
echo ""
echo "Verificacao do cluster (aguarde alguns segundos):"
docker node ls
