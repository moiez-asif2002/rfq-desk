-- Runs once on first container start.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE DATABASE rfq_desk_test OWNER rfq;
\connect rfq_desk_test
CREATE EXTENSION IF NOT EXISTS pg_trgm;
