#!/bin/bash
set -e
# ============================================================
# Script 01: Setup base - Execute em TODOS os nos (node-1, node-2, node-3)
# ============================================================

echo "=== Iniciando setup base em $(hostname) ==="

# Evita que dpkg trave em prompt interativo de conffile (ex: sshd_config
# ja modificado) quando o script roda sem TTY.
export DEBIAN_FRONTEND=noninteractive
APT_CONFOPTS=(-o Dpkg::Options::=--force-confold -o Dpkg::Options::=--force-confdef)

# Atualiza o sistema
apt-get update && apt-get -y "${APT_CONFOPTS[@]}" upgrade
apt-get install -y curl wget git vim htop net-tools nfs-common \
    software-properties-common apt-transport-https ca-certificates gnupg lsb-release

# Instala Docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Configura Docker daemon
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << DOCKEREOF
{
  "log-driver": "json-file",
  "log-opts": {"max-size": "100m", "max-file": "3"},
  "default-ulimits": {"nofile": {"Hard": 65536, "Name": "nofile", "Soft": 65536}}
}
DOCKEREOF
systemctl restart docker
systemctl enable docker

# Configura /etc/hosts para resolucao de nomes entre nos
grep -q "node-1" /etc/hosts || cat >> /etc/hosts << HOSTSEOF
<ip-node-1> node-1
<ip-node-2>  node-2
<ip-node-3> node-3
HOSTSEOF

# Monta o disco de dados 3TB
DISK=$(lsblk -dn -o NAME,SIZE | awk '$2=="3T"{print $1}' | head -1)
if [ -n "$DISK" ] && ! mountpoint -q /data; then
    echo "Formatando e montando disco /dev/$DISK..."
    mkfs.ext4 -F /dev/$DISK
    mkdir -p /data
    echo "/dev/$DISK /data ext4 defaults,noatime 0 2" >> /etc/fstab
    mount -a
fi

# Cria diretorios de dados
mkdir -p /data/seaweedfs/master /data/seaweedfs/volume /data/seaweedfs/filer
mkdir -p /data/postgres /data/jupyter /data/spark /data/trino

# Ajusta limites do sistema
cat >> /etc/sysctl.conf << SYSCTLEOF
vm.max_map_count=262144
fs.file-max=65536
net.core.somaxconn=65535
net.ipv4.ip_forward=1
SYSCTLEOF
sysctl -p

echo "=== Setup base concluido em $(hostname) ==="
echo "Docker version: $(docker --version)"
echo "Disco /data: $(df -h /data 2>/dev/null | tail -1 || echo 'nao montado')"
