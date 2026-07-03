# =============================================================================
# JupyterHub Configuration
# Multi-user Jupyter Notebook server com SSO (Keycloak realm 'datastack')
# UI publica: https://jupyter.<eip>.sslip.io  (via Traefik/443)
# =============================================================================

import os

# ---------------------------------------------------------------------------
# Basico
# ---------------------------------------------------------------------------
c.JupyterHub.bind_url = "http://:8000"
c.JupyterHub.log_level = "INFO"

# DockerSpawner spawna cada notebook como um container standalone na overlay
# 'datastack-net' (attachable). Por isso o Hub precisa anunciar um endereco
# que esses containers alcancem: o proprio nome de servico Swarm 'jupyterhub'.
c.JupyterHub.hub_ip = "0.0.0.0"
c.JupyterHub.hub_connect_ip = "jupyterhub"

# ---------------------------------------------------------------------------
# Autenticacao: OIDC via Keycloak (GenericOAuthenticator)
# Hairpin (igual Trino/oauth2-proxy): o browser usa a URL EXTERNA (authorize);
# o back-channel (token/userinfo) usa http://keycloak:8080 INTERNO -> o
# container do Hub nao alcanca o EIP, mas alcanca o servico keycloak.
# ---------------------------------------------------------------------------
from oauthenticator.generic import GenericOAuthenticator

c.JupyterHub.authenticator_class = GenericOAuthenticator
# Vai direto pro Keycloak (pula a pagina com botao "Sign in with Keycloak")
c.Authenticator.auto_login = True

_REALM_EXT = "https://keycloak.<eip>.sslip.io/realms/datastack"
_REALM_INT = "http://keycloak:8080/realms/datastack"

c.GenericOAuthenticator.client_id = "jupyterhub"
c.GenericOAuthenticator.client_secret = "jupyterhub-oidc-secret-CHANGE-ME"
c.GenericOAuthenticator.oauth_callback_url = (
    "https://jupyter.<eip>.sslip.io/hub/oauth_callback"
)
c.GenericOAuthenticator.authorize_url = _REALM_EXT + "/protocol/openid-connect/auth"
c.GenericOAuthenticator.token_url = _REALM_INT + "/protocol/openid-connect/token"
c.GenericOAuthenticator.userdata_url = _REALM_INT + "/protocol/openid-connect/userinfo"
c.GenericOAuthenticator.logout_redirect_url = (
    _REALM_EXT + "/protocol/openid-connect/logout"
)
c.GenericOAuthenticator.login_service = "Keycloak"
c.GenericOAuthenticator.username_claim = "preferred_username"
c.GenericOAuthenticator.scope = ["openid", "email", "profile"]

# Qualquer usuario do realm entra; estes viram admin do Hub.
c.GenericOAuthenticator.allow_all = True
c.GenericOAuthenticator.admin_users = {"admin", "superadmin"}

# ---------------------------------------------------------------------------
# Spawner: um container por usuario (imagem com PySpark)
# ---------------------------------------------------------------------------
c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"
c.DockerSpawner.image = "jupyter/pyspark-notebook:spark-3.5.0"
# Baixa a imagem UMA vez (se nao existir localmente) e reusa nos proximos
# logins; nunca rebaixa uma imagem ja presente no node-1.
c.DockerSpawner.pull_policy = "ifnotpresent"
c.DockerSpawner.remove = True
c.DockerSpawner.debug = True

# Rede: usar nomes de servico Swarm (nao IP/hostname de VM)
c.DockerSpawner.network_name = "datastack-net"
c.DockerSpawner.use_internal_ip = True

c.DockerSpawner.environment = {
    # HA via ZooKeeper (3 masters, um lider por vez) - o servico antigo
    # "spark-master" nao existe mais; listar os TRES aqui (Spark Standalone
    # HA faz failover no cliente). Nota: containers de notebook ja abertos
    # nao pegam essa mudanca - so `docker service update --force
    # apps_jupyterhub` + um novo spawn.
    "SPARK_MASTER": "spark://spark-master-1:7077,spark-master-2:7077,spark-master-3:7077",
    "SPARK_HOME": "/usr/local/spark",
    "HADOOP_CONF_DIR": "/opt/hadoop/conf",
    "AWS_ENDPOINT_URL": "http://seaweedfs-filer-1:8333",
}

# Volume persistente por usuario + pasta compartilhada de CODIGO (nao dados)
# entre Jupyter (/home/jovyan/shared), spark-master (/opt/shared) e Airflow
# (/opt/shared): mesmo /data/shared do host (sticky 1777, uids diferentes
# escrevem). DADOS sempre via s3a:// - executores em node-2/node-3 nao veem
# o disco do node-1. Bind mount so pega em NOVOS spawns; um container de
# notebook ja aberto antes desta mudanca nao ganha o mount (precisa parar e
# reabrir o servidor pelo Hub).
c.DockerSpawner.volumes = {
    "jupyterhub-user-{username}": "/home/jovyan/work",
    "/data/shared": "/home/jovyan/shared",
}

c.DockerSpawner.cpu_limit = 8
c.DockerSpawner.mem_limit = "32G"

# Timeouts (primeiro spawn pode puxar imagem)
c.Spawner.http_timeout = 180
c.Spawner.start_timeout = 300
