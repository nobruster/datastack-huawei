#!/bin/bash
set -euo pipefail
# ============================================================
# render-to-opt.sh - renderiza o repo para /opt/datastack (node-1)
#
# POR QUE ISTO EXISTE
# --------------------
# github.com/nobruster/datastack-huawei e um repo PUBLICO. Desde 2026-07-04
# o historico inteiro foi reescrito: todo IP real (EIP, IP privado de cada
# no, subnet privada) foi trocado por placeholders literais -
# <eip>, <ip-node-1>, <ip-node-2>, <ip-node-3>, <subnet-privada> - em
# stacks/, config/, scripts/, README.md e CLAUDE.md. A copia deployada em
# /opt/datastack no node-1 precisa dos valores REAIS para o cluster
# funcionar (bind mounts do Traefik/Trino/Keycloak/etc, /etc/hosts,
# docker swarm init --advertise-addr...). Um `cp -r` puro do repo para
# /opt/datastack agora instalaria a STRING "<eip>" num arquivo de config
# ativo e quebraria o servico.
#
# Este script substitui aquele `cp` manual. Ele:
#   1. Le os 5 valores reais de /opt/datastack/.site.env (git-ignored,
#      chmod 600, existe SOMENTE no node-1 - nunca no repo).
#   2. Copia a arvore versionada do repo (stacks/, config/, scripts/,
#      dags/, jobs/, notebooks/, README.md, CLAUDE.md) para /opt/datastack,
#      SEM apagar o que ja esta la e nao vem do repo (certificados TLS nao
#      versionados em config/traefik/tls|config/seaweedfs/tls,
#      __pycache__, etc) - nunca usa rsync --delete.
#   3. Substitui os 5 placeholders pelos valores reais, so em arquivos de
#      texto que de fato contem algum placeholder (grep -I ja pula
#      binarios sozinho - .jar/.crt/.key nunca sao tocados).
#
# A substituicao SO roda sobre a copia de destino (staging ou
# /opt/datastack) - nunca sobre $REPO_DIR. O repo em si nunca e escrito por
# este script; se algum dia um placeholder aparecer substituido dentro do
# checkout do repo, isso e um bug deste script, nao o comportamento
# esperado - trate como incidente de seguranca (repo publico).
#
# ARQUIVOS PROTEGIDOS - NUNCA sobrescritos por este script
# ----------------------------------------------------------
# config/trino/coordinator/config.properties
# config/trino/worker/config.properties
#
# Essas duas sao versionadas no repo e contem segredo placeholder
# (*-CHANGE-ME), mas o client-secret OAuth2 e o
# internal-communication.shared-secret reais foram setados a mao
# diretamente no node-1 e NUNCA foram commitados (ver CLAUDE.md,
# secao "Placeholder secrets"/SSO). Se este script copiasse a versao do
# repo por cima, o Trino perderia o client-secret real (login OAuth2
# quebra na hora) e teria que reaplicar o shared-secret real em
# coordinator+worker manualmente para religar a comunicacao interna.
# Se voce adicionar uma propriedade NOVA a esses arquivos no repo, aplique
# a mudanca a mao no node-1, preservando as linhas de segredo real.
#
# Uso:
#   scripts/render-to-opt.sh            # renderiza e aplica em /opt/datastack
#   scripts/render-to-opt.sh --check    # renderiza num dir temporario e
#                                        # mostra o diff contra /opt/datastack,
#                                        # sem alterar nada
#
# Variavel de ambiente DATASTACK_TARGET sobrescreve /opt/datastack (usada
# para testar o script num diretorio de mentira, sem tocar no cluster).
# ============================================================

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
REPO_DIR="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
TARGET_DIR="${DATASTACK_TARGET:-/opt/datastack}"
SITE_ENV="${TARGET_DIR}/.site.env"

CHECK_MODE=0
if [[ "${1:-}" == "--check" ]]; then
    CHECK_MODE=1
fi

# Itens versionados que sao copiados do repo para o alvo.
ITEMS=(stacks config scripts dags jobs notebooks README.md CLAUDE.md)

# Arquivos que existem no repo mas jamais devem ser sobrescritos (ver
# comentario de cabecalho acima) - caminhos relativos a raiz do repo.
PROTECTED_FILES=(
    config/trino/coordinator/config.properties
    config/trino/worker/config.properties
)

# --- 1. valores reais ---------------------------------------------------
if [[ ! -f "$SITE_ENV" ]]; then
    echo "ERRO: $SITE_ENV nao existe." >&2
    echo "Crie-o com EIP=, IP_NODE_1=, IP_NODE_2=, IP_NODE_3=, SUBNET_PRIVADA= (chmod 600). Ver CLAUDE.md." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$SITE_ENV"
set +a

REQUIRED_VARS=(EIP IP_NODE_1 IP_NODE_2 IP_NODE_3 SUBNET_PRIVADA)
missing=()
for v in "${REQUIRED_VARS[@]}"; do
    if [[ -z "${!v:-}" ]]; then
        missing+=("$v")
    fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERRO: variaveis faltando em $SITE_ENV: ${missing[*]}" >&2
    exit 1
fi

# --- 2. dir de destino real ou staging (--check) ------------------------
STAGE_DIR="$(mktemp -d /tmp/datastack-render.XXXXXX)"
if [[ -z "$STAGE_DIR" || ! -d "$STAGE_DIR" ]]; then
    echo "ERRO: mktemp -d falhou (STAGE_DIR='$STAGE_DIR')." >&2
    exit 1
fi
cleanup() { rm -rf "$STAGE_DIR"; }
trap cleanup EXIT

if [[ $CHECK_MODE -eq 1 ]]; then
    DEST_DIR="$STAGE_DIR"
    mkdir -p "$DEST_DIR"
    if [[ -d "$TARGET_DIR" ]]; then
        # Semeia o staging com o que ja esta deployado, assim arquivos que
        # o render nao toca (protegidos, TLS nao versionado, __pycache__,
        # sobras antigas nao versionadas) comparam identico e nao viram
        # ruido no diff final.
        rsync -a --exclude '.git' --exclude '.site.env' "$TARGET_DIR"/ "$DEST_DIR"/
    fi
else
    DEST_DIR="$TARGET_DIR"
    mkdir -p "$DEST_DIR"
fi

# Cinto de seguranca: a substituicao de placeholders so pode rodar dentro
# de $DEST_DIR. Se por algum motivo DEST_DIR ficar vazio/errado (ex.:
# mktemp falhou em silencio), $DEST_DIR/$item viraria um caminho tipo
# "/scripts" e o script abortaria aqui em vez de escrever em lugar errado.
case "$DEST_DIR" in
    /tmp/datastack-render.*|"$TARGET_DIR")
        ;;
    *)
        echo "ERRO: DEST_DIR inesperado ('$DEST_DIR'). Abortando por seguranca." >&2
        exit 1
        ;;
esac
case "$REPO_DIR" in
    /*[!/]) ;; # precisa ser um caminho absoluto normal (nao "/")
    *)
        echo "ERRO: REPO_DIR resolveu para '$REPO_DIR', que parece invalido. Abortando." >&2
        exit 1
        ;;
esac
if [[ "$REPO_DIR" == "/" ]]; then
    echo "ERRO: REPO_DIR resolveu para '/'. Abortando por seguranca." >&2
    exit 1
fi
# REPO_DIR precisa ser de fato o checkout do repo (nao uma copia solta do
# script rodando de outro lugar, ex.: uma copia em /tmp durante debug, ou
# a propria copia deployada em /opt/datastack apos um render anterior).
# Exigir marcadores conhecidos do repo evita repetir esse incidente: apos
# copiar/mover so este script para fora do checkout, ${BASH_SOURCE[0]}/..
# pode resolver para um diretorio que nao e o repo (ex.: "/"), e um render
# rodando dali acabaria lendo/gravando em lugares inesperados.
if [[ ! -d "$REPO_DIR/stacks" || ! -f "$REPO_DIR/CLAUDE.md" ]]; then
    echo "ERRO: REPO_DIR ('$REPO_DIR') nao parece ser o checkout do repo" >&2
    echo "(faltam stacks/ e/ou CLAUDE.md). Rode este script pelo caminho real" >&2
    echo "dentro do repo (nao uma copia em /tmp ou similar)." >&2
    exit 1
fi
# Nunca renderizar o repo em cima de si mesmo (ex.: alguem rodando a copia
# ja deployada em /opt/datastack/scripts/render-to-opt.sh, onde
# REPO_DIR e TARGET_DIR default coincidiriam em /opt/datastack).
if [[ "$REPO_DIR" == "$TARGET_DIR" ]]; then
    echo "ERRO: REPO_DIR e TARGET_DIR sao o mesmo diretorio ('$REPO_DIR')." >&2
    echo "Rode este script a partir do checkout do repo (nao da copia deployada)." >&2
    exit 1
fi

# --- 3. copia a arvore versionada, preservando o resto ------------------
echo "== REPO_DIR=$REPO_DIR =="
echo "== Copiando arvore versionada do repo para $( [[ $CHECK_MODE -eq 1 ]] && echo "staging ($STAGE_DIR)" || echo "$TARGET_DIR" ) =="
COPIED_ROOTS=()
for item in "${ITEMS[@]}"; do
    src="$REPO_DIR/$item"
    [[ -e "$src" ]] || continue

    if [[ -d "$src" ]]; then
        mkdir -p "$DEST_DIR/$item"
        exclude_args=(--exclude '__pycache__')
        for pf in "${PROTECTED_FILES[@]}"; do
            if [[ "$pf" == "$item/"* ]]; then
                exclude_args+=(--exclude "${pf#"$item"/}")
            fi
        done
        # sem --delete: nunca apaga o que ja existe no alvo e nao vem do repo
        rsync -a "${exclude_args[@]}" "$src"/ "$DEST_DIR/$item"/
    else
        cp -a "$src" "$DEST_DIR/$item"
    fi
    COPIED_ROOTS+=("$DEST_DIR/$item")
done

echo "== Arquivos protegidos (segredo real so no node, nunca renderizados): =="
for pf in "${PROTECTED_FILES[@]}"; do
    echo "  - $pf"
done

# --- 4. substitui os placeholders, so em arquivos de texto que os contem --
echo "== Substituindo placeholders pelos valores reais (escopo: ${#COPIED_ROOTS[@]} diretorio(s)/arquivo(s) sob $DEST_DIR) =="
TARGET_FILES=()
if [[ ${#COPIED_ROOTS[@]} -gt 0 ]]; then
    mapfile -t TARGET_FILES < <(
        grep -rIl \
            -e '<eip>' -e '<ip-node-1>' -e '<ip-node-2>' -e '<ip-node-3>' -e '<subnet-privada>' \
            "${COPIED_ROOTS[@]}" 2>/dev/null || true
    )
fi
for f in "${TARGET_FILES[@]}"; do
    # cinto de seguranca extra: nunca aplicar fora de $DEST_DIR
    case "$f" in
        "$DEST_DIR"/*) ;;
        *)
            echo "ERRO: arquivo fora do escopo esperado: $f. Abortando." >&2
            exit 1
            ;;
    esac
    sed -i \
        -e "s|<eip>|${EIP}|g" \
        -e "s|<ip-node-1>|${IP_NODE_1}|g" \
        -e "s|<ip-node-2>|${IP_NODE_2}|g" \
        -e "s|<ip-node-3>|${IP_NODE_3}|g" \
        -e "s|<subnet-privada>|${SUBNET_PRIVADA}|g" \
        "$f"
done
echo "   ${#TARGET_FILES[@]} arquivo(s) com placeholder substituido."

# --- 5. --check: so mostra o diff, nao aplica nada -----------------------
if [[ $CHECK_MODE -eq 1 ]]; then
    echo
    echo "== --check: diff entre o render (staging) e $TARGET_DIR =="
    echo "(nada foi alterado em $TARGET_DIR - staging e temporario)"
    echo
    diff -rq --exclude='.git' --exclude='.site.env' "$STAGE_DIR" "$TARGET_DIR" || true
    exit 0
fi

echo
echo "== Render aplicado em $TARGET_DIR =="
echo "Lembrete: se config/ mudou, rode scripts/07-sync-config.sh para propagar"
echo "para node-2/node-3, e 'docker service update --force <servico>' para"
echo "servicos que nao fazem hot-reload de config bind-mounted."
