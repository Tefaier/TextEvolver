CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE user_account (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT NOT NULL UNIQUE,
    last_entry TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    language TEXT CHECK (language = 'ru' OR language = 'en'),
    setting_limit INTEGER NOT NULL DEFAULT 5
);

CREATE TABLE user_password (
    user_id UUID PRIMARY KEY REFERENCES user_account(id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL
);

CREATE TABLE setting (
    id BIGSERIAL PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    public BOOLEAN NOT NULL DEFAULT FALSE,
    clean_empty BOOLEAN NOT NULL DEFAULT FALSE,
    convert_to_utf BOOLEAN NOT NULL DEFAULT FALSE,
    use_comma_separator BOOLEAN NOT NULL DEFAULT FALSE,
    expect_feet BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_setting_owner_id ON setting(owner_id);
CREATE INDEX ix_setting_public_name_trgm
    ON setting USING gin (name gin_trgm_ops)
    WHERE public;
CREATE INDEX ix_setting_name_trgm
    ON setting USING gin (name gin_trgm_ops);
CREATE INDEX ix_setting_public_order
    ON setting(name, id)
    WHERE public;

CREATE TABLE fandom (
    id BIGSERIAL PRIMARY KEY,
    setting_id BIGINT NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    separation INTEGER NOT NULL DEFAULT 1,
    support_value_1 BOOLEAN NOT NULL DEFAULT FALSE,
    support_value_2 BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_fandom_setting_id ON fandom(setting_id);

CREATE TABLE unit_conversion (
    id BIGSERIAL PRIMARY KEY,
    setting_id BIGINT NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    phrase_from TEXT NOT NULL,
    phrase_to TEXT NOT NULL,
    conversion DOUBLE PRECISION NOT NULL,
    can_be_word BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_unit_conversion_setting_id ON unit_conversion(setting_id);

CREATE TABLE phrase_conversion (
    id BIGSERIAL PRIMARY KEY,
    setting_id BIGINT NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    phrase_from TEXT NOT NULL,
    phrase_to TEXT NOT NULL,
    direct BOOLEAN NOT NULL DEFAULT FALSE,
    mutations BOOLEAN NOT NULL DEFAULT FALSE,
    regex BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_phrase_conversion_setting_id ON phrase_conversion(setting_id);

CREATE TABLE image_conversion (
    id BIGSERIAL PRIMARY KEY,
    setting_id BIGINT NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    phrase TEXT NOT NULL,
    separation INTEGER NOT NULL DEFAULT 1,
    explanation TEXT NOT NULL,
    mutations BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_image_conversion_setting_id ON image_conversion(setting_id);

CREATE TABLE image_conversion_file (
    id BIGSERIAL PRIMARY KEY,
    image_conversion_id BIGINT NOT NULL REFERENCES image_conversion(id) ON DELETE CASCADE,
    object_key TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    position INTEGER NOT NULL CHECK (position >= 0),
    CONSTRAINT uq_image_conversion_file_object_key UNIQUE (object_key),
    CONSTRAINT uq_image_conversion_file_position UNIQUE (image_conversion_id, position)
);

CREATE TABLE processing_job (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    setting_id BIGINT NOT NULL REFERENCES setting(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status = 'queued' OR status = 'running' OR status = 'completed' OR status = 'failed' OR status = 'cancelled'),
    cancellation_requested BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    error_message TEXT
);
CREATE INDEX ix_processing_job_queued_order
    ON processing_job(created_at, id)
    WHERE status = 'queued';
CREATE INDEX ix_processing_job_setting_status ON processing_job(setting_id, status);
CREATE INDEX ix_processing_job_user_id ON processing_job(user_id);
CREATE UNIQUE INDEX ux_processing_job_active_user ON processing_job(user_id)
    WHERE status IN ('queued', 'running');

CREATE TABLE login_failure (
    username TEXT NOT NULL,
    ip TEXT NOT NULL,
    failure_count INTEGER NOT NULL CHECK (failure_count >= 1),
    window_started_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (username, ip)
);
CREATE INDEX ix_login_failure_window_started_at ON login_failure(window_started_at);

CREATE TABLE login_failure_by_account (
    username TEXT NOT NULL PRIMARY KEY,
    failure_score INTEGER NOT NULL CHECK (failure_score >= 1),
    last_failure_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_login_failure_by_account_last_failure_at ON login_failure_by_account(last_failure_at);

CREATE TABLE browser_session (
    token_hash TEXT NOT NULL PRIMARY KEY,
    user_id UUID REFERENCES user_account(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    authenticated_at TIMESTAMPTZ,
    authentication_method TEXT
);
CREATE INDEX ix_browser_session_user_id ON browser_session(user_id);
CREATE INDEX ix_browser_session_expires_at ON browser_session(expires_at);

CREATE TABLE google_identity (
    user_id UUID PRIMARY KEY REFERENCES user_account(id) ON DELETE CASCADE,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    CONSTRAINT uq_google_identity UNIQUE (issuer, subject)
);

CREATE TABLE passkey (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    credential_id TEXT NOT NULL UNIQUE,
    public_key TEXT NOT NULL,
    sign_count BIGINT NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    transports TEXT NOT NULL DEFAULT '[]',
    backed_up BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ
);
CREATE INDEX ix_passkey_user_id ON passkey(user_id);

CREATE TABLE auth_flow (
    id BIGSERIAL PRIMARY KEY,
    browser_session_token_hash TEXT NOT NULL REFERENCES browser_session(token_hash) ON UPDATE CASCADE ON DELETE CASCADE,
    user_id UUID REFERENCES user_account(id) ON DELETE CASCADE,
    passkey_id BIGINT REFERENCES passkey(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    data TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX uq_auth_flow_session_purpose ON auth_flow(browser_session_token_hash, purpose);
CREATE INDEX ix_auth_flow_expires_at ON auth_flow(expires_at);
CREATE INDEX ix_auth_flow_browser_session_token_hash ON auth_flow(browser_session_token_hash);
CREATE INDEX ix_auth_flow_user_id ON auth_flow(user_id);

CREATE TABLE browser_session_flash (
    id BIGSERIAL PRIMARY KEY,
    browser_session_token_hash TEXT NOT NULL REFERENCES browser_session(token_hash) ON UPDATE CASCADE ON DELETE CASCADE,
    category TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX ix_browser_session_flash_token_hash ON browser_session_flash(browser_session_token_hash);
