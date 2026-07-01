#!/bin/bash
set -e
# ============================================================
# Script 00: Configura comunicacao SSH sem senha entre os nos
# Execute SOMENTE no node-1, depois que node-2 e node-3 ja
# estiverem acessiveis com senha (ou com a chave da nuvem).
#
# Necessario para:
#   - scripts/07-trino-config.sh (rsync das configs para node-2/node-3)
#   - qualquer administracao futura via SSH a partir do node-1
# ============================================================

NODES=(node-2 node-3)

echo "=== Configurando SSH sem senha do node-1 para ${NODES[*]} ==="

# Garante resolucao de nomes (ja deveria existir apos scripts/01-base-setup.sh)
grep -q "node-1" /etc/hosts || cat >> /etc/hosts << HOSTSEOF
<ip-node-1> node-1
<ip-node-2>  node-2
<ip-node-3> node-3
HOSTSEOF

# Gera par de chaves dedicado, se ainda nao existir
if [ ! -f /root/.ssh/id_ed25519 ]; then
    echo "Gerando novo par de chaves ed25519..."
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N "" -C "node-1-datastack"
fi

# Adiciona as chaves publicas dos nos ao known_hosts (evita prompt interativo)
mkdir -p /root/.ssh
touch /root/.ssh/known_hosts
for n in node-1 "${NODES[@]}"; do
    ssh-keyscan -H "$n" >> /root/.ssh/known_hosts 2>/dev/null
done
sort -u /root/.ssh/known_hosts -o /root/.ssh/known_hosts

# Copia a chave publica para cada no (pede senha uma unica vez por no)
for n in "${NODES[@]}"; do
    echo "--- Copiando chave publica para $n (informe a senha de root quando solicitado) ---"
    ssh-copy-id -i /root/.ssh/id_ed25519.pub "root@$n"
done

# Valida acesso sem senha
echo ""
echo "=== Validando acesso SSH sem senha ==="
for n in "${NODES[@]}"; do
    if ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$n" "echo OK - $(hostname 2>/dev/null || true)" 2>/dev/null; then
        echo "$n: OK"
    else
        echo "$n: FALHOU - verifique conectividade/senha e rode o script novamente"
        exit 1
    fi
done

echo "=== Comunicacao SSH entre os nos configurada com sucesso ==="
