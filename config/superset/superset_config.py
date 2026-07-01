# =============================================================================
# Apache Superset Configuration
# IMPORTANTE: Altere SECRET_KEY e senhas antes do deploy em producao
# =============================================================================

import os

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
