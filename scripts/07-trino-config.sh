#!/bin/bash
set -e
# Script 07: Configura Trino coordinator e workers - Execute no node-1

echo "=== Configurando Trino ==="

# Cria estrutura de diretorios
mkdir -p /opt/trino/coordinator/catalog
mkdir -p /opt/trino/worker/catalog
mkdir -p /data/trino

# === COORDINATOR (node-1) ===
cat > /opt/trino/coordinator/config.properties << EOF
coordinator=true
node-scheduler.include-coordinator=false
http-server.http.port=8080
query.max-memory=50GB
query.max-memory-per-node=8GB
discovery-server.enabled=true
discovery.uri=http://node-1:8080
EOF

cat > /opt/trino/coordinator/node.properties << EOF
node.environment=production
node.id=coordinator-1
node.data-dir=/data/trino
EOF

cat > /opt/trino/coordinator/jvm.config << EOF
-server
-Xmx20G
-XX:G1HeapRegionSize=32M
-XX:+UseGCOverheadLimit
-XX:+HeapDumpOnOutOfMemoryError
-XX:+ExitOnOutOfMemoryError
EOF

# Catalogo Hive com SeaweedFS S3
cat > /opt/trino/coordinator/catalog/hive.properties << EOF
connector.name=hive
hive.metastore.uri=thrift://node-1:9083
hive.s3.endpoint=http://node-1:8333
hive.s3.aws-access-key=any
hive.s3.aws-secret-key=any
hive.s3.path-style-access=true
hive.s3.ssl.enabled=false
hive.allow-drop-table=true
EOF

# Catalogo PostgreSQL
cat > /opt/trino/coordinator/catalog/postgresql.properties << EOF
connector.name=postgresql
connection-url=jdbc:postgresql://node-1:5432/
connection-user=hive
connection-password=hive_password_CHANGE_ME
EOF

# === WORKER (node-2 e node-3) ===
cp -r /opt/trino/coordinator/catalog /opt/trino/worker/

cat > /opt/trino/worker/config.properties << EOF
coordinator=false
http-server.http.port=8080
query.max-memory=50GB
query.max-memory-per-node=20GB
discovery.uri=http://node-1:8080
EOF

cat > /opt/trino/worker/node.properties << EOF
node.environment=production
node.data-dir=/data/trino
EOF

cat > /opt/trino/worker/jvm.config << EOF
-server
-Xmx80G
-XX:G1HeapRegionSize=32M
-XX:+UseGCOverheadLimit
-XX:+HeapDumpOnOutOfMemoryError
-XX:+ExitOnOutOfMemoryError
EOF

# Copia configuracoes para node-2 e node-3
rsync -av /opt/trino/worker/ root@node-2:/opt/trino/worker/
rsync -av /opt/trino/worker/ root@node-3:/opt/trino/worker/

echo "=== Configuracao Trino concluida ==="
