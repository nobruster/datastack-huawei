# =============================================================================
# JupyterHub Configuration
# Multi-user Jupyter Notebook server
# UI: http://<ip-node-1>:8000
# =============================================================================

import os
import sys

# Configuracao basica
c.JupyterHub.ip = "0.0.0.0"
c.JupyterHub.port = 8000
c.JupyterHub.bind_url = "http://:8000"

# Spawner - usa Docker para criar notebooks por usuario
c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"

# Imagem do notebook com PySpark pre-instalado
c.DockerSpawner.image = "jupyter/pyspark-notebook:spark-3.5.0"
c.DockerSpawner.remove = True
c.DockerSpawner.debug = True

# Rede Docker
c.DockerSpawner.network_name = "datastack_net"
c.DockerSpawner.use_internal_ip = True

# Variaveis de ambiente para Spark
c.DockerSpawner.environment = {
    "SPARK_MASTER": "spark://<ip-node-1>:7077",
    "SPARK_HOME": "/usr/local/spark",
    "HADOOP_CONF_DIR": "/opt/hadoop/conf",
    "AWS_ACCESS_KEY_ID": "",
    "AWS_SECRET_ACCESS_KEY": "",
    "AWS_ENDPOINT_URL": "http://<ip-node-1>:8333",
}

# Volumes persistentes por usuario
c.DockerSpawner.volumes = {
    "jupyterhub-user-{username}": "/home/jovyan/work",
}

# Recursos
c.DockerSpawner.cpu_limit = 8
c.DockerSpawner.mem_limit = "32G"

# Autenticacao - PAM (usuario do sistema)
# Para producao, considere usar OAuth/LDAP
c.JupyterHub.authenticator_class = "nativeauthenticator.NativeAuthenticator"
c.NativeAuthenticator.open_signup = False

# Admin users (altere conforme necessario)
c.Authenticator.admin_users = {"admin"}
c.Authenticator.allowed_users = {"admin", "analyst", "engineer"}

# Timeout
c.Spawner.http_timeout = 120
c.Spawner.start_timeout = 120

# Servico de proxy
c.ConfigurableHTTPProxy.should_start = True

# Log
c.JupyterHub.log_level = "INFO"
