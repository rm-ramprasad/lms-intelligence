-- Creates the Glue Data Catalog database used by Athena. No crawler is used.
CREATE DATABASE IF NOT EXISTS analytics_poc
COMMENT 'Conversational BI POC V0.1: raw CSV tables and typed views';
