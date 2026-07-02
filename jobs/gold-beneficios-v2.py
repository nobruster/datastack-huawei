"""
==================================================================
   CAMADA OURO - Beneficios Emitidos PDA 2025-2027 [v2]
==================================================================

Fonte   : s3a://prata/pda/beneficios-emitidos/   (Delta Lake Silver)
Destino : s3a://ouro/pda/beneficios-emitidos/    (Delta Lake - 4 tabelas)

--- TABELAS GERADAS ---
  fat_uf         grain: (uf x grupo_especie x _ano_mes)
  fat_especie    grain: (especie x _ano_mes)     ~57 linhas/mes
  fat_banco      grain: (banco x _ano_mes)        ~21 linhas/mes
  kpis_nacionais grain: (_ano_mes)                1 linha/mes

--- AMBIENTE (ver CLAUDE.md) ---
  - Delta via --packages io.delta:delta-spark_2.12:3.2.0.
  - catalogImplementation=in-memory: este job usa spark.sql() sobre TEMP VIEWS
    (nao tabelas do metastore). Com o catalogo Hive (padrao do spark-defaults) a
    1a query inicializa o cliente Hive e quebra (metastore 3.1.3 sem
    metastore.jars: "Execution 2.3.9 != Metastore 3.1.3"). in-memory + caminhos
    Delta contorna isso sem depender do metastore.
  - PERSIST: a silver e lida 1x e reutilizada nas 4 tabelas.

Como executar (no node-1):
  scripts/run-spark-job.sh jobs/gold-beneficios-v2.py io.delta:delta-spark_2.12:3.2.0
"""

import time
from datetime import datetime, timezone

from delta.tables import DeltaTable
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql import Window

# --- Configuracoes ---
ANO_MES     = "202601"
SILVER_PATH = "s3a://prata/pda/beneficios-emitidos/"
GOLD_BASE   = "s3a://ouro/pda/beneficios-emitidos"

GOLD_FAT_UF      = f"{GOLD_BASE}/fat_uf/"
GOLD_FAT_ESPECIE = f"{GOLD_BASE}/fat_especie/"
GOLD_FAT_BANCO   = f"{GOLD_BASE}/fat_banco/"
GOLD_KPIS        = f"{GOLD_BASE}/kpis_nacionais/"

# Classificacao de especies em grupos de negocio
GRUPO_ESPECIE_SQL = """
    CASE
        WHEN especie_codigo IN (1,2,3,21,22,23,26,27,28,29,54,55,56,59)
            THEN 'Pensao'
        WHEN especie_codigo IN (4,5,6,7,8,30,32,33,34,37,38,40,41,42,43,44,45,46,47,48,49,51,57,58,72,79)
            THEN 'Aposentadoria'
        WHEN especie_codigo IN (10,13,16,18,25,31,36,60)
            THEN 'Auxilio'
        WHEN especie_codigo IN (11,12,87,88)
            THEN 'Amparo_BPC'
        ELSE 'Outros'
    END
"""

GOLD_TS = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

print("=" * 65)
print("  OURO - Beneficios Emitidos PDA 2025-2027 [v2]")
print("=" * 65)

# --- SparkSession com Delta + catalogo in-memory ---
print("\n1. Iniciando SparkSession...")
spark = (
    SparkSession.builder
    .appName(f"Gold-PDA-Beneficios-v2-{ANO_MES}")
    .config("spark.sql.extensions",
            "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    # in-memory: usa spark.sql() sobre temp views sem inicializar o cliente Hive
    # (que quebra: metastore 3.1.3 sem metastore.jars). Ver docstring.
    .config("spark.sql.catalogImplementation", "in-memory")
    .config("spark.sql.adaptive.enabled", "true")
    .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

total_cores   = spark.sparkContext.defaultParallelism
shuffle_parts = max(48, total_cores * 24)
spark.conf.set("spark.sql.shuffle.partitions", shuffle_parts)
print(f"   Spark {spark.version} | Delta | shuffle.partitions={shuffle_parts} | catalog=in-memory")

# --- 2. Leitura da silver (cache; varrida 4x) ---
print("\n2. Lendo silver (cache em memoria - reutilizada 4x)...")
t0 = time.time()

df = (
    spark.read.format("delta").load(SILVER_PATH)
    .filter(F.col("_ano_mes") == ANO_MES)
    .persist()
)
n_total = df.count()
print(f"   {n_total:,} linhas em cache | {time.time()-t0:.1f}s")
df.createOrReplaceTempView("silver")

# --- TABELA 1: fat_uf (grain: UF x grupo_especie x mes) ---
print("\n3. Construindo fat_uf...")

fat_uf_raw = spark.sql(f"""
    SELECT
        uf, sg_uf, _ano_mes,
        COUNT(*)                                    AS qtd_beneficios,
        SUM(vl_liquido)                             AS vl_total,
        AVG(vl_liquido)                             AS vl_medio,
        PERCENTILE_APPROX(vl_liquido, 0.5, 100)     AS vl_mediano,
        MIN(vl_liquido)                             AS vl_minimo,
        MAX(vl_liquido)                             AS vl_maximo,
        ROUND(100.0 * SUM(CASE WHEN sexo = 'Feminino'  THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_feminino,
        ROUND(100.0 * SUM(CASE WHEN sexo = 'Masculino' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_masculino,
        ROUND(100.0 * SUM(CASE WHEN ramo_atividade = 'Rural' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_rural,
        ROUND(100.0 * SUM(CASE WHEN fl_mesmo_municipio = true THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_pagto_mesmo_municipio,
        COUNT(DISTINCT mun_resid_codigo)            AS qtd_municipios_atendidos,
        COUNT(DISTINCT banco_codigo)                AS qtd_bancos_utilizados,
        {GRUPO_ESPECIE_SQL}                         AS grupo_especie_top,
        '{GOLD_TS}'                                 AS _gold_ts
    FROM silver
    GROUP BY uf, sg_uf, _ano_mes, {GRUPO_ESPECIE_SQL}
""")

w_nac = Window.partitionBy("_ano_mes")
fat_uf = (
    fat_uf_raw
    .withColumn("rank_qtd", F.dense_rank().over(w_nac.orderBy(F.col("qtd_beneficios").desc())))
    .withColumn("rank_vl_total", F.dense_rank().over(w_nac.orderBy(F.col("vl_total").desc())))
    .withColumn("pct_nacional_qtd",
        F.round(F.col("qtd_beneficios") * 100.0 / F.sum("qtd_beneficios").over(w_nac), 2))
    .withColumn("pct_nacional_valor",
        F.round(F.col("vl_total") * 100.0 / F.sum("vl_total").over(w_nac), 2))
)

fat_uf = fat_uf.persist()
n_fat_uf = fat_uf.count()

total_pct = fat_uf.agg(F.sum("pct_nacional_qtd")).collect()[0][0]
assert abs(float(total_pct) - 100.0) < 1.0, \
    f"FALHA: pct_nacional_qtd soma {total_pct}, esperado ~100"
print(f"   Validacao: pct_nacional_qtd soma {total_pct:.2f}% (esperado ~100)")

(fat_uf.write.format("delta").mode("overwrite")
    .option("replaceWhere", f"_ano_mes = '{ANO_MES}'")
    .option("overwriteSchema", "true").option("mergeSchema", "true")
    .partitionBy("_ano_mes").save(GOLD_FAT_UF))
print(f"   fat_uf gravada: {n_fat_uf} linhas -> {GOLD_FAT_UF}")

# --- TABELA 2: fat_especie (grain: especie x mes) ---
print("\n4. Construindo fat_especie...")

fat_especie = spark.sql(f"""
    WITH base AS (
        SELECT especie_codigo, especie_descricao, {GRUPO_ESPECIE_SQL} AS grupo_especie,
               _ano_mes, uf, sg_uf, vl_liquido, sexo, ramo_atividade, clientela,
               fl_mesmo_municipio, fl_vl_zero
        FROM silver
    ),
    uf_top AS (
        SELECT especie_codigo, sg_uf AS uf_top,
               ROW_NUMBER() OVER (PARTITION BY especie_codigo ORDER BY COUNT(*) DESC) AS rn
        FROM base GROUP BY especie_codigo, sg_uf
    )
    SELECT
        b.especie_codigo, b.especie_descricao, b.grupo_especie, b._ano_mes,
        COUNT(*)                                        AS qtd_beneficios,
        SUM(b.vl_liquido)                               AS vl_total,
        AVG(b.vl_liquido)                               AS vl_medio,
        PERCENTILE_APPROX(b.vl_liquido, 0.5, 100)       AS vl_mediano,
        MAX(b.vl_liquido)                               AS vl_maximo,
        ROUND(100.0 * SUM(CASE WHEN b.sexo = 'Feminino' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_feminino,
        ROUND(100.0 * SUM(CASE WHEN b.ramo_atividade = 'Rural' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_rural,
        ROUND(100.0 * SUM(CASE WHEN b.clientela = 'Urbano' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_urbano,
        ROUND(100.0 * SUM(CASE WHEN b.fl_mesmo_municipio THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_mesmo_municipio,
        COUNT(DISTINCT b.uf)                            AS qtd_ufs_atendidas,
        ut.uf_top                                       AS uf_com_mais_beneficios,
        SUM(CASE WHEN b.fl_vl_zero THEN 1 ELSE 0 END)   AS qtd_vl_zero,
        '{GOLD_TS}'                                     AS _gold_ts
    FROM base b
    LEFT JOIN uf_top ut ON b.especie_codigo = ut.especie_codigo AND ut.rn = 1
    GROUP BY b.especie_codigo, b.especie_descricao, b.grupo_especie, b._ano_mes, ut.uf_top
    ORDER BY qtd_beneficios DESC
""")

w_grupo = Window.partitionBy("_ano_mes", "grupo_especie").orderBy(F.col("qtd_beneficios").desc())
fat_especie = fat_especie.withColumn("rank_no_grupo", F.dense_rank().over(w_grupo))

fat_especie = fat_especie.persist()
n_fat_esp = fat_especie.count()

(fat_especie.write.format("delta").mode("overwrite")
    .option("replaceWhere", f"_ano_mes = '{ANO_MES}'")
    .option("overwriteSchema", "true").option("mergeSchema", "true")
    .partitionBy("_ano_mes").save(GOLD_FAT_ESPECIE))
print(f"   fat_especie gravada: {n_fat_esp} linhas -> {GOLD_FAT_ESPECIE}")

# --- TABELA 3: fat_banco (grain: banco x mes) ---
print("\n5. Construindo fat_banco...")

fat_banco_raw = spark.sql(f"""
    SELECT
        banco_codigo, banco_nome, _ano_mes,
        COUNT(*)                                        AS qtd_beneficios,
        SUM(vl_liquido)                                 AS vl_total,
        AVG(vl_liquido)                                 AS vl_medio,
        COUNT(DISTINCT uf)                              AS qtd_ufs_atendidas,
        COUNT(DISTINCT mun_pagto_codigo)                AS qtd_municipios_pagadores,
        ROUND(100.0 * SUM(CASE WHEN meio_pag_codigo = 'CCF' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_conta_corrente,
        ROUND(100.0 * SUM(CASE WHEN meio_pag_codigo = 'CMG' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_cartao,
        '{GOLD_TS}'                                     AS _gold_ts
    FROM silver
    GROUP BY banco_codigo, banco_nome, _ano_mes
""")

w_banco = Window.partitionBy("_ano_mes")
fat_banco = (
    fat_banco_raw
    .withColumn("rank_qtd", F.dense_rank().over(w_banco.orderBy(F.col("qtd_beneficios").desc())))
    .withColumn("market_share_pct",
        F.round(F.col("qtd_beneficios") * 100.0 / F.sum("qtd_beneficios").over(w_banco), 2))
    .withColumn("market_share_valor_pct",
        F.round(F.col("vl_total") * 100.0 / F.sum("vl_total").over(w_banco), 2))
)

fat_banco = fat_banco.persist()
n_fat_ban = fat_banco.count()

(fat_banco.write.format("delta").mode("overwrite")
    .option("replaceWhere", f"_ano_mes = '{ANO_MES}'")
    .option("overwriteSchema", "true").option("mergeSchema", "true")
    .partitionBy("_ano_mes").save(GOLD_FAT_BANCO))
print(f"   fat_banco gravada: {n_fat_ban} linhas -> {GOLD_FAT_BANCO}")

# --- TABELA 4: kpis_nacionais (grain: mes) ---
# Anti-OOM: PERCENTILE_APPROX separado com accuracy=100 (~100x menos memoria).
print("\n6. Construindo kpis_nacionais...")

kpis_main = spark.sql(f"""
    SELECT
        _ano_mes,
        COUNT(*)                                          AS total_beneficios,
        COUNT(DISTINCT uf)                                AS total_ufs_atendidas,
        COUNT(DISTINCT mun_resid_codigo)                  AS total_municipios_atendidos,
        SUM(vl_liquido)                                   AS vl_total_brasil,
        AVG(vl_liquido)                                   AS vl_medio_nacional,
        MAX(vl_liquido)                                   AS vl_maximo_nacional,
        ROUND(100.0 * SUM(CASE WHEN sexo = 'Feminino' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_feminino,
        ROUND(100.0 * SUM(CASE WHEN ramo_atividade = 'Rural' THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_rural,
        ROUND(100.0 * SUM(CASE WHEN fl_mesmo_municipio THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_pagto_mesmo_municipio,
        COUNT(DISTINCT especie_codigo)                    AS total_especies_ativas,
        COUNT(DISTINCT banco_codigo)                      AS total_bancos_pagadores,
        SUM(CASE WHEN fl_vl_zero   THEN 1 ELSE 0 END)     AS qtd_vl_zero,
        SUM(CASE WHEN sexo IS NULL THEN 1 ELSE 0 END)     AS qtd_sexo_nulo
    FROM silver
    GROUP BY _ano_mes
""")

print("   6b. Percentis com accuracy=100 (low-memory)...")
kpis_perc = spark.sql("""
    SELECT
        _ano_mes,
        PERCENTILE_APPROX(vl_liquido, 0.5, 100) AS vl_mediano_nacional,
        PERCENTILE_APPROX(vl_liquido, 0.9, 100) AS vl_p90_nacional
    FROM silver
    GROUP BY _ano_mes
""")

kpis = (
    kpis_main.join(kpis_perc, on="_ano_mes", how="inner")
    .withColumn("_gold_ts", F.lit(GOLD_TS))
)

kpis = kpis.persist()
kpis.count()

(kpis.write.format("delta").mode("overwrite")
    .option("replaceWhere", f"_ano_mes = '{ANO_MES}'")
    .option("overwriteSchema", "true").option("mergeSchema", "true")
    .partitionBy("_ano_mes").save(GOLD_KPIS))
print(f"   kpis_nacionais gravada: 1 linha -> {GOLD_KPIS}")

# --- 7. Validacao cruzada (DataFrames em cache, sem re-leitura Delta) ---
print("\n7. Validacao cruzada das tabelas gold...")
kpi_row     = kpis.collect()[0]
sum_qtd_esp = fat_especie.agg(F.sum("qtd_beneficios")).collect()[0][0]
assert abs(int(sum_qtd_esp) - int(kpi_row["total_beneficios"])) < 10, \
    f"FALHA: fat_especie qtd {sum_qtd_esp} != kpi {kpi_row['total_beneficios']}"
print(f"   fat_especie qtd = kpi total ({int(sum_qtd_esp):,})")

print(f"\n{'='*65}")
print("   RESULTADO FINAL - CAMADA OURO")
print(f"{'='*65}")
print(f"\n   {'Tabela':<22} {'Linhas':>8}")
print(f"   {'-'*32}")
print(f"   {'fat_uf':<22} {n_fat_uf:>8}")
print(f"   {'fat_especie':<22} {n_fat_esp:>8}")
print(f"   {'fat_banco':<22} {n_fat_ban:>8}")
print(f"   {'kpis_nacionais':<22} {1:>8}")

print(f"\n   -- KPIs Nacionais ({ANO_MES}) --")
print(f"   Total beneficios    : {int(kpi_row['total_beneficios']):>15,}")
print(f"   Municipios atendidos: {int(kpi_row['total_municipios_atendidos']):>15,}")
print(f"   Valor total         : R$ {float(kpi_row['vl_total_brasil']):>18,.2f}")
print(f"   Valor medio         : R$ {float(kpi_row['vl_medio_nacional']):>18,.2f}")
print(f"   Mediana             : R$ {float(kpi_row['vl_mediano_nacional']):>18,.2f}")
print(f"   P90                 : R$ {float(kpi_row['vl_p90_nacional']):>18,.2f}")
print(f"   % Feminino          :    {float(kpi_row['pct_feminino']):>16.2f}%")
print(f"   % Rural             :    {float(kpi_row['pct_rural']):>16.2f}%")
print(f"   Especies ativas     : {int(kpi_row['total_especies_ativas']):>15,}")
print(f"   Bancos pagadores    : {int(kpi_row['total_bancos_pagadores']):>15,}")
print(f"   Sexo nulo           : {int(kpi_row['qtd_sexo_nulo']):>15,}")

print("\n   -- Top 5 UFs por valor total --")
(fat_uf.filter("rank_vl_total <= 5")
    .select("rank_vl_total", "sg_uf", "qtd_beneficios", "vl_total", "vl_medio",
            "pct_feminino", "pct_nacional_valor")
    .orderBy("rank_vl_total").show(5, truncate=False))

print("\n   -- Top 5 Especies por volume --")
(fat_especie
    .select("especie_codigo", "especie_descricao", "grupo_especie",
            "qtd_beneficios", "vl_total", "vl_medio", "uf_com_mais_beneficios")
    .orderBy(F.col("qtd_beneficios").desc()).show(5, truncate=40))

print("\n   -- Market Share Bancario (Top 5) --")
(fat_banco
    .select("rank_qtd", "banco_codigo", "banco_nome",
            "qtd_beneficios", "market_share_pct", "market_share_valor_pct")
    .orderBy("rank_qtd").show(5, truncate=False))

print("\n   -- Grupos de Especie --")
(fat_especie.groupBy("grupo_especie")
    .agg(F.sum("qtd_beneficios").alias("qtd"), F.sum("vl_total").alias("vl_total"))
    .orderBy(F.col("qtd").desc()).show(truncate=False))

print("\n8. Historico Delta (fat_uf):")
DeltaTable.forPath(spark, GOLD_FAT_UF).history().select(
    "version", "timestamp", "operation").show(3, truncate=40)

print(f"\n   Tempo total: {time.time()-t0:.1f}s")
print(f"\n{'='*65}")
print("   CAMADA OURO CONCLUIDA COM SUCESSO!")
print(f"{'='*65}\n")

fat_uf.unpersist(); fat_especie.unpersist(); fat_banco.unpersist()
kpis.unpersist(); df.unpersist()
spark.stop()
