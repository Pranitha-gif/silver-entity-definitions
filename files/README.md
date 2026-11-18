# silver-entity-definitions

Simple base project for the semantic layer of a Medallion architecture.
Each Silver entity is defined once in JSON, and the code builds the Silver table from Bronze using that definition.

## Structure
    entities/   one JSON definition per entity (customer, order)
    bronze/     sample raw CSV files
    silver_entity_definitions.py

## Run
    python silver_entity_definitions.py
    python silver_entity_definitions.py --entities entities --db silver.db

## A definition contains
entity name, version, Bronze source, Silver table, primary key, and columns
(name, source column, type: int/float/string/date, required, rules: trim/lower/upper/title).

## Check results
    sqlite3 silver.db "SELECT * FROM silver_customer;"
    sqlite3 silver.db "SELECT entity, reason FROM silver_rejects;"
    sqlite3 silver.db "SELECT entity, version, silver_table FROM entity_registry;"

## Ideas to extend
Add a Gold metric view on top, foreign-key checks (order.customer_id -> customer), SCD history, YAML definitions.
