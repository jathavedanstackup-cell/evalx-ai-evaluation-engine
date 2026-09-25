#!/bin/sh
set -eu

# Create dedicated test application role and database using least privilege (NO SUPERUSER)
psql --username "$POSTGRES_USER" --dbname postgres --set=ON_ERROR_STOP=1 <<-EOSQL
CREATE ROLE $POSTGRES_TEST_USER LOGIN PASSWORD '$POSTGRES_TEST_PASSWORD';
CREATE DATABASE $POSTGRES_TEST_DB OWNER $POSTGRES_TEST_USER;
GRANT ALL PRIVILEGES ON DATABASE $POSTGRES_TEST_DB TO $POSTGRES_TEST_USER;
EOSQL

# Install pgvector extension in development database using admin context
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 <<-EOSQL
CREATE EXTENSION IF NOT EXISTS vector;
EOSQL

# Install pgvector extension in test database using admin context and grant schema ownership
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_TEST_DB" --set=ON_ERROR_STOP=1 <<-EOSQL
CREATE EXTENSION IF NOT EXISTS vector;
ALTER SCHEMA public OWNER TO $POSTGRES_TEST_USER;
GRANT ALL ON SCHEMA public TO $POSTGRES_TEST_USER;
EOSQL
