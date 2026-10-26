"""All DDL, in the order it has to run. Idempotent: every statement is safe to
replay against a database that already has some or all of it.

`main.py` applies it; nothing here talks to a database or to AWS.

    EXTENSIONS  the Postgres extensions the rest of this depends on
    CORE        ingest_job, product, branch, price -- what the pipeline needs
    PORTAL      everything the backend adds: tenancy, alerts, uploads, catalogue
    PIPELINE    what the S3 -> SQS pipeline adds: inbox, outbox, day close
    INDEXES     built last, some of them expensive
"""
from __future__ import annotations

EXTENSIONS = """
CREATE EXTENSION IF NOT EXISTS cube;
CREATE EXTENSION IF NOT EXISTS earthdistance;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- unaccent() is STABLE, not IMMUTABLE (it resolves a text-search dictionary via
-- search_path), so it cannot be used in an index expression as-is. Pinning the
-- dictionary by name makes the wrapper honest about being IMMUTABLE.
CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
  RETURN public.unaccent('public.unaccent'::regdictionary, $1);
"""

# The pipeline's own tables. Unchanged in shape from the original bootstrap
# schema except `ingest_job.kind`, which is what lets an upload ride the same
# claim/parse/load path as a SEPA chain without pretending to be one.
CORE = r"""
CREATE TABLE IF NOT EXISTS ingest_job (
  job_id        BIGSERIAL PRIMARY KEY,
  feed_date     DATE        NOT NULL,
  chain_id      TEXT        NOT NULL,
  s3_key        TEXT        NOT NULL,
  kind          TEXT        NOT NULL DEFAULT 'sepa',
  status        TEXT        NOT NULL DEFAULT 'pending',
  worker_id     TEXT,
  claimed_at    TIMESTAMPTZ,
  attempts      INT         NOT NULL DEFAULT 0,
  rows_ok       INT,
  rows_rejected INT,
  last_error    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Column may already exist from before `kind` was added to the table above.
ALTER TABLE ingest_job ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'sepa';

DO $$ BEGIN
  ALTER TABLE ingest_job
    ADD CONSTRAINT ingest_job_kind_check CHECK (kind IN ('sepa', 'upload'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Idempotency for SEPA: one job per chain per day, so re-running the ingest
-- never duplicates work. Scoped to `kind = 'sepa'` because a merchant's own
-- uploads must NOT collapse: one company has one company_id, so under a
-- table-wide UNIQUE (feed_date, chain_id) a second upload for the same day
-- would overwrite the first one's job row -- and a rejected upload would then
-- read back the clean one's counts and report itself as done.
ALTER TABLE ingest_job DROP CONSTRAINT IF EXISTS ingest_job_feed_date_chain_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_job_sepa_feed_chain
  ON ingest_job (feed_date, chain_id) WHERE kind = 'sepa';

CREATE INDEX IF NOT EXISTS idx_job_pending ON ingest_job (status, created_at)
  WHERE status = 'pending';

-- The catalogue every chain's product resolves into. `product_name` (below)
-- keeps each chain's own wording; this is the canonical row derived from it.
CREATE TABLE IF NOT EXISTS product (
  ean          TEXT PRIMARY KEY,
  description  TEXT,
  brand        TEXT,
  presentation TEXT
);

CREATE TABLE IF NOT EXISTS branch (
  company_id  INT  NOT NULL,
  banner_id   INT  NOT NULL,
  branch_id   TEXT NOT NULL,
  banner_name TEXT,
  legal_name  TEXT,
  name        TEXT,
  kind        TEXT,
  province    TEXT,
  locality    TEXT,
  latitude    DOUBLE PRECISION,
  longitude   DOUBLE PRECISION,
  PRIMARY KEY (company_id, banner_id, branch_id)
);

-- No FK from `price` to `branch`/`product`: it is loaded by COPY at ~14.5M
-- rows a day, and two FK checks per row would dominate the load. sepa.read()
-- already rejects price rows whose branch key is unknown.
--
-- Partitioned by month on feed_date: at ~14.5M rows a day this is the one
-- table where dropping a month has to be a DDL statement rather than a DELETE
-- of 400M rows. The partition column only has to be *part* of the primary key,
-- not all of it, which is why the existing key works unchanged.
--
-- !! This only applies to a database the schema is applied to from scratch.
-- `CREATE TABLE IF NOT EXISTS` does NOT convert an existing non-partitioned
-- `price` that already has rows -- it sees the table and does nothing, and the
-- installation keeps the old shape with no warning. Migrating one with data in
-- it means creating the partitioned table under another name, copying, and
-- renaming; that procedure is out of scope here.
CREATE TABLE IF NOT EXISTS price (
  feed_date  DATE          NOT NULL,
  ean        TEXT          NOT NULL,
  company_id INT           NOT NULL,
  banner_id  INT           NOT NULL,
  branch_id  TEXT          NOT NULL,
  price      NUMERIC(12,2) NOT NULL,
  PRIMARY KEY (feed_date, ean, company_id, banner_id, branch_id)
) PARTITION BY RANGE (feed_date);

-- Makes every month inside the window exist, and prunes what falls out behind
-- it: from `retention_months` back through three months ahead, then detaches
-- and drops anything older than the retention.
--
-- It reaches *backwards* as well as forwards, which is not decoration. Without
-- a DEFAULT partition an INSERT outside every range fails, and the loads that
-- are not "today" are routine here: the staged SEPA archive is whatever Sunday
-- was downloaded (docs/LGDC_Levantar_la_infraestructura_a_mano.md), a merchant
-- uploads a price list
-- dated whenever they took stock, and reprocessing a chain's day replays an
-- old feed_date. Creating only the current month forward makes all three fail
-- with `no partition of relation price found for row`.
--
-- The lower bound matches what the purge below keeps, so the two cannot
-- disagree: a month is created exactly while it is not old enough to drop.
--
-- Still deliberately no DEFAULT partition. A feed_date outside the retention
-- window is a bug -- a typo'd year, a feed from two years ago -- and it has to
-- fail loudly rather than land in a catch-all nothing queries or prunes.
CREATE OR REPLACE FUNCTION price_maintain_partitions(retention_months int DEFAULT 6)
RETURNS void AS $$
DECLARE
    cursor_month date := date_trunc('month', now());
    partition_name text;
    lower_bound date;
    upper_bound date;
BEGIN
    FOR i IN -retention_months..3 LOOP
        lower_bound := cursor_month + (i || ' months')::interval;
        upper_bound := lower_bound + interval '1 month';
        partition_name := 'price_' || to_char(lower_bound, 'YYYY_MM');
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = partition_name) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF price FOR VALUES FROM (%L) TO (%L)',
                partition_name, lower_bound, upper_bound);
        END IF;
    END LOOP;

    FOR partition_name IN
        SELECT c.relname FROM pg_inherits i
        JOIN pg_class c ON c.oid = i.inhrelid
        JOIN pg_class p ON p.oid = i.inhparent
        WHERE p.relname = 'price'
          AND to_date(substring(c.relname FROM 'price_(\d{4}_\d{2})'), 'YYYY_MM')
              < cursor_month - (retention_months || ' months')::interval
    LOOP
        EXECUTE format('ALTER TABLE price DETACH PARTITION %I', partition_name);
        EXECUTE format('DROP TABLE %I', partition_name);
    END LOOP;
END;
$$ LANGUAGE plpgsql;
"""

# Everything the backend owns: who the clients are, which branches they watch,
# what they get alerted on, what they upload, and the derived tables that keep
# the expensive parts (geo, deltas, canonical naming) off the request path.
PORTAL = """
CREATE TABLE IF NOT EXISTS org (
  org_id     BIGSERIAL PRIMARY KEY,
  name       TEXT        NOT NULL,
  slug       TEXT        NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- `user` is reserved in SQL; naming it that would force quoting everywhere.
-- Cognito authenticates: a user is its token's `sub`, with no password here.
-- `org_id` is null from the first login until the user names its org.
CREATE TABLE IF NOT EXISTS app_user (
  user_id       BIGSERIAL   PRIMARY KEY,
  org_id        BIGINT      REFERENCES org (org_id) ON DELETE CASCADE,
  cognito_sub   TEXT,
  email         TEXT        NOT NULL UNIQUE,
  role          TEXT        NOT NULL DEFAULT 'member',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at TIMESTAMPTZ,
  CHECK (role IN ('owner', 'member'))
);

-- A database from before Cognito (ticket 05) had the own login's shape. Its
-- users keep their rows, with no sub: none of them can log in until the
-- same email signs up in Cognito, which links the row (app/api/queries.py).
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS cognito_sub TEXT;
ALTER TABLE app_user ALTER COLUMN org_id DROP NOT NULL;
ALTER TABLE app_user DROP COLUMN IF EXISTS password_hash;

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_cognito_sub ON app_user (cognito_sub);
CREATE INDEX IF NOT EXISTS idx_user_org ON app_user (org_id);

-- Which branches an org watches, and at what radius by default. `radius_km`
-- lives here so the competitive set always has a default without the caller
-- having to pass one.
CREATE TABLE IF NOT EXISTS org_branch (
  org_id     BIGINT       NOT NULL REFERENCES org (org_id) ON DELETE CASCADE,
  company_id INT          NOT NULL,
  banner_id  INT          NOT NULL,
  branch_id  TEXT         NOT NULL,
  label      TEXT,
  radius_km  NUMERIC(4,1) NOT NULL DEFAULT 5.0,
  created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, company_id, banner_id, branch_id),
  FOREIGN KEY (company_id, banner_id, branch_id)
    REFERENCES branch (company_id, banner_id, branch_id) ON DELETE CASCADE,
  -- the cap `branch_neighbor` is precomputed for; a bigger radius falls back
  -- to a live query the backend is free to refuse.
  CHECK (radius_km > 0 AND radius_km <= 10.0)
);

CREATE INDEX IF NOT EXISTS idx_org_branch_key
  ON org_branch (company_id, banner_id, branch_id);

-- SEPA company ids are small integers (2..47). A merchant that uploads its own
-- list gets an id from a disjoint high range, so to `app/batch/worker/main.py` -- which
-- does `int(job.chain_id)` and deletes-by-company on reprocess -- an upload is
-- just one more chain with one branch. This is what keeps the claim, the
-- idempotent delete and Job.partition working unmodified for uploads.
CREATE SEQUENCE IF NOT EXISTS merchant_company_id_seq START WITH 900000000;

CREATE TABLE IF NOT EXISTS merchant_source (
  company_id INT         PRIMARY KEY DEFAULT nextval('merchant_company_id_seq'),
  org_id     BIGINT      NOT NULL REFERENCES org (org_id) ON DELETE CASCADE,
  banner_id  INT         NOT NULL DEFAULT 1,
  branch_id  TEXT        NOT NULL DEFAULT '1',
  name       TEXT        NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id)
);

CREATE TABLE IF NOT EXISTS price_upload (
  upload_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id     BIGINT      NOT NULL REFERENCES org (org_id) ON DELETE CASCADE,
  company_id INT         NOT NULL REFERENCES merchant_source (company_id),
  feed_date  DATE        NOT NULL,
  s3_key     TEXT        NOT NULL,
  filename   TEXT,
  status     TEXT        NOT NULL DEFAULT 'awaiting_file',
  job_id     BIGINT      REFERENCES ingest_job (job_id) ON DELETE SET NULL,
  created_by BIGINT      REFERENCES app_user (user_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (status IN ('awaiting_file', 'queued', 'done', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_upload_org ON price_upload (org_id, created_at DESC);

-- A watch rule always anchors to one of the org's own branches. The three
-- filters the pitch names map directly: watch_product = "productos criticos",
-- target_company/banner/branch = "competidor puntual", threshold_pct = "umbral
-- de variacion". Leaving target_company_id null means "any competitor in
-- radius"; leaving watch_product empty means "every product".
CREATE TABLE IF NOT EXISTS watch_rule (
  rule_id           BIGSERIAL    PRIMARY KEY,
  org_id            BIGINT       NOT NULL REFERENCES org (org_id) ON DELETE CASCADE,
  name              TEXT         NOT NULL,
  company_id        INT          NOT NULL,
  banner_id         INT          NOT NULL,
  branch_id         TEXT         NOT NULL,
  radius_km         NUMERIC(4,1),
  target_company_id INT,
  target_banner_id  INT,
  target_branch_id  TEXT,
  threshold_pct     NUMERIC(6,2) NOT NULL DEFAULT 5.00,
  enabled           BOOLEAN      NOT NULL DEFAULT TRUE,
  created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
  FOREIGN KEY (org_id, company_id, banner_id, branch_id)
    REFERENCES org_branch (org_id, company_id, banner_id, branch_id) ON DELETE CASCADE,
  CHECK (threshold_pct >= 0),
  CHECK (target_company_id IS NOT NULL OR target_banner_id IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_rule_anchor
  ON watch_rule (company_id, banner_id, branch_id) WHERE enabled;
CREATE INDEX IF NOT EXISTS idx_rule_org ON watch_rule (org_id);

CREATE TABLE IF NOT EXISTS watch_product (
  rule_id BIGINT NOT NULL REFERENCES watch_rule (rule_id) ON DELETE CASCADE,
  ean     TEXT   NOT NULL,
  PRIMARY KEY (rule_id, ean)
);

-- One row per (branch, product) whose price moved between two consecutive
-- feed_dates for that chain. Facts, kept separate from `alert` (which is a
-- rule match against a fact) so re-tuning a rule does not require re-parsing.
CREATE TABLE IF NOT EXISTS price_change (
  feed_date          DATE          NOT NULL,
  ean                TEXT          NOT NULL,
  company_id         INT           NOT NULL,
  banner_id          INT           NOT NULL,
  branch_id          TEXT          NOT NULL,
  previous_feed_date DATE          NOT NULL,
  previous_price     NUMERIC(12,2) NOT NULL,
  price              NUMERIC(12,2) NOT NULL,
  delta_pct          NUMERIC(8,2)  NOT NULL,
  PRIMARY KEY (feed_date, ean, company_id, banner_id, branch_id)
);

CREATE INDEX IF NOT EXISTS idx_change_branch
  ON price_change (feed_date, company_id, banner_id, branch_id);

CREATE TABLE IF NOT EXISTS alert (
  alert_id       BIGSERIAL     PRIMARY KEY,
  org_id         BIGINT        NOT NULL REFERENCES org (org_id) ON DELETE CASCADE,
  rule_id        BIGINT        NOT NULL REFERENCES watch_rule (rule_id) ON DELETE CASCADE,
  feed_date      DATE          NOT NULL,
  ean            TEXT          NOT NULL,
  company_id     INT           NOT NULL,
  banner_id      INT           NOT NULL,
  branch_id      TEXT          NOT NULL,
  previous_price NUMERIC(12,2) NOT NULL,
  price          NUMERIC(12,2) NOT NULL,
  delta_pct      NUMERIC(8,2)  NOT NULL,
  created_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
  read_at        TIMESTAMPTZ,
  -- what makes the worker's per-(feed_date, chain) recompute an upsert
  -- instead of a source of duplicate alerts on reprocess.
  UNIQUE (rule_id, feed_date, ean, company_id, banner_id, branch_id)
);

CREATE INDEX IF NOT EXISTS idx_alert_feed   ON alert (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_unread ON alert (org_id, created_at DESC)
  WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_alert_source ON alert (feed_date, company_id);

-- Cross-company branch pairs within the radius any org can configure (see the
-- org_branch.radius_km cap). Depends only on `branch`, which changes at most
-- once a day, so it is worth precomputing rather than joined live: it takes
-- the geo predicate off both the comparison query and the alert pass.
CREATE TABLE IF NOT EXISTS branch_neighbor (
  company_id       INT  NOT NULL,
  banner_id        INT  NOT NULL,
  branch_id        TEXT NOT NULL,
  other_company_id INT  NOT NULL,
  other_banner_id  INT  NOT NULL,
  other_branch_id  TEXT NOT NULL,
  distance_m       INT  NOT NULL,
  PRIMARY KEY (company_id, banner_id, branch_id,
               other_company_id, other_banner_id, other_branch_id)
);

CREATE INDEX IF NOT EXISTS idx_neighbor_radius
  ON branch_neighbor (company_id, banner_id, branch_id, distance_m);
CREATE INDEX IF NOT EXISTS idx_neighbor_reverse
  ON branch_neighbor (other_company_id, other_banner_id, other_branch_id, distance_m);

-- What each chain calls the same barcode. `product` (in CORE) collapses this
-- to one row by majority vote; this keeps the evidence so a product's page
-- can show every chain's own wording.
CREATE TABLE IF NOT EXISTS product_name (
  ean          TEXT NOT NULL,
  company_id   INT  NOT NULL,
  description  TEXT,
  brand        TEXT,
  presentation TEXT,
  PRIMARY KEY (ean, company_id)
);

-- The search/display projection: accent-folded, lower-cased, size parsed out.
CREATE TABLE IF NOT EXISTS product_norm (
  ean        TEXT PRIMARY KEY REFERENCES product (ean) ON DELETE CASCADE,
  name_norm  TEXT        NOT NULL,
  brand_norm TEXT,
  size_value NUMERIC(10,3),
  size_unit  TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# What the TP3 pipeline adds on top of `ingest_job`, which stops being the
# queue (the jobs SQS is) and stays as the record the Pipeline screen shows.
PIPELINE = """
-- The inbox: one row per SQS message a worker has loaded, keyed by
-- `<s3 key>#<version id>`. Inserted with ON CONFLICT DO NOTHING in the same
-- transaction as the load, so either the prices and the message are both
-- recorded or neither is; a redelivered message finds its row and is dropped.
CREATE TABLE IF NOT EXISTS processed_message (
  message_key  TEXT        PRIMARY KEY,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The outbox: the chain-processed event, written in the same transaction as
-- the load and published to SNS after the commit. `sent_at` stays null until
-- the publish succeeds, so an event the worker could not publish is still
-- here to retry -- never lost, never announced before it was stored.
CREATE TABLE IF NOT EXISTS outbox (
  event_id   BIGSERIAL   PRIMARY KEY,
  payload    JSONB       NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outbox_unsent ON outbox (created_at)
  WHERE sent_at IS NULL;

-- One row per feed_date. The day-close Lambda wins the close with
-- `UPDATE ... SET closed_at = now() WHERE closed_at IS NULL`, so of several
-- invocations racing on the last chain of the day exactly one runs the
-- maintenance and the precompute.
CREATE TABLE IF NOT EXISTS feed_day (
  feed_date  DATE        PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at  TIMESTAMPTZ
);

-- The chains a day has taken in: marked by each chain's notice, and by the
-- close for every chain it found settled. A notice of a chain the day did
-- not have yet (one the backup cron closed without) reopens the day, which
-- closes again at once with it.
CREATE TABLE IF NOT EXISTS feed_day_chain (
  feed_date DATE        NOT NULL REFERENCES feed_day (feed_date) ON DELETE CASCADE,
  chain_id  TEXT        NOT NULL,
  marked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (feed_date, chain_id)
);
"""

# Built last. The comment on each one is the query pattern it exists for.
INDEXES = """
-- The portal's single hottest path: a branch's price list for a date. Column
-- order makes (feed_date, company_id) alone a usable prefix, ean last makes
-- the whole assortment one contiguous range, and INCLUDE makes it an
-- index-only scan -- the heap is never touched. Also what the worker's
-- reprocessing DELETE and the comparison query's per-branch scans hit.
CREATE INDEX IF NOT EXISTS idx_price_branch
  ON price (feed_date, company_id, banner_id, branch_id, ean) INCLUDE (price);

-- Geo radius without PostGIS: an expression index over cube+earthdistance.
-- STRICT means a branch missing coordinates just doesn't appear in it.
CREATE INDEX IF NOT EXISTS idx_branch_earth
  ON branch USING gist (ll_to_earth(latitude, longitude));

-- Trigram search over the normalised catalogue.
CREATE INDEX IF NOT EXISTS idx_product_norm_trgm
  ON product_norm USING gin (name_norm gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_product_norm_brand ON product_norm (brand_norm);
"""

# `idx_price_ean (feed_date, ean)` predates `idx_price_branch` and is now a
# strict prefix of the primary key `(feed_date, ean, company_id, banner_id,
# branch_id)` -- every query it could answer, the PK answers at the same cost.
# It is pure storage and COPY overhead once idx_price_branch exists.
DROP_REDUNDANT = """
DROP INDEX IF EXISTS idx_price_ean;
"""

ALL = (EXTENSIONS, CORE, PORTAL, PIPELINE, INDEXES, DROP_REDUNDANT)
