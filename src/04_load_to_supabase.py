"""
Step 4: Load the cleaned, enriched data into Supabase Postgres/PostGIS.

Requires a .env file (copy .env.example -> .env and fill in DATABASE_URL --
use the Session pooler connection string from Supabase, port 5432) and
the postgis extension already enabled on the project, and sql/schema.sql
already applied.

Usage:
    python3 src/04_load_to_supabase.py
"""
import os
from pathlib import Path

import geopandas as gpd
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "data" / "processed"

load_dotenv(BASE_DIR / ".env")
DATABASE_URL = os.environ.get("DATABASE_URL")


def get_engine():
    if not DATABASE_URL or "[YOUR-PASSWORD]" in DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill in "
            "your real Supabase connection string first."
        )
    return create_engine(DATABASE_URL)


def apply_schema(engine):
    schema_sql = (BASE_DIR / "sql" / "schema.sql").read_text()
    with engine.begin() as conn:
        for statement in schema_sql.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))
    print("Schema applied (tables + indexes created if not already present).")


def load_parcels(engine):
    gdf = gpd.read_parquet(PROCESSED_DIR / "vineyard_parcels.parquet")
    gdf = gdf.rename(columns={"geometry": "geom"}).set_geometry("geom")
    # cast to the schema's declared type
    gdf["survey_date"] = pd.to_datetime(gdf["survey_date"]).dt.date

    gdf.to_postgis("vineyard_parcels", engine, if_exists="append", index=False)
    print(f"Loaded {len(gdf)} parcels into vineyard_parcels.")


def load_summary_tables(engine):
    yearly = pd.read_csv(PROCESSED_DIR / "yearly_summary.csv")
    yearly.to_sql("yearly_summary", engine, if_exists="append", index=False)
    print(f"Loaded {len(yearly)} rows into yearly_summary.")

    subregion = pd.read_csv(PROCESSED_DIR / "subregion_summary.csv")
    subregion.to_sql("subregion_summary", engine, if_exists="append", index=False)
    print(f"Loaded {len(subregion)} rows into subregion_summary.")


def main():
    engine = get_engine()
    apply_schema(engine)

    with engine.begin() as conn:
        # idempotent re-runs: clear existing rows before reloading
        conn.execute(text("truncate vineyard_parcels, yearly_summary, subregion_summary"))

    load_parcels(engine)
    load_summary_tables(engine)
    print("\nDone. Data is live in Supabase.")


if __name__ == "__main__":
    main()
