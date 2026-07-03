# =============================================================================
# Airflow 3.0 - FAB auth manager: SSO OIDC via Keycloak (realm 'datastack')
# =============================================================================
# Lido pelo apache-airflow-providers-fab (FabAuthManager). O caminho e definido
# por [fab] config_file = /opt/airflow/webserver_config.py (default da imagem
# 3.0.6, confirmado com `airflow config list`); a stack monta ESTE arquivo la.
#
# Mesmo mecanismo do Superset (Flask-AppBuilder AUTH_OAUTH) e do JupyterHub/Trino:
# cada servico faz seu proprio fluxo OIDC, sem passar pelo oauth2-proxy central.
# Requer Authlib (ja presente na imagem base 3.0.6; reforcado no Dockerfile).
#
# Hairpin (igual Superset/Trino/JupyterHub): o container do Airflow NAO alcanca o
# EIP, so o servico Swarm 'keycloak'. Entao:
#   - authorize_url (browser / leg de login)  -> URL EXTERNA https://keycloak...sslip.io
#   - access_token_url / api_base_url(userinfo) / jwks_uri (back-channel, chamado
#     pelo proprio container)                   -> URL INTERNA http://keycloak:8080
# UI: https://airflow.<eip>.sslip.io
# =============================================================================

import os

from flask_appbuilder.const import AUTH_OAUTH
from airflow.providers.fab.auth_manager.security_manager.override import (
    FabAirflowSecurityManagerOverride,
)

# Habilita o autoregistro de usuarios via OAuth do FAB
AUTH_TYPE = AUTH_OAUTH

_KEYCLOAK_REALM_EXT = "https://keycloak.<eip>.sslip.io/realms/datastack"
_KEYCLOAK_REALM_INT = "http://keycloak:8080/realms/datastack"

OAUTH_PROVIDERS = [
    {
        "name": "keycloak",
        "icon": "fa-key",
        "token_key": "access_token",
        "remote_app": {
            "client_id": "airflow",
            "client_secret": os.environ.get(
                "AIRFLOW_OIDC_CLIENT_SECRET", "airflow-oidc-secret-CHANGE-ME"
            ),
            "client_kwargs": {"scope": "openid email profile"},
            # api_base_url/access_token_url/jwks_uri = back-channel (INTERNO).
            # Authlib concatena api_base_url + "userinfo" p/ buscar o perfil
            # (chamado em get_oauth_user_info abaixo).
            "api_base_url": f"{_KEYCLOAK_REALM_INT}/protocol/openid-connect/",
            "access_token_url": f"{_KEYCLOAK_REALM_INT}/protocol/openid-connect/token",
            "authorize_url": f"{_KEYCLOAK_REALM_EXT}/protocol/openid-connect/auth",
            "jwks_uri": f"{_KEYCLOAK_REALM_INT}/protocol/openid-connect/certs",
        },
    }
]

# Login via SSO cria o usuario no 1o acesso. Papel default = Viewer (leitura).
# Realm role 'airflow_admin' (Keycloak) -> role Admin do Airflow, via
# AUTH_ROLES_MAPPING + AUTH_ROLES_SYNC_AT_LOGIN (reavaliado a cada login).
AUTH_USER_REGISTRATION = True
AUTH_USER_REGISTRATION_ROLE = "Viewer"
AUTH_ROLES_SYNC_AT_LOGIN = True
AUTH_ROLES_MAPPING = {
    "airflow_admin": ["Admin"],
}


class KeycloakSecurityManager(FabAirflowSecurityManagerOverride):
    """FAB nao tem provider nativo p/ Keycloak (so google/github/... tem parser
    pronto). Sobrescrevemos get_oauth_user_info (a base define
    self.oauth_user_info = self.get_oauth_user_info) buscando /userinfo pelo
    back-channel interno e mapeando os campos + role_keys (realm_access.roles,
    exposto via protocol mapper 'realm-roles' no client 'airflow' do realm)
    para o AUTH_ROLES_MAPPING acima."""

    def get_oauth_user_info(self, provider, response=None):
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


# O FabAuthManager le esta chave (fab_auth_manager.security_manager); a classe
# TEM que estender FabAirflowSecurityManagerOverride (issubclass e checado).
SECURITY_MANAGER_CLASS = KeycloakSecurityManager
