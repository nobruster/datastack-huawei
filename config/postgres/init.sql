-- Databases e usuarios para Hive Metastore e Superset
-- Executado automaticamente pelo entrypoint da imagem postgres na primeira
-- inicializacao (diretorio de dados vazio).
CREATE USER hive WITH PASSWORD 'hive_password_CHANGE_ME';
CREATE DATABASE hive_metastore OWNER hive;
CREATE USER superset WITH PASSWORD 'superset_password_CHANGE_ME';
CREATE DATABASE superset OWNER superset;
GRANT ALL PRIVILEGES ON DATABASE hive_metastore TO hive;
GRANT ALL PRIVILEGES ON DATABASE superset TO superset;
