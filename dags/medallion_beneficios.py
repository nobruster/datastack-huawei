"""
DAG medallion_beneficios - pipeline PDA Beneficios Emitidos (landing -> bronze
-> prata -> ouro), orquestrando os jobs Spark ja validados manualmente (ver
CLAUDE.md, secao "Delta Lake jobs" / "landing-beneficios-v3.py").

Como cada task roda:
  Nao ha `docker cp` aqui (diferente de scripts/run-spark-job.sh, pensado p/ uso
  manual do terminal): o spark-master monta /opt/datastack/jobs (ro) desde a
  integracao da pasta compartilhada (stacks/06-datastack.yml), entao o
  spark-submit referencia o .py DIRETO nesse path dentro do container. O
  scheduler roda como root com acesso a /var/run/docker.sock (mesmo mecanismo
  usado para validar `docker exec` no spark-master antes deste DAG existir) e
  resolve o container do spark-master via `docker ps` (nome de servico Swarm =
  sufixo aleatorio, nunca um nome fixo) - ver _spark_submit_cmd() abaixo, que
  fatora o padrao usado pelas 4 tasks.

Schedule: `schedule=None` (disparo manual) por decisao consciente enquanto o
pipeline esta em validacao - a task `landing` baixa um ZIP de ~11GB da fonte
externa (Portal de Dados Abertos), entao rodar isso num cron sem necessidade
desperdicaria egress/tempo. Para promover a producao com uma cadencia (ex.:
diaria as 3h): trocar `schedule=None` por `schedule="0 3 * * *"` (ou um
`Dataset`/`Asset` schedule, se o pipeline passar a reagir a um novo arquivo na
fonte) - nada mais no DAG precisa mudar.

Camadas bronze/prata/ouro sao Delta Lake com escrita em `overwrite`
(bronze/silver/gold jobs) e portanto SEGURAS de re-rodar isoladamente (Delta
`replaceWhere`/overwrite marca arquivos antigos como removidos no transaction
log, sem delete-storm no SeaweedFS - ver CLAUDE.md, "Delta overwrite e safe to
re-run"). A task `landing` (Parquet cru, sem Delta) NAO tem essa garantia e
re-baixa o ZIP inteiro - evitar re-rodar sem necessidade.
"""

from __future__ import annotations

import pendulum

from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator

JOBS_DIR = "/opt/datastack/jobs"
DELTA_PACKAGES = "io.delta:delta-spark_2.12:3.2.0"


def _spark_submit_cmd(job_file: str, packages: str | None = None) -> str:
    """Monta o bash_command de uma task, replicando o padrao comprovado de
    scripts/run-spark-job.sh - SEM o `docker cp` (o job ja esta disponivel
    dentro do spark-master via bind mount ro de /opt/datastack/jobs).

    Tres armadilhas replicadas do wrapper original (ver CLAUDE.md):
      1. spark-master e um servico Swarm -> o nome do container tem sufixo
         aleatorio; resolve-se sempre via `docker ps --filter name=...`.
      2. Submeter como uid 0 (`docker exec -u 0`): fora do entrypoint bitnami,
         uid 1001 nao tem entrada em /etc/passwd e o login UGI do Hadoop
         quebra ("invalid null input: name").
      3. HOME/ivy graváveis (export HOME=/root + spark.jars.ivy=/root/.ivy2).
    """
    pkg_arg = f"--packages {packages} " if packages else ""
    return f"""\
set -euo pipefail
CID=$(docker ps --filter name=datastack_spark-master -q | head -1)
if [ -z "$CID" ]; then
  echo "ERRO: container do spark-master nao encontrado (docker ps --filter name=datastack_spark-master)" >&2
  exit 1
fi
echo ">> spark-master container: $CID"
docker exec -u 0 "$CID" sh -c "export HOME=/root && cd /opt/bitnami/spark && \\
  bin/spark-submit --conf spark.jars.ivy=/root/.ivy2 {pkg_arg}\\
  --master spark://spark-master-1:7077,spark-master-2:7077,spark-master-3:7077 {JOBS_DIR}/{job_file}"
"""


with DAG(
    dag_id="medallion_beneficios",
    description="Pipeline medallion PDA Beneficios Emitidos: landing -> bronze -> prata -> ouro (Spark/Delta em spark-master via docker exec)",
    schedule=None,  # disparo manual por enquanto - ver docstring do modulo p/ como promover a cron
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 1,
        "retry_delay": pendulum.duration(minutes=5),
    },
    tags=["spark", "delta", "medallion", "beneficios", "pda"],
) as dag:

    landing = BashOperator(
        task_id="landing",
        bash_command=_spark_submit_cmd("landing-beneficios-v3.py"),
    )

    bronze = BashOperator(
        task_id="bronze",
        bash_command=_spark_submit_cmd("bronze-beneficios-v2.py", DELTA_PACKAGES),
    )

    prata = BashOperator(
        task_id="prata",
        bash_command=_spark_submit_cmd("silver-beneficios-v2.py", DELTA_PACKAGES),
    )

    ouro = BashOperator(
        task_id="ouro",
        bash_command=_spark_submit_cmd("gold-beneficios-v2.py", DELTA_PACKAGES),
    )

    landing >> bronze >> prata >> ouro
