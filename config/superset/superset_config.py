# =============================================================================
# Apache Superset Configuration
# IMPORTANTE: Altere SECRET_KEY e senhas antes do deploy em producao
# =============================================================================

import os

from flask_appbuilder.security.manager import AUTH_OAUTH
from superset.security import SupersetSecurityManager

# Seguranca - ALTERE ESTE VALOR!
SECRET_KEY = os.environ.get("SUPERSET_SECRET_KEY", "CHANGE_ME_super_secret_key_32chars_minimum")

# Banco de dados do Superset (PostgreSQL no node-1)
SQLALCHEMY_DATABASE_URI = os.environ.get(
    "SUPERSET_DATABASE_URI",
    "postgresql+psycopg2://superset:superset_password_CHANGE_ME@postgres:5432/superset"
)

# Redis para cache e Celery
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
    "CACHE_KEY_PREFIX": "superset_",
    "CACHE_REDIS_HOST": REDIS_HOST,
    "CACHE_REDIS_PORT": REDIS_PORT,
    "CACHE_REDIS_DB": 0,
}

DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 3600,
    "CACHE_KEY_PREFIX": "superset_results_",
    "CACHE_REDIS_HOST": REDIS_HOST,
    "CACHE_REDIS_PORT": REDIS_PORT,
    "CACHE_REDIS_DB": 1,
}

# Celery para queries async
class CeleryConfig:
    broker_url = f"redis://{REDIS_HOST}:{REDIS_PORT}/1"
    imports = ("superset.sql_lab",)
    result_backend = f"redis://{REDIS_HOST}:{REDIS_PORT}/2"
    worker_prefetch_multiplier = 10
    task_acks_late = True
    beat_schedule = {
        "reports.scheduler": {
            "task": "reports.scheduler",
            "schedule": 1,
        },
        "reports.prune_log": {
            "task": "reports.prune_log",
            "schedule": 600,
        },
    }

CELERY_CONFIG = CeleryConfig

# Configuracoes de timeout para queries longas (Trino/Spark)
SQLLAB_TIMEOUT = 300
SQLLAB_ASYNC_TIME_LIMIT_SEC = 900
SUPERSET_WEBSERVER_TIMEOUT = 300

# Feature flags
FEATURE_FLAGS = {
    "ENABLE_TEMPLATE_PROCESSING": True,
    "ENABLE_EXPLORE_DRAG_AND_DROP": True,
    "ENABLE_NATIVE_FILTERS": True,
    "EMBEDDED_SUPERSET": True,
}

# Configuracao do servidor web
ENABLE_PROXY_FIX = True
ROW_LIMIT = 5000
VIZ_ROW_LIMIT = 10000

# Log level
FLASK_ENV = os.environ.get("SUPERSET_ENV", "production")

# =============================================================================
# SSO - OIDC via Keycloak (realm 'datastack'), AUTH_OAUTH nativo do
# Flask-AppBuilder (mesmo mecanismo do JupyterHub/GenericOAuthenticator e do
# Trino - cada servico faz seu proprio OIDC, sem passar por oauth2-proxy).
# Requer Authlib no classpath Python: nao vem na imagem apache/superset
# oficial, por isso a stack usa a imagem local datastack/superset:3.1.3-oidc
# (ver config/superset/Dockerfile). UI: https://superset.<eip>.sslip.io
#
# Hairpin (igual Trino/JupyterHub): o container do Superset nao alcanca o EIP,
# so o servico Swarm 'keycloak' -> o login (browser, authorize_url) usa a URL
# EXTERNA; o back-channel (token/userinfo, chamado pelo proprio container)
# usa http://keycloak:8080 INTERNO.
# =============================================================================
AUTH_TYPE = AUTH_OAUTH

_KEYCLOAK_REALM_EXT = "https://keycloak.<eip>.sslip.io/realms/datastack"
_KEYCLOAK_REALM_INT = "http://keycloak:8080/realms/datastack"

OAUTH_PROVIDERS = [
    {
        "name": "keycloak",
        "icon": "fa-key",
        "token_key": "access_token",
        "remote_app": {
            "client_id": "superset",
            "client_secret": os.environ.get(
                "SUPERSET_OIDC_CLIENT_SECRET", "superset-oidc-secret-CHANGE-ME"
            ),
            "client_kwargs": {"scope": "openid email profile"},
            # api_base_url/access_token_url = back-channel (interno); Authlib
            # concatena api_base_url + "userinfo" para buscar o perfil do
            # usuario (chamado em KeycloakSecurityManager.oauth_user_info).
            "api_base_url": f"{_KEYCLOAK_REALM_INT}/protocol/openid-connect/",
            "access_token_url": f"{_KEYCLOAK_REALM_INT}/protocol/openid-connect/token",
            "authorize_url": f"{_KEYCLOAK_REALM_EXT}/protocol/openid-connect/auth",
            "jwks_uri": f"{_KEYCLOAK_REALM_INT}/protocol/openid-connect/certs",
        },
    }
]

# Login via SSO cria o usuario automaticamente no 1o acesso (Gamma = leitura/
# exploracao basica). superset_admin (Keycloak) -> Admin, via AUTH_ROLES_MAPPING.
AUTH_USER_REGISTRATION = True
AUTH_USER_REGISTRATION_ROLE = "Gamma"
AUTH_ROLES_SYNC_AT_LOGIN = True
AUTH_ROLES_MAPPING = {
    "superset_admin": ["Admin"],
}


class KeycloakSecurityManager(SupersetSecurityManager):
    """FAB nao tem suporte nativo a Keycloak como provider - so providers
    conhecidos (google/github/...) tem oauth_user_info() pronto. Aqui
    buscamos o /userinfo (via api_base_url + 'userinfo', back-channel
    interno) e mapeamos os campos + role_keys (realm_access.roles, exposto
    via protocol mapper 'realm-roles' no client 'superset' do realm) para o
    AUTH_ROLES_MAPPING acima."""

    def oauth_user_info(self, provider, response=None):
        if provider == "keycloak":
            me = self.appbuilder.sm.oauth_remotes[provider].get("userinfo").json()
            return {
                "username": me["preferred_username"],
                "email": me.get("email"),
                "first_name": me.get("given_name", ""),
                "last_name": me.get("family_name", ""),
                "role_keys": me.get("realm_access", {}).get("roles", []),
            }
        return {}


CUSTOM_SECURITY_MANAGER = KeycloakSecurityManager
