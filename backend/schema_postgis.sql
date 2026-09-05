-- ============================================================================
-- Vaani - Local Air-Gapped Storage & PostgreSQL + PostGIS Spatial Schema
-- Zero Cloud in Hot Path - All spatial queries and storage operate offline
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS postgis;

-- 1. Calls: master table of all incoming emergency calls and uploaded audio
CREATE TABLE IF NOT EXISTS calls (
    id VARCHAR(64) PRIMARY KEY,
    received_at DOUBLE PRECISION NOT NULL,
    transcript TEXT,
    text_english TEXT,
    language VARCHAR(16),
    audio_path VARCHAR(512),
    source VARCHAR(32) DEFAULT 'upload',
    from_number VARCHAR(32),
    status VARCHAR(32) DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Locations: spatial table with PostGIS geometry and geocoded landmarks
CREATE TABLE IF NOT EXISTS locations (
    id SERIAL PRIMARY KEY,
    call_id VARCHAR(64) NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    geom GEOMETRY(Point, 4326),
    error_radius_m DOUBLE PRECISION DEFAULT 50.0,
    source VARCHAR(32) DEFAULT 'unknown',
    landmark VARCHAR(255),
    landmark_confidence DOUBLE PRECISION,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_locations_geom ON locations USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_locations_call_id ON locations(call_id);

-- 3. Extractions: structured triage fields extracted by spotter & LLM
CREATE TABLE IF NOT EXISTS extractions (
    id SERIAL PRIMARY KEY,
    call_id VARCHAR(64) NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    hazard_class VARCHAR(32) NOT NULL DEFAULT 'other',
    severity_band INTEGER NOT NULL DEFAULT 1,
    people_affected VARCHAR(16) DEFAULT '1',
    trapped BOOLEAN DEFAULT FALSE,
    medical_critical BOOLEAN DEFAULT FALSE,
    vulnerable TEXT, -- JSON array of tags e.g. ["child", "elderly"]
    access_constraint VARCHAR(32) DEFAULT 'unknown',
    extractor VARCHAR(32) DEFAULT 'keyword',
    confidence VARCHAR(16) DEFAULT 'medium',
    note TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_extractions_call_id ON extractions(call_id);
CREATE INDEX IF NOT EXISTS idx_extractions_band ON extractions(severity_band);

-- 4. Exposure Scores: terrain and flood susceptibility computed from Copernicus DEM & HAND
CREATE TABLE IF NOT EXISTS exposure_scores (
    id SERIAL PRIMARY KEY,
    call_id VARCHAR(64) NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    hand_m DOUBLE PRECISION,
    hand_median_m DOUBLE PRECISION,
    p50 DOUBLE PRECISION,
    p75 DOUBLE PRECISION,
    p90 DOUBLE PRECISION,
    in_aoi BOOLEAN DEFAULT TRUE,
    wide_error BOOLEAN DEFAULT FALSE,
    terrain_edge BOOLEAN DEFAULT FALSE,
    model_ver VARCHAR(64),
    computed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_exposure_scores_call_id ON exposure_scores(call_id);

-- 5. Rankings: dynamic triage positions computed by the fusion ranker
CREATE TABLE IF NOT EXISTS rankings (
    id SERIAL PRIMARY KEY,
    call_id VARCHAR(64) NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    rank_pos INTEGER NOT NULL,
    band INTEGER NOT NULL,
    within_score DOUBLE PRECISION NOT NULL,
    override_val INTEGER DEFAULT 0,
    reason TEXT,
    ranked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rankings_call_id ON rankings(call_id);
CREATE INDEX IF NOT EXISTS idx_rankings_pos ON rankings(rank_pos);

-- 6. Audit Log: immutable audit trail for operator accountability
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
    call_id VARCHAR(64),
    action VARCHAR(64) NOT NULL,
    actor VARCHAR(64) NOT NULL,
    payload TEXT NOT NULL,
    logged_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_log_call_id ON audit_log(call_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action);
