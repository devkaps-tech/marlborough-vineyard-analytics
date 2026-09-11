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
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    BASE_DIR,
    FOOTPRINT_BY_YEAR,
    PARCELS_ENRICHED,
    PROCESSED_DIR,
    SUBREGION_SUMMARY,
    TRANSITIONS,
    YEARLY_SUMMARY,
    require,
)

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
    require(PARCELS_ENRICHED)
    gdf = gpd.read_parquet(PARCELS_ENRICHED)
    gdf = gdf.rename(columns={"geometry": "geom"}).set_geometry("geom")
    # cast to the schema's declared type
    gdf["survey_date"] = pd.to_datetime(gdf["survey_date"]).dt.date

    gdf.to_postgis("vineyard_parcels", engine, if_exists="append", index=False)
    print(f"Loaded {len(gdf)} parcels into vineyard_parcels.")


def load_summary_tables(engine):
    yearly = pd.read_csv(YEARLY_SUMMARY)
    yearly.to_sql("yearly_summary", engine, if_exists="append", index=False)
    print(f"Loaded {len(yearly)} rows into yearly_summary.")

    subregion = pd.read_csv(SUBREGION_SUMMARY)
    subregion.to_sql("subregion_summary", engine, if_exists="append", index=False)
    print(f"Loaded {len(subregion)} rows into subregion_summary.")

    # step-05 outputs: the overlay-derived footprint and churn series
    footprint = pd.read_csv(FOOTPRINT_BY_YEAR)
    footprint.to_sql("footprint_by_year", engine, if_exists="append", index=False)
    print(f"Loaded {len(footprint)} rows into footprint_by_year.")

    transitions = pd.read_csv(TRANSITIONS)
    # keep only the columns the table declares; the forensics merge adds more
    cols = ["prior_survey_year", "survey_year", "years_in_interval",
            "footprint_prior_ha", "footprint_ha", "new_ha", "retired_ha",
            "persisting_ha", "net_change_ha", "new_ha_per_year",
            "retired_ha_per_year", "annualised_pct_growth"]
    transitions[[c for c in cols if c in transitions.columns]].to_sql(
        "transitions", engine, if_exists="append", index=False)
    print(f"Loaded {len(transitions)} rows into transitions.")


def main():
    engine = get_engine()
    apply_schema(engine)

    with engine.begin() as conn:
        # idempotent re-runs: clear existing rows before reloading
        conn.execute(text("truncate vineyard_parcels, yearly_summary, "
                          "subregion_summary, footprint_by_year, transitions"))

    load_parcels(engine)
    load_summary_tables(engine)
    print("\nDone. Data is live in Supabase.")


if __name__ == "__main__":
    main()
