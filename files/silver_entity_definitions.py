"""
silver-entity-definitions
-------------------------
Simple base project for the semantic layer of a Medallion architecture.

Each Silver entity (customer, order, ...) is defined once in a JSON file under
entities/: its Bronze source, columns, types, required flags, cleaning rules
and primary key. This script:

  1. loads and checks the entity definitions
  2. registers them (with version) in an entity_registry table
  3. reads the Bronze CSV, applies each definition and loads the Silver table
  4. sends rows that break the definition to silver_rejects with a reason

Usage:
    python silver_entity_definitions.py
    python silver_entity_definitions.py --entities entities --db silver.db
"""
import argparse
import csv
import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("silver-entity-definitions")

SQL_TYPES = {"int": "INTEGER", "float": "REAL", "string": "TEXT", "date": "TEXT"}

RULES = {
    "trim": str.strip,
    "lower": str.lower,
    "upper": str.upper,
    "title": str.title,
}


# ---------- definitions ----------
def load_definitions(folder: Path) -> list[dict]:
    defs = []
    for path in sorted(folder.glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        check_definition(d, path.name)
        defs.append(d)
    return defs


def check_definition(d: dict, name: str) -> None:
    for key in ("entity", "version", "bronze_source", "silver_table", "primary_key", "columns"):
        if key not in d:
            raise ValueError(f"{name}: missing '{key}'")
    col_names = [c["name"] for c in d["columns"]]
    if len(set(col_names)) != len(col_names):
        raise ValueError(f"{name}: duplicate column names")
    if d["primary_key"] not in col_names:
        raise ValueError(f"{name}: primary_key not in columns")
    for c in d["columns"]:
        if c["type"] not in SQL_TYPES:
            raise ValueError(f"{name}: unknown type '{c['type']}' on {c['name']}")
        for r in c.get("rules", []):
            if r not in RULES:
                raise ValueError(f"{name}: unknown rule '{r}' on {c['name']}")


# ---------- row conversion ----------
def convert(value, col: dict):
    """Apply rules and cast to the declared type. Raises ValueError if invalid."""
    text = "" if value is None else str(value)
    for rule in col.get("rules", []):
        text = RULES[rule](text)
    text = text.strip()
    if text == "":
        if col.get("required"):
            raise ValueError(f"{col['name']} is required")
        return None
    t = col["type"]
    try:
        if t == "int":
            return int(text)
        if t == "float":
            return float(text)
        if t == "date":
            return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{col['name']}: '{text}' is not a valid {t}") from None
    return text


def build_row(raw: dict, d: dict) -> dict:
    return {c["name"]: convert(raw.get(c["source"]), c) for c in d["columns"]}


# ---------- database ----------
def init_db(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entity_registry (
            entity TEXT, version INTEGER, description TEXT, silver_table TEXT,
            primary_key TEXT, definition_json TEXT, registered_at TEXT,
            PRIMARY KEY (entity, version));
        CREATE TABLE IF NOT EXISTS silver_rejects (
            entity TEXT, source_row TEXT, reason TEXT, rejected_at TEXT);
        """
    )


def register(conn, d: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO entity_registry VALUES (?,?,?,?,?,?,?)",
        (d["entity"], d["version"], d.get("description", ""), d["silver_table"],
         d["primary_key"], json.dumps(d), datetime.now(timezone.utc).isoformat()),
    )


def create_silver_table(conn, d: dict) -> None:
    cols = ", ".join(f'"{c["name"]}" {SQL_TYPES[c["type"]]}' for c in d["columns"])
    conn.execute(f'DROP TABLE IF EXISTS "{d["silver_table"]}"')
    conn.execute(
        f'CREATE TABLE "{d["silver_table"]}" ({cols}, _entity_version INTEGER, '
        f'_loaded_at TEXT, PRIMARY KEY ("{d["primary_key"]}"))'
    )


# ---------- build one entity ----------
def build_entity(conn, d: dict, base: Path) -> tuple[int, int]:
    source = base / d["bronze_source"]
    with source.open(newline="", encoding="utf-8-sig") as f:
        raw_rows = list(csv.DictReader(f))

    create_silver_table(conn, d)
    now = datetime.now(timezone.utc).isoformat()
    names = [c["name"] for c in d["columns"]]
    seen_keys, ok, bad = set(), 0, 0

    def reject(raw, reason):
        conn.execute("INSERT INTO silver_rejects VALUES (?,?,?,?)",
                     (d["entity"], json.dumps(raw), reason, now))

    for raw in raw_rows:
        try:
            row = build_row(raw, d)
            key = row[d["primary_key"]]
            if key in seen_keys:
                raise ValueError(f"duplicate {d['primary_key']} {key}")
            seen_keys.add(key)
        except ValueError as exc:
            reject(raw, str(exc))
            bad += 1
            continue
        conn.execute(
            f'INSERT INTO "{d["silver_table"]}" ({", ".join(chr(34)+n+chr(34) for n in names)}, '
            f'_entity_version, _loaded_at) VALUES ({", ".join("?" * (len(names) + 2))})',
            [row[n] for n in names] + [d["version"], now],
        )
        ok += 1
    return ok, bad


def run(entities: Path, db: Path) -> None:
    base = entities.parent
    defs = load_definitions(entities)
    conn = sqlite3.connect(db)
    init_db(conn)
    conn.execute("DELETE FROM silver_rejects")  # rejects reflect the latest run
    for d in defs:
        register(conn, d)
        ok, bad = build_entity(conn, d, base)
        log.info("%-9s v%s -> %s: %d loaded, %d rejected",
                 d["entity"], d["version"], d["silver_table"], ok, bad)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Silver entity definitions (Bronze CSV -> Silver SQLite)")
    ap.add_argument("--entities", default="entities", type=Path)
    ap.add_argument("--db", default="silver.db", type=Path)
    args = ap.parse_args()
    run(args.entities, args.db)
