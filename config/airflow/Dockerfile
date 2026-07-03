# Build (no node-1, imagem local sem registry):
#   docker build -t datastack/airflow:3.0-oidc config/airflow/
# Mesmo padrao das outras imagens custom do stack (datastack/superset:3.1.3-oidc,
# datastack/jupyterhub:4.1-oidc): fica pinada no node-1 nos servicos do
# stacks/12-airflow.yml, porque nao ha registry e node-2/node-3 nao tem egress.
#
# Base Airflow 3.0.6 (ultimo patch 3.0.x em 2026-07; decisao do usuario: NAO usar
# 3.1+). Verificado dentro da imagem oficial apache/airflow:3.0.6 que ela JA traz:
#   - Authlib 1.6.2            (obrigatorio p/ AUTH_OAUTH do FAB auth manager)
#   - apache-airflow-providers-fab 2.4.1  (o auth manager que le webserver_config.py)
#   - docker CLI 28.3.3 em /usr/bin/docker  (scheduler roda `docker exec` no
#     spark-master via /var/run/docker.sock)
# Ainda assim reinstalamos authlib + provider FAB PRESOS ao constraints file
# oficial da versao: (a) deixa a imagem auto-documentada/idempotente, (b) protege
# contra um rebuild futuro da base que remova/altere esses pacotes, (c) o
# constraints garante que nenhuma dependencia transitiva quebre (regra do task:
# "respeite os constraints do Airflow ao instalar pips").
FROM apache/airflow:3.0.6

# Armadilha de volume-shadowing (mesma do Superset/Authlib): o pip do usuario
# 'airflow' instala em /home/airflow/.local/lib/python3.12/site-packages. O
# stack NAO monta volume sobre /home/airflow nem sobre esse path (os binds do
# 12-airflow.yml sao /opt/airflow/dags, /opt/airflow/webserver_config.py,
# /var/run/docker.sock, /opt/datastack/jobs, /opt/shared) -> sem shadowing.
# Nao usamos USER root: o pip --user do proprio usuario airflow ja escreve no
# path correto (.local), que e onde o FAB provider da base tambem vive.
ARG AIRFLOW_VERSION=3.0.6
ARG PYTHON_VERSION=3.12
ARG CONSTRAINTS_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

RUN pip install --no-cache-dir --constraint "${CONSTRAINTS_URL}" \
        "apache-airflow-providers-fab" \
        "Authlib"

# Verificacao em build-time: falha o build se qualquer requisito nao estiver
# importavel/presente (auth manager, security manager override, authlib, docker CLI).
RUN python -c "from airflow.providers.fab.auth_manager.fab_auth_manager import FabAuthManager" \
 && python -c "from airflow.providers.fab.auth_manager.security_manager.override import FabAirflowSecurityManagerOverride" \
 && python -c "from authlib.integrations.flask_client import OAuth" \
 && docker --version
