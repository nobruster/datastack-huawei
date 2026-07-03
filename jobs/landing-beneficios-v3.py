"""
Pipeline Landing Zone - Beneficios Emitidos PDA 2025-2027  [v3 — cluster-ready]
Fonte : Portal de Dados Abertos do Governo Federal
Destino: SeaweedFS  s3a://landing/pda/beneficios-emitidos/202601/

Diferenca central para o v2 (por que o v2 nao roda distribuido):
  o v2 le o CSV de um CAMINHO LOCAL do driver. Num cluster Spark o work dir e um
  volume local de cada no, entao os executors em node-2/node-3 NAO enxergam o
  arquivo do driver (node-1) -> funciona so em local mode.

v3 resolve com o padrao de STAGING:
  1. Baixa o ZIP no driver (node-1 tem o EIP; os workers nao tem egress)
  2. Extrai o CSV para um cache local do driver
  3. Estagia o CSV no proprio SeaweedFS (fs.copyFromLocalFile -> s3a://.../_staging/)
  4. Le de s3a://.../_staging/ -> agora TODO executor enxerga o dado
  5. Grava Parquet em s3a://landing/pda/beneficios-emitidos/202601/
  6. Valida lendo de volta (groupBy UF em passe unico)

Requisitos de ambiente (ver CLAUDE.md), todos ja em config/spark/spark-defaults.conf:
  - S3A MAGIC COMMITTER: o FileOutputCommitter finaliza com rename=COPY, que o
    SeaweedFS rejeita (hang silencioso). Jar spark-hadoop-cloud bind-montado em
    todos os nodes Spark.
  - fs.s3a.directory.marker.retention=keep: sem isso, o S3A tenta apagar
    marcadores de diretorio a cada arquivo escrito e o SeaweedFS responde 500 ->
    retry-backoff -> a escrita de muitos arquivos TRAVA por minutos. Com 'keep' a
    gravacao de 41M linhas / 88 arquivos leva ~13s.
  - Submeter como uid com entrada em /etc/passwd: `docker exec -u 0 <spark-master>`
    (o exec fora do entrypoint bitnami quebra o login UGI do Hadoop com uid 1001).

Como executar (no node-1):
  # Opcao A (recomendada) - o wrapper resolve o container Swarm, copia o .py e
  # submete como uid 0. Sem --packages (este job nao usa Delta):
  scripts/run-spark-job.sh jobs/landing-beneficios-v3.py

  # Opcao B (manual) - NAO use `docker exec spark-master`: e servico Swarm, o
  # container tem sufixo aleatorio. Resolva o ID e copie o job pra dentro:
  CID=$(docker ps --filter name=datastack_spark-master -q | head -1)
  docker cp jobs/landing-beneficios-v3.py "$CID:/tmp/landing-v3.py"
  docker exec -u 0 "$CID" sh -c 'export HOME=/root && cd /opt/bitnami/spark && \
    bin/spark-submit --conf spark.jars.ivy=/root/.ivy2 \
    --master spark://spark-master-1:7077,spark-master-2:7077,spark-master-3:7077 /tmp/landing-v3.py'
"""
import io
import os
import time
import urllib.request
import zipfile

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

# ---------------------------------------------------------------------------
# Configuracoes
# ---------------------------------------------------------------------------
URL = (
    "https://armazenamento-dadosabertos.s3.sa-east-1.amazonaws.com/"
    "PDA_2025_2027/Grupos_de_dados/Benef%C3%ADcios+emitidos/"
    "D.SDA.PDA.003.EMI.202601.CSV.ZIP"
)

CACHE_DIR = "/tmp/dados-abertos"
CSV_NAME = "D.SDA.PDA.003.EMI.202601.csv"
csv_path = os.path.join(CACHE_DIR, CSV_NAME)

BUCKET = "landing"
STAGING_PATH = f"s3a://{BUCKET}/pda/_staging/"
DEST_PATH = f"s3a://{BUCKET}/pda/beneficios-emitidos/202601/"

os.makedirs(CACHE_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# 1. Download + extracao (pula se CSV ja existe no cache local do driver)
# ---------------------------------------------------------------------------
print("=" * 60)
if os.path.exists(csv_path):
    size_mb = os.path.getsize(csv_path) / (1024 ** 2)
    print(f"1. Cache local encontrado: {csv_path} ({size_mb:.1f} MB) - pulando download")
else:
    print("1. Baixando ZIP da fonte publica (no driver)...")
    t0 = time.time()
    zip_local = os.path.join(CACHE_DIR, "beneficios.zip")
    # Stream para disco (o ZIP tem ~549MB — evita segurar tudo em memoria).
    urllib.request.urlretrieve(URL, zip_local)
    print(f"   Baixado: {os.path.getsize(zip_local)/(1024**2):.1f} MB em {time.time()-t0:.1f}s")

    print("2. Extraindo CSV...")
    with zipfile.ZipFile(zip_local) as zf:
        csv_files = [f for f in zf.namelist() if f.lower().endswith(".csv")]
        zf.extract(csv_files[0], CACHE_DIR)
        extracted = os.path.join(CACHE_DIR, csv_files[0])
    if extracted != csv_path:
        os.replace(extracted, csv_path)
    os.remove(zip_local)
    print(f"   Extraido: {csv_path} ({os.path.getsize(csv_path)/(1024**2):.1f} MB)")

# ---------------------------------------------------------------------------
# 2. Detectar separador (le so o header local)
# ---------------------------------------------------------------------------
with open(csv_path, "r", encoding="latin-1") as f:
    header = f.readline()
sep = next((s for s in [";", ",", "\t", "|"] if header.count(s) > 2), ";")
print(f"\n3. Separador detectado: '{sep}'")

# ---------------------------------------------------------------------------
# 3. SparkSession — herda S3A/SeaweedFS + magic committer de spark-defaults.conf.
#    Configs de performance S3A no builder (iguais ao v2).
# ---------------------------------------------------------------------------
print("\n4. Iniciando SparkSession...")
spark = (
    SparkSession.builder
    .appName("Landing-PDA-Beneficios-202601-v3")
    # 128m (nao 256m): com ~11.6 GB isso gera ~88 particoes de leitura em vez
    # de ~44. Como o job nao tem shuffle (read->write direto), o numero de
    # particoes de entrada = paralelismo de escrita = numero de arquivos de
    # saida. ~88 tasks saturam melhor os executors quando a alocacao dinamica
    # escala (ate maxExecutors=10 = 40 cores) e reduzem stragglers; a saida
    # fica ~130 MB/arquivo (tamanho Parquet saudavel).
    .config("spark.sql.files.maxPartitionBytes", "128m")
    .config("spark.sql.adaptive.enabled", "true")
    .config("spark.sql.adaptive.coalescePartitions.enabled", "true")
    .config("spark.hadoop.fs.s3a.multipart.size", "134217728")       # 128 MB por parte
    .config("spark.hadoop.fs.s3a.connection.maximum", "100")         # pool de 100 conexoes
    .config("spark.hadoop.fs.s3a.fast.upload", "true")               # upload assincrono
    .config("spark.hadoop.fs.s3a.fast.upload.buffer", "bytebuffer")  # buffer em heap
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

jvm = spark.sparkContext._jvm
hadoop_conf = spark.sparkContext._jsc.hadoopConfiguration()
fs = jvm.org.apache.hadoop.fs.FileSystem.get(
    jvm.java.net.URI.create(f"s3a://{BUCKET}"), hadoop_conf
)

# ---------------------------------------------------------------------------
# 4. Garantir o bucket 'landing'
# ---------------------------------------------------------------------------
print(f"\n5. Verificando bucket '{BUCKET}'...")
bucket_path = jvm.org.apache.hadoop.fs.Path(f"s3a://{BUCKET}/")
if not fs.exists(bucket_path):
    fs.mkdirs(bucket_path)
    print(f"   Bucket '{BUCKET}' criado.")
else:
    print(f"   Bucket '{BUCKET}' ja existe.")

# ---------------------------------------------------------------------------
# 5. STAGING — sobe o CSV local do driver para o SeaweedFS, para que todos os
#    executors (em qualquer node) leiam a MESMA fonte via s3a://. Sem isso, o
#    CSV so existe no filesystem local do driver e falha distribuido.
# ---------------------------------------------------------------------------
staging_file = STAGING_PATH + CSV_NAME
print(f"\n6. Estagiando CSV no SeaweedFS: {staging_file}")
t0 = time.time()
local_src = jvm.org.apache.hadoop.fs.Path("file://" + csv_path)
staging_dst = jvm.org.apache.hadoop.fs.Path(staging_file)
# copyFromLocalFile(delSrc=False, overwrite=True, src, dst)
fs.copyFromLocalFile(False, True, local_src, staging_dst)
print(f"   Estagiado em {time.time()-t0:.1f}s")

# ---------------------------------------------------------------------------
# 6. Ler CSV de s3a:// (distribuido) — landing = raw, inferSchema=False
# ---------------------------------------------------------------------------
print("\n7. Lendo CSV do staging (s3a://) com Spark...")
df = spark.read.csv(
    staging_file,
    header=True,
    inferSchema=False,
    sep=sep,
    encoding="ISO-8859-1",
)

# Desambiguar colunas duplicadas ('Especie' aparece 2x no dataset)
seen, new_cols = {}, []
for c in df.columns:
    if c in seen:
        seen[c] += 1
        new_cols.append(f"{c}_{seen[c]}")
    else:
        seen[c] = 0
        new_cols.append(c)
df = df.toDF(*new_cols)
print(f"   Colunas ({len(df.columns)}): {df.columns}")

# ---------------------------------------------------------------------------
# 7. Gravar Parquet no destino (magic committer via spark-defaults.conf)
#    SEM coalesce: a leitura do CSV ja particiona por spark.sql.files.
#    maxPartitionBytes (256m) -> ~1 particao por 256 MB, todas escritas em
#    paralelo pelos executors (com dynamicAllocation escalando ate maxExecutors).
#    Um coalesce(N) baixo aqui estrangularia os 11 GB por N tasks (foi o que
#    deixou a v1 desta run gastando 6 de 8 cores ociosos). O numero de arquivos
#    de saida acompanha as particoes de entrada (~250 MB/arquivo = tamanho
#    Parquet saudavel; nao precisa compactar).
# ---------------------------------------------------------------------------
n_parts = df.rdd.getNumPartitions()
print(f"\n8. Gravando em {DEST_PATH} ({n_parts} particoes de escrita)...")
t0 = time.time()
df.write.mode("overwrite").parquet(DEST_PATH)
print(f"   Gravacao concluida em {time.time()-t0:.1f}s")

# ---------------------------------------------------------------------------
# 8. Validacao em passe unico (groupBy UF persistido; total derivado da soma)
# ---------------------------------------------------------------------------
print("\n9. Validando leitura do destino...")
df_val = spark.read.parquet(DEST_PATH)
top_uf = (
    df_val.groupBy("UF")
    .agg(F.count("*").alias("count"))
    .orderBy(F.col("count").desc())
    .persist()
)
row_count = top_uf.agg(F.sum("count")).collect()[0][0]

print(f"\n{'='*60}")
print("   RESULTADOS DA VALIDACAO")
print(f"{'='*60}")
print(f"\n   Linhas gravadas : {row_count:,}")
print(f"   Destino         : {DEST_PATH}")
print("\n   Schema:")
df_val.printSchema()
print("\n   Top 10 UFs por volume de beneficios:")
top_uf.show(10, truncate=False)
top_uf.unpersist()

# ---------------------------------------------------------------------------
# 9. Limpeza do staging (o Parquet final ja e a fonte de verdade)
# ---------------------------------------------------------------------------
print("\n10. Limpando staging...")
try:
    fs.delete(jvm.org.apache.hadoop.fs.Path(staging_file), False)
    print("   Staging removido.")
except Exception as e:  # noqa: BLE001
    print(f"   Aviso: falha ao remover staging ({e}) - ignorando.")

print(f"\n{'='*60}")
print("   LANDING ZONE (v3) CONCLUIDA COM SUCESSO!")
print(f"{'='*60}\n")

spark.stop()
