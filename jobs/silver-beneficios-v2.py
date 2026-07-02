"""
==================================================================
   CAMADA SILVER - Beneficios Emitidos PDA 2025-2027  [v2]
==================================================================

Fonte   : s3a://bronze/pda/beneficios-emitidos/   (Delta Lake)
Destino : s3a://prata/pda/beneficios-emitidos/    (Delta Lake)

--- 1. ENTENDIMENTO DOS DADOS ---
  - Strings tem trailing spaces -> trim obrigatorio
  - Valor invalido '{n class}' em: sexo, clientela, ramo_atividade
  - 'banco'    : campo composto "104-Caixa Economica" -> codigo + nome
  - 'mun_*'    : campo composto "28043-To-Miracema..." -> codigo + sg_uf + nome
  - 'especie_codigo' : string " 88" -> IntegerType
  - registros com vl_liquido = 0 sao sinalizados (fl_vl_zero), nao descartados

--- 2. TRANSFORMACOES SILVER ---
  - Trim de todas as colunas string
  - '{n class}' / 'Nao Informado' -> NULL
  - Parse banco     -> banco_codigo (Int) + banco_nome (String)
  - Parse mun_pagto -> mun_pagto_codigo, mun_pagto_sg_uf, mun_pagto_nome
  - Parse mun_resid -> mun_resid_codigo, mun_resid_sg_uf, mun_resid_nome
  - Derivar sg_uf (nome do estado -> sigla)
  - especie_codigo  -> Integer
  - Temporal        -> ano_inicio (Int), mes_inicio (Int)
  - Flags           -> fl_vl_zero, fl_mesmo_municipio
  - meio_pagamento  -> meio_pag_codigo + meio_pag_descricao
  - Metadado        -> _silver_ts

Ambiente (ver CLAUDE.md): Delta via --packages io.delta:delta-spark_2.12:3.2.0;
NAO usar spark.sql() (VACUUM/DESCRIBE HISTORY) - o catalogo Hive quebra
(metastore 3.1.3 sem metastore.jars); usar a API DeltaTable.

Como executar (no node-1):
  scripts/run-spark-job.sh jobs/silver-beneficios-v2.py io.delta:delta-spark_2.12:3.2.0
"""

import time
from datetime import datetime, timezone

from delta.tables import DeltaTable
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType

# --- Configuracao centralizada ---
ANO_MES     = "202601"
BRONZE_PATH = "s3a://bronze/pda/beneficios-emitidos/"
SILVER_PATH = "s3a://prata/pda/beneficios-emitidos/"

# Mapeamento completo de estados para siglas UF (chaves = valores UTF-8 reais
# da coluna 'uf' no bronze - conferido na inspecao).
UF_PARA_SIGLA = {
    "Acre": "AC", "Alagoas": "AL", "Amapá": "AP", "Amazonas": "AM",
    "Bahia": "BA", "Ceará": "CE", "Distrito Federal": "DF",
    "Espírito Santo": "ES", "Goiás": "GO", "Maranhão": "MA",
    "Mato Grosso": "MT", "Mato Grosso do Sul": "MS", "Minas Gerais": "MG",
    "Pará": "PA", "Paraíba": "PB", "Paraná": "PR", "Pernambuco": "PE",
    "Piauí": "PI", "Rio de Janeiro": "RJ", "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS", "Rondônia": "RO", "Roraima": "RR",
    "Santa Catarina": "SC", "São Paulo": "SP", "Sergipe": "SE",
    "Tocantins": "TO",
}

# Prefixo textual do meio de pagamento -> (sigla, descricao)
MEIO_PAG_MAP = {
    "Ccf": ("CCF", "Conta-Corrente Fisica"),
    "Ccl": ("CCL", "Conta-Corrente Loterica"),
    "Cmg": ("CMG", "Cartao Magnetico"),
}

# Valores que representam ausencia de informacao no dataset PDA
VALORES_NULOS = ["{ñ class}", "Nao Informado", ""]

print("=" * 65)
print("  SILVER v2 - Beneficios Emitidos PDA 2025-2027")
print("=" * 65)

# --- 1. SparkSession ---
print("\n1. Iniciando SparkSession...")
spark = (
    SparkSession.builder
    .appName(f"Silver-PDA-Beneficios-v2-{ANO_MES}")
    .config("spark.sql.extensions",
            "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config("spark.sql.adaptive.enabled", "true")
    .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

total_cores   = spark.sparkContext.defaultParallelism
shuffle_parts = max(12, total_cores * 6)
spark.conf.set("spark.sql.shuffle.partitions", shuffle_parts)

print(f"   Spark {spark.version}")
print(f"   Cores alocados      : {total_cores}")
print(f"   shuffle.partitions  : {shuffle_parts}")

# --- 3. Leitura da bronze (predicate pushdown na particao) ---
print(f"\n3. Lendo bronze - particao _ano_mes='{ANO_MES}'...")
t0 = time.time()

df_bronze = (
    spark.read
    .format("delta")
    .load(BRONZE_PATH)
    .filter(F.col("_ano_mes") == ANO_MES)
    .cache()
)

n_bronze = df_bronze.count()
print(f"   {n_bronze:,} linhas lidas da bronze em {time.time()-t0:.1f}s")
df = df_bronze

# --- 4. Transformacoes silver ---
print("\n4. Aplicando transformacoes silver...")

# 4a. Trim + nulidade em passe unico
str_cols_set = {f.name for f in df.schema.fields
                if str(f.dataType) == "StringType()" and not f.name.startswith("_")}
campos_categoricos = ["sexo", "clientela", "ramo_atividade", "despacho"]
campos_cat_set = set(campos_categoricos)

def clean_expr(field):
    c = field.name
    expr = F.col(c)
    if c in str_cols_set:
        expr = F.trim(expr)
    if c in campos_cat_set:
        expr = F.when(expr.isin(VALORES_NULOS), F.lit(None)).otherwise(expr)
    return expr.alias(c)

df = df.select([clean_expr(f) for f in df.schema.fields])
print(f"   Trim + nulidade ({len(str_cols_set)} colunas trim, {len(campos_cat_set)} cat)")

# 4b. Parse banco -> banco_codigo (Int) + banco_nome
banco_parts = F.split(F.col("banco"), r"-")
df = df.select(
    [F.col(c) for c in df.columns if c != "banco"] + [
        banco_parts.getItem(0).cast(IntegerType()).alias("banco_codigo"),
        F.regexp_replace(F.col("banco"), r"^\d+-", "").alias("banco_nome"),
    ]
)
print("   banco -> banco_codigo (Int) + banco_nome")

# 4c. Parse mun_pagto + mun_resid -> codigo + sg_uf + nome
def mun_exprs(col_orig):
    prefix = col_orig.replace("mun_", "")
    parts  = F.split(F.col(col_orig), r"-")
    return [
        parts.getItem(0).alias(f"mun_{prefix}_codigo"),
        F.upper(parts.getItem(1)).alias(f"mun_{prefix}_sg_uf"),
        F.regexp_replace(F.col(col_orig), r"^\d+-[A-Za-z]+-", "").alias(f"mun_{prefix}_nome"),
    ]

other_cols = [F.col(c) for c in df.columns if c not in {"mun_pagto", "mun_resid"}]
df = df.select(other_cols + mun_exprs("mun_pagto") + mun_exprs("mun_resid"))
print("   mun_pagto + mun_resid -> codigo + sg_uf + nome")

# 4d. sg_uf (nome do estado -> sigla) via MapType literal (lookup O(1))
uf_map_expr = F.create_map(
    *[item for pair in
      [(F.lit(k), F.lit(v)) for k, v in UF_PARA_SIGLA.items()]
      for item in pair]
)
df = df.withColumn("sg_uf", uf_map_expr[F.col("uf")])
print(f"   sg_uf derivada ({len(UF_PARA_SIGLA)} estados)")

# 4e. especie_codigo -> Integer (bronze traz como string com espacos)
df = df.withColumn("especie_codigo", F.col("especie_codigo").cast(IntegerType()))
print("   especie_codigo -> Integer")

# 4f. Parse meio_pagamento -> meio_pag_codigo + meio_pag_descricao
mp_map = F.create_map(
    *[item for pair in
      [(F.lit(k), F.lit(f"{v[0]}|{v[1]}")) for k, v in MEIO_PAG_MAP.items()]
      for item in pair]
)
df = df.withColumn("_mp_raw", F.split(F.col("meio_pagamento"), r"\s+-\s+").getItem(0))
df = df.withColumn("meio_pag_codigo",
                   F.split(mp_map[F.col("_mp_raw")], r"\|").getItem(0))
df = df.withColumn("meio_pag_descricao",
                   F.split(mp_map[F.col("_mp_raw")], r"\|").getItem(1))
df = df.drop("meio_pagamento", "_mp_raw")
print("   meio_pagamento -> meio_pag_codigo + meio_pag_descricao")

# 4g. Derivacoes temporais
df = df.withColumn("ano_inicio", F.year("dt_inicio_validade").cast(IntegerType()))
df = df.withColumn("mes_inicio", F.month("dt_inicio_validade").cast(IntegerType()))
print("   Temporais: ano_inicio + mes_inicio")

# 4h. Flags
df = df.withColumn("fl_vl_zero",
                   F.when(F.col("vl_liquido") <= 0, True).otherwise(False))
df = df.withColumn("fl_mesmo_municipio",
                   F.col("mun_pagto_codigo") == F.col("mun_resid_codigo"))
print("   Flags: fl_vl_zero + fl_mesmo_municipio")

# 4i. Metadado silver
silver_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
df = df.withColumn("_silver_ts", F.lit(silver_ts))
print(f"   _silver_ts = {silver_ts}")

print(f"\n   Schema silver ({len(df.columns)} colunas):")
for field in sorted(df.schema.fields, key=lambda f: f.name.startswith("_")):
    marker = " *" if field.name.startswith("_") else "  "
    print(f"    {marker}{field.name:<28} {str(field.dataType)}")

# --- 5. Relatorio de qualidade ANTES de gravar ---
print("\n5. Relatorio de qualidade dos dados silver...")

neg_cols   = [f.name for f in df.schema.fields if not f.name.startswith("_")]
null_exprs = [F.sum(F.col(c).isNull().cast("int")).alias(c) for c in neg_cols]
nulls      = df.select(null_exprs).collect()[0].asDict()

print("\n   Nulos por coluna (top 10):")
top_nulls = sorted(nulls.items(), key=lambda x: x[1], reverse=True)[:10]
for col, cnt in top_nulls:
    pct = cnt / n_bronze * 100
    print(f"     {col:<28} {cnt:>8,}  ({pct:5.1f}%)")

# --- 6. Gravacao Delta Lake ---
# SEM coalesce: o df herda ~11 particoes da leitura do bronze e as transformacoes
# sao narrow (sem shuffle antes do write), entao escrevem em paralelo. coalesce(2)
# - defaultParallelism lido antes dos executors registrarem - estrangularia os
# 41.5M x 29 colunas em 2 tasks (lento e mais sujeito a OOM). Libera o cache
# bronze antes do write para ceder heap ao write.
print(f"\n6. Gravando Delta Lake em {SILVER_PATH} ...")
t_write = time.time()

n_silver = n_bronze          # silver preserva 100% das linhas (sem filtros)
df_bronze.unpersist()

(
    df.write
    .format("delta")
    .mode("overwrite")
    .option("replaceWhere", f"_ano_mes = '{ANO_MES}'")
    .option("overwriteSchema", "true")
    .option("mergeSchema", "true")
    .partitionBy("_ano_mes")
    .save(SILVER_PATH)
)

elapsed = time.time() - t_write
print(f"   Gravacao concluida em {elapsed:.1f}s")

# VACUUM via API DeltaTable (nao spark.sql: evita o cliente Hive). Em dev,
# RETAIN 0 HORAS para liberar espaco imediatamente (em producao, >= 168h).
# Na 1a escrita nao ha versao antiga -> no-op; util nos re-runs.
print("\n   VACUUM (RETAIN 0 HOURS, dev)...")
spark.conf.set("spark.databricks.delta.retentionDurationCheck.enabled", "false")
DeltaTable.forPath(spark, SILVER_PATH).vacuum(0)
print("   VACUUM concluido.")

# --- 7. Validacao final ---
print("\n7. Validando camada silver...")
df_val = spark.read.format("delta").load(SILVER_PATH)

print(f"\n{'='*65}")
print("   RESULTADO FINAL - SILVER v2")
print(f"{'='*65}")
print(f"\n   Linhas bronze     : {n_bronze:>15,}")
print(f"   Linhas silver     : {n_silver:>15,}  (100% preservadas)")
print(f"   Colunas silver    : {len(df_val.columns):>15}")
print(f"   Tempo total       : {time.time()-t0:>14.1f}s")
print(f"\n   Destino  : {SILVER_PATH}")
print(f"   Formato  : Delta Lake | Particao: _ano_mes='{ANO_MES}'")

print("\n   Top 5 UFs - volume financeiro:")
df_val.groupBy("sg_uf", "uf").agg(
    F.count("*").alias("qtd"),
    F.sum("vl_liquido").alias("vl_total"),
    F.avg("vl_liquido").alias("vl_medio"),
).orderBy(F.col("vl_total").desc()).show(5, truncate=False)

print("\n   Top 5 especies de beneficio:")
df_val.groupBy("especie_codigo", "especie_descricao").agg(
    F.count("*").alias("qtd"),
    F.sum("vl_liquido").alias("vl_total"),
).orderBy(F.col("qtd").desc()).show(5, truncate=40)

print("\n   Pagto em municipio != residencia:")
df_val.agg(
    F.count(F.when(~F.col("fl_mesmo_municipio"), True)).alias("pagto_diferente"),
    F.count(F.when( F.col("fl_mesmo_municipio"), True)).alias("pagto_mesmo"),
).show()

print("\n8. Historico Delta (Time Travel):")
DeltaTable.forPath(spark, SILVER_PATH).history().select(
    "version", "timestamp", "operation", "operationParameters"
).show(5, truncate=60)

print(f"\n{'='*65}")
print("   CAMADA SILVER v2 CONCLUIDA COM SUCESSO!")
print(f"{'='*65}\n")

spark.stop()
