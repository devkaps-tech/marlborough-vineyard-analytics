-- Marlborough Vineyard Growth Intelligence -- warehouse schema
-- Run once against Supabase (requires the postgis extension, enabled via
-- Database > Extensions in the dashboard) before the loader script runs.

create extension if not exists postgis;

create table if not exists vineyard_parcels (
    parcel_id        text primary key,
    survey_year       int not null,
    survey_date       date not null,
    area_hectares     numeric not null,
    subregion         text not null,
    geom              geometry(MultiPolygon, 4326) not null,
    loaded_at         timestamptz not null default now()
);
create index if not exists idx_vineyard_parcels_geom on vineyard_parcels using gist (geom);
create index if not exists idx_vineyard_parcels_year on vineyard_parcels (survey_year);

create table if not exists yearly_summary (
    survey_year               int primary key,
    total_parcels             int not null,
    total_area_ha             numeric not null,
    avg_parcel_size_ha        numeric not null,
    prior_survey_year         int,
    years_since_prior_survey  int,
    area_added_ha             numeric,
    pct_growth                numeric,
    loaded_at                 timestamptz not null default now()
);

create table if not exists subregion_summary (
    survey_year     int not null,
    subregion       text not null,
    total_parcels   int not null,
    total_area_ha   numeric not null,
    loaded_at       timestamptz not null default now(),
    primary key (survey_year, subregion)
);
