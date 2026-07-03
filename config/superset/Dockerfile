# Build: docker build -t datastack/superset:3.1.3-oidc config/superset/
# (imagem local, sem registry - por isso superset/superset-worker ficam
# pinados no node-1, mesmo padrao de config/jupyterhub/Dockerfile.)
FROM apache/superset:3.1.3
# Authlib: dependencia obrigatoria do Flask-AppBuilder para AUTH_OAUTH (SSO via
# Keycloak) - nao vem na imagem oficial.
# cryptography fixado em <43 - apache-superset 3.1.3 exige <43.0.0,>=42.0.4;
# sem o pin o Authlib puxa a ultima (49.x) e quebra essa constraint.
# USER root: a imagem roda como usuario "superset" (nao-root), sem permissao
# de escrita no site-packages do sistema - sem isto o pip cai silenciosamente
# no modo --user, instalando em /app/superset_home/.local/... . Esse caminho
# fica DEBAIXO do volume superset-data (stacks/08-apps-stack.yml monta
# superset-data:/app/superset_home) - em runtime o volume esconde o que foi
# instalado na imagem, e o Superset sobe sem authlib (ModuleNotFoundError).
USER root
RUN pip install --no-cache-dir "cryptography>=42.0.4,<43.0.0" Authlib
USER superset
