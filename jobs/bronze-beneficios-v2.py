"""
==================================================================
     CAMADA BRONZE - Beneficios Emitidos PDA 2025-2027 [v2]
==================================================================

Fonte   : s3a://landing/pda/beneficios-emitidos/202601/   (Parquet raw)
Destino : s3a://bronze/pda/beneficios-emitidos/           (Delta Lake)

Transformacoes minimas aplicadas (bronze = fidelidade ao dado original):
  - Normalizacao de nomes de colunas -> snake_case sem acentos/espacos
  - Cast vl_liquido  : string "1.621,00" -> Decimal(12,2)
  - Cast dt_inicio   : string "30/01/2026" -> Date
  - Metadados de rastreabilidade: _ingestion_ts, _source_path, _ano_mes
  - SEM limpeza de dados, SEM regras de negocio, SEM filtragem de nulos

Idempotencia  : Delta replaceWhere "_ano_mes = '202601'"

Ambiente (ver CLAUDE.md):
  - Delta Lake NAO vem na imagem bitnami/spark:3.5 -> submeter com
    --packages io.delta:delta-spark_2.12:3.2.0 (resolvido no node-1, que tem
    egress; o driver envia os jars aos executors, que nao precisam de internet).
  - fs.s3a.directory.marker.retention=keep (spark-defaults.conf) evita o hang
    de escrita no SeaweedFS.

Como executar (no node-1):
  # Opcao A (recomendada) - o wrapper resolve o container Swarm, copia o .py e
  # submete como uid 0 com o --packages do Delta:
  scripts/run-spark-job.sh jobs/bronze-beneficios-v2.py io.delta:delta-spark_2.12:3.2.0

  # Opcao B (manual) - NAO use `docker exec spark-master`: e servico Swarm, o
  # container tem sufixo aleatorio. Resolva o ID e copie o job pra dentro:
  CID=$(docker ps --filter name=datastack_spark-master -q | head -1)
  docker cp jobs/bronze-beneficios-v2.py "$CID:/tmp/bronze-v2.py"
  docker exec -u 0 "$CID" sh -c 'export HOME=/root && cd /opt/bitnami/spark && \
    bin/spark-submit --conf spark.jars.ivy=/root/.ivy2 \
    --packages io.delta:delta-spark_2.12:3.2.0 \
    --master spark://spark-master:7077 /tmp/bronze-v2.py'
"""

import time
from datetime import datetime, timezone

from delta.tables import DeltaTable
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType

# ---------------------------------------------------------------------------
# Configuracao central - altere apenas estas variaveis para novos periodos
# ---------------------------------------------------------------------------
ANO_MES      = "202601"
LANDING_PATH = f"s3a://landing/pda/beneficios-emitidos/{ANO_MES}/"
BRONZE_PATH  = "s3a://bronze/pda/beneficios-emitidos/"
SOURCE_TAG   = f"landing/pda/beneficios-emitidos/{ANO_MES}"

# ---------------------------------------------------------------------------
# Inicializacao
# ---------------------------------------------------------------------------
print("=" * 65)
print("  BRONZE - Beneficios Emitidos PDA 2025-2027  [v2 - otimizado]")
print("=" * 65)
print("\n1. Iniciando SparkSession com Delta Lake e AQE...")

# AQE: permite ao Spark reotimizar o plano em tempo de execucao, incluindo
# coalesce adaptativo de particoes de shuffle.
spark = (
    SparkSession.builder
    .appName(f"Bronze-PDA-Beneficios-{ANO_MES}-v2")
    .config("spark.sql.extensions",
            "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config("spark.sql.adaptive.enabled", "true")
    .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")
print(f"   Spark {spark.version} | Delta Lake pronto | AQE ativado")

# shuffle.partitions dinamico: o padrao 200 e inadequado tanto para clusters
# pequenos quanto para datasets grandes. Base = total_cores.
# NB: defaultParallelism aqui e lido ANTES dos executors dinamicos registrarem
# (retorna 2). So serve para shuffle.partitions (que nem ha shuffle neste job);
# NAO usar para coalesce do write - ver secao de escrita.
total_cores   = spark.sparkContext.defaultParallelism
shuffle_parts = max(4, total_cores * 2)
spark.conf.set("spark.sql.shuffle.partitions", shuffle_parts)
print(f"   Cores: {total_cores} | shuffle.partitions: {shuffle_parts}")

# ---------------------------------------------------------------------------
# Verificacao do bucket bronze via Hadoop FileSystem API
# ---------------------------------------------------------------------------
print("\n2. Verificando bucket 'bronze'...")
jvm         = spark.sparkContext._jvm
hadoop_conf = spark.sparkContext._jsc.hadoopConfiguration()
fs          = jvm.org.apache.hadoop.fs.FileSystem.get(
                  jvm.java.net.URI.create("s3a://bronze"), hadoop_conf)
bucket_path = jvm.org.apache.hadoop.fs.Path("s3a://bronze/")

if not fs.exists(bucket_path):
    fs.mkdirs(bucket_path)
    print("   Bucket 'bronze' criado.")
else:
    print("   Bucket 'bronze' ja existe.")

# ---------------------------------------------------------------------------
# Leitura da landing
# ---------------------------------------------------------------------------
print(f"\n3. Lendo da landing: {LANDING_PATH}")
t0 = time.time()

df_raw = spark.read.parquet(LANDING_PATH)

print(f"   Schema original ({len(df_raw.columns)} colunas):")
for field in df_raw.schema.fields:
    print(f"     {field.name:<25} {str(field.dataType)}")

# Capturar n_raw ANTES de transformar (reutilizado na validacao final).
print("\n   Contando linhas da landing (reutilizado na validacao)...")
n_raw = df_raw.count()
print(f"   Linhas na landing: {n_raw:,}")

# ---------------------------------------------------------------------------
# Transformacoes minimas - SELECT UNICO
# ---------------------------------------------------------------------------
print("\n4. Aplicando transformacoes minimas (SELECT unico)...")

ingestion_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# Conversao monetaria: "1.621,00" -> remove separador de milhar (.) ->
# virgula decimal vira ponto -> Decimal(12,2)
vl_expr = (
    F.regexp_replace(
        F.regexp_replace(F.col("Vl Líquido"), r"\.", ""),
        r",", "."
    ).cast(DecimalType(12, 2))
)

df = df_raw.select(
    F.col("Despacho").alias("despacho"),
    F.col("`Sexo.`").alias("sexo"),
    F.col("Clientela").alias("clientela"),
    F.col("Tipo Benefício").alias("tipo_beneficio"),
    F.col("UF").alias("uf"),
    F.col("Meio pagamento").alias("meio_pagamento"),
    F.col("Banco").alias("banco"),
    F.col("Mun Pagto").alias("mun_pagto"),
    F.col("Mun Resid").alias("mun_resid"),
    vl_expr.alias("vl_liquido"),
    F.col("Ramo Atividade").alias("ramo_atividade"),
    F.to_date(F.col("Dt início validade"), "dd/MM/yyyy").alias("dt_inicio_validade"),
    F.col("Espécie12").alias("especie_codigo"),
    F.col("Espécie13").alias("especie_descricao"),
    # Metadados de rastreabilidade
    F.lit(ANO_MES).alias("_ano_mes"),
    F.lit(SOURCE_TAG).alias("_source_path"),
    F.lit(ingestion_ts).alias("_ingestion_ts"),
)

print(f"   Schema bronze ({len(df.columns)} colunas):")
for field in df.schema.fields:
    marker = " *" if field.name.startswith("_") else "  "
    print(f"    {marker}{field.name:<25} {str(field.dataType)}")

# ---------------------------------------------------------------------------
# Escrita Delta Lake com idempotencia via replaceWhere
# ---------------------------------------------------------------------------
print(f"\n5. Gravando Delta Lake em {BRONZE_PATH} ...")
print(f"   Particao: _ano_mes = '{ANO_MES}'  |  modo: overwrite (replaceWhere)")

t_write = time.time()

# SEM coalesce: o df herda as ~88 particoes da leitura do landing (Parquet) e o
# select e narrow (sem shuffle), entao as 88 tasks escrevem em paralelo pelos
# executors. Um coalesce(2) - defaultParallelism lido antes dos executors
# registrarem - estrangulava os 41M em 2 tasks (medido: 108s). Delta compacta
# depois com OPTIMIZE se quiser menos arquivos.
(
    df
    .write
    .format("delta")
    .mode("overwrite")
    .option("replaceWhere", f"_ano_mes = '{ANO_MES}'")
    .option("overwriteSchema", "true")
    .option("mergeSchema", "true")
    .partitionBy("_ano_mes")
    .save(BRONZE_PATH)
)

elapsed_write = time.time() - t_write
print(f"   Gravacao concluida em {elapsed_write:.1f}s")

# ---------------------------------------------------------------------------
# Validacao - passe unico via agg()
# ---------------------------------------------------------------------------
print("\n6. Validando camada bronze...")
df_val = spark.read.format("delta").load(BRONZE_PATH)

val = df_val.agg(
    F.count("*").alias("total"),
    F.sum(F.col("vl_liquido").isNull().cast("int")).alias("null_vl"),
    F.sum(F.col("dt_inicio_validade").isNull().cast("int")).alias("null_dt"),
).collect()[0]

row_count = val["total"]
null_vl   = val["null_vl"]
null_dt   = val["null_dt"]

print(f"\n{'='*65}")
print("   RESULTADO FINAL - BRONZE")
print(f"{'='*65}")
print(f"\n   Linhas gravadas    : {row_count:>15,}")
print(f"   Linhas totais src  : {n_raw:>15,}")
print(f"   Nulos vl_liquido   : {null_vl:>15,}  ({null_vl/row_count*100:.2f}%)")
print(f"   Nulos dt_inicio    : {null_dt:>15,}  ({null_dt/row_count*100:.2f}%)")
print(f"   Tempo total        : {time.time()-t0:>14.1f}s")
print(f"\n   Destino  : {BRONZE_PATH}")
print(f"   Particao : _ano_mes = '{ANO_MES}'")
print("   Formato  : Delta Lake  (ACID | Time Travel | Schema Evolution)")

print("\n   Amostra - Top UFs:")
(
    df_val
    .groupBy("uf")
    .agg(
        F.count("*").alias("qtd_beneficios"),
        F.sum("vl_liquido").alias("vl_total"),
    )
    .orderBy(F.col("qtd_beneficios").desc())
    .show(10, truncate=False)
)

print("\n   Primeiras 5 linhas da camada bronze:")
df_val.select(
    "despacho", "sexo", "uf", "tipo_beneficio",
    "vl_liquido", "dt_inicio_validade",
    "especie_codigo", "especie_descricao",
    "_ano_mes", "_ingestion_ts"
).show(5, truncate=35)

print("\n7. Historico Delta (Time Travel):")
# API nativa do Delta (le o _delta_log direto). NAO usar
# spark.sql("DESCRIBE HISTORY ...") aqui: como spark-defaults tem
# catalogImplementation=hive + metastore.version=3.1.3 sem metastore.jars, a
# primeira query SQL que inicializa o cliente Hive quebra com
# "Builtin jars ... Execution 2.3.9 != Metastore 3.1.3". A escrita Delta (API
# DataFrame) nao passa por Hive; so o SQL passava.
DeltaTable.forPath(spark, BRONZE_PATH).history().select(
    "version", "timestamp", "operation", "operationParameters"
).show(5, truncate=60)

print(f"\n{'='*65}")
print("   CAMADA BRONZE CONCLUIDA COM SUCESSO!  [v2 - otimizado]")
print(f"{'='*65}\n")

spark.stop()
