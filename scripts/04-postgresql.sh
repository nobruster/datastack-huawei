#!/bin/bash
set -e
# Script 04: Instala e configura PostgreSQL - Execute no node-1

echo "=== Instalando PostgreSQL 15 no node-1 ==="

apt-get install -y postgresql-15 postgresql-client-15

# Move dados para o disco de dados
systemctl stop postgresql

# Configura postgresql.conf
cat >> /etc/postgresql/15/main/postgresql.conf << PGEOF
listen_addresses = '*'
max_connections = 200
shared_buffers = 8GB
effective_cache_size = 24GB
work_mem = 256MB
maintenance_work_mem = 2GB
wal_buffers = 64MB
PGEOF

# Permite acesso da rede interna
echo "host    all             all             <subnet-privada>          md5" >> /etc/postgresql/15/main/pg_hba.conf

systemctl start postgresql
systemctl enable postgresql

# Aguarda PostgreSQL iniciar
sleep 3

# Cria databases e usuarios
sudo -u postgres psql << PSQL
CREATE USER hive WITH PASSWORD 'hive_password_CHANGE_ME';
CREATE DATABASE hive_metastore OWNER hive;
CREATE USER superset WITH PASSWORD 'superset_password_CHANGE_ME';
CREATE DATABASE superset OWNER superset;
GRANT ALL PRIVILEGES ON DATABASE hive_metastore TO hive;
GRANT ALL PRIVILEGES ON DATABASE superset TO superset;
\q
PSQL

echo "=== PostgreSQL configurado ==="
sudo -u postgres psql -c "\l"
