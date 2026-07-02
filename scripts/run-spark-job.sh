#!/usr/bin/env bash
# ============================================================================
# Roda um job PySpark no container do spark-master (rodar no node-1).
#
# Por que este wrapper existe (tres armadilhas que quebram o comando "obvio"):
#   1. spark-master e um SERVICO Swarm -> o container tem sufixo aleatorio
#      (datastack_spark-master.1.<hash>). `docker exec spark-master ...` sempre
#      falha com "No such container". Resolvemos o ID de verdade via docker ps.
#   2. O job precisa estar DENTRO do container. `docker cp` o .py antes de rodar.
#   3. Submeter como uid 0 (docker exec -u 0): o exec entra fora do entrypoint
#      bitnami e o uid 1001 nao tem entrada em /etc/passwd -> o login UGI do
#      Hadoop quebra ("invalid null input: name"). HOME/ivy graváveis idem.
#
# Uso:
#   scripts/run-spark-job.sh jobs/landing-beneficios-v3.py
#   scripts/run-spark-job.sh jobs/bronze-beneficios-v2.py io.delta:delta-spark_2.12:3.2.0
#
# 2o argumento (opcional): coordenadas Maven para --packages (ex.: Delta Lake,
# que nao vem na imagem bitnami/spark). Resolve no node-1 (tem egress) e o
# driver envia os jars aos executors.
# ============================================================================
set -euo pipefail

JOB="${1:?uso: run-spark-job.sh <arquivo.py> [pacotes_maven]}"
PKGS="${2:-}"

if [ ! -f "$JOB" ]; then
  echo "ERRO: job nao encontrado: $JOB" >&2
  exit 1
fi

CID=$(docker ps --filter name=datastack_spark-master -q | head -1)
if [ -z "$CID" ]; then
  echo "ERRO: container do spark-master nao encontrado." >&2
  echo "      Rode no node-1 e confira: docker service ls | grep spark-master" >&2
  exit 1
fi

BASE=$(basename "$JOB")
echo ">> spark-master container: $CID"
echo ">> copiando $JOB -> /tmp/$BASE"
docker cp "$JOB" "$CID:/tmp/$BASE"

PKG_ARG=""
if [ -n "$PKGS" ]; then
  PKG_ARG="--packages $PKGS"
  echo ">> --packages $PKGS"
fi

echo ">> submetendo..."
docker exec -u 0 "$CID" sh -c "export HOME=/root && cd /opt/bitnami/spark && \
  bin/spark-submit --conf spark.jars.ivy=/root/.ivy2 $PKG_ARG \
  --master spark://spark-master:7077 /tmp/$BASE"
