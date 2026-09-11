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

-- Step-05 outputs: the overlay-derived series. `footprint_by_year` is the
-- dissolved area per survey (the honest area measure); `transitions` is the
-- set-difference of consecutive surveys.
--
-- NOTE on retired_ha: this is area present in one survey and absent in the
-- next. It is NOT confirmed vine removal -- part of it is the same ground
-- re-digitised at a different boundary. The split is a modelling choice and
-- lives in data/processed/stats/retirement_classification.csv, deliberately
-- not in this table.
create table if not exists footprint_by_year (
    survey_year    int primary key,
    footprint_ha   numeric not null,
    naive_sum_ha   numeric not null,
    overlap_ha     numeric,
    overlap_pct    numeric,
    loaded_at      timestamptz not null default now()
);

create table if not exists transitions (
    prior_survey_year      int not null,
    survey_year            int not null,
    years_in_interval      int not null,
    footprint_prior_ha     numeric not null,
    footprint_ha           numeric not null,
    new_ha                 numeric not null,
    retired_ha             numeric not null,
    persisting_ha          numeric not null,
    net_change_ha          numeric not null,
    new_ha_per_year        numeric,
    retired_ha_per_year    numeric,
    annualised_pct_growth  numeric,
    loaded_at              timestamptz not null default now(),
    primary key (prior_survey_year, survey_year)
);
