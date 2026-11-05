CREATE TABLE user_account (
    id TEXT NOT NULL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    last_entry TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    language TEXT CHECK (language = 'ru' OR language = 'en'),
    setting_limit INTEGER NOT NULL DEFAULT 5
);

CREATE TABLE user_password (
    user_id TEXT NOT NULL PRIMARY KEY REFERENCES user_account(id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL
);

CREATE TABLE setting (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    owner_id TEXT NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    public BOOLEAN NOT NULL DEFAULT FALSE,
    clean_empty BOOLEAN NOT NULL DEFAULT FALSE,
    convert_to_utf BOOLEAN NOT NULL DEFAULT FALSE,
    use_comma_separator BOOLEAN NOT NULL DEFAULT FALSE,
    deletion_requested BOOLEAN NOT NULL DEFAULT FALSE,
    expect_feet BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_setting_owner_id ON setting(owner_id);
-- SQLite has no pg_trgm or GIN equivalent for the PostgreSQL substring-search indexes.
CREATE INDEX ix_setting_public_order
    ON setting(name, id)
    WHERE public;

CREATE TABLE fandom (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    setting_id INTEGER NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    separation INTEGER NOT NULL DEFAULT 1,
    support_value_1 BOOLEAN NOT NULL DEFAULT FALSE,
    support_value_2 BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_fandom_setting_id ON fandom(setting_id);

CREATE TABLE unit_conversion (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    setting_id INTEGER NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    phrase_from TEXT NOT NULL,
    phrase_to TEXT NOT NULL,
    conversion FLOAT NOT NULL,
    can_be_word BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_unit_conversion_setting_id ON unit_conversion(setting_id);

CREATE TABLE phrase_conversion (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    setting_id INTEGER NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    phrase_from TEXT NOT NULL,
    phrase_to TEXT NOT NULL,
    direct BOOLEAN NOT NULL DEFAULT FALSE,
    mutations BOOLEAN NOT NULL DEFAULT FALSE,
    regex BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_phrase_conversion_setting_id ON phrase_conversion(setting_id);

CREATE TABLE image_conversion (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    setting_id INTEGER NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    phrase TEXT NOT NULL,
    separation INTEGER NOT NULL DEFAULT 1,
    explanation TEXT NOT NULL,
    mutations BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_image_conversion_setting_id ON image_conversion(setting_id);

CREATE TABLE image_conversion_file (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    image_conversion_id INTEGER NOT NULL REFERENCES image_conversion(id) ON DELETE CASCADE,
    object_key TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    position INTEGER NOT NULL CHECK (position >= 0),
    CONSTRAINT uq_image_conversion_file_object_key UNIQUE (object_key),
    CONSTRAINT uq_image_conversion_file_position UNIQUE (image_conversion_id, position)
);

CREATE TABLE setting_image_deletion (
    object_key TEXT NOT NULL PRIMARY KEY
);

CREATE TABLE processing_job (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    setting_id INTEGER REFERENCES setting(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status = 'queued' OR status = 'running' OR status = 'completed' OR status = 'failed' OR status = 'cancelled'),
    cancellation_requested BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    heartbeat_at TIMESTAMP,
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
    window_started_at TIMESTAMP NOT NULL,
    PRIMARY KEY (username, ip)
);
CREATE INDEX ix_login_failure_window_started_at ON login_failure(window_started_at);

CREATE TABLE login_failure_by_account (
    username TEXT NOT NULL PRIMARY KEY,
    failure_score INTEGER NOT NULL CHECK (failure_score >= 1),
    last_failure_at TIMESTAMP NOT NULL
);
CREATE INDEX ix_login_failure_by_account_last_failure_at
    ON login_failure_by_account(last_failure_at);

CREATE TABLE browser_session (
    token_hash TEXT NOT NULL PRIMARY KEY,
    user_id TEXT REFERENCES user_account(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP,
    authenticated_at TIMESTAMP,
    authentication_method TEXT
);
CREATE INDEX ix_browser_session_user_id ON browser_session(user_id);
CREATE INDEX ix_browser_session_expires_at ON browser_session(expires_at);

CREATE TABLE google_identity (
    user_id TEXT NOT NULL PRIMARY KEY REFERENCES user_account(id) ON DELETE CASCADE,
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    CONSTRAINT uq_google_identity UNIQUE (issuer, subject)
);

CREATE TABLE passkey (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    credential_id TEXT NOT NULL UNIQUE,
    public_key TEXT NOT NULL,
    sign_count BIGINT NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    transports TEXT NOT NULL DEFAULT '[]',
    backed_up BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL,
    last_used_at TIMESTAMP
);
CREATE INDEX ix_passkey_user_id ON passkey(user_id);

CREATE TABLE auth_flow (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    browser_session_token_hash TEXT NOT NULL REFERENCES browser_session(token_hash) ON UPDATE CASCADE ON DELETE CASCADE,
    user_id TEXT REFERENCES user_account(id) ON DELETE CASCADE,
    passkey_id INTEGER REFERENCES passkey(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    data TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL
);
CREATE UNIQUE INDEX uq_auth_flow_session_purpose ON auth_flow(browser_session_token_hash, purpose);
CREATE INDEX ix_auth_flow_expires_at ON auth_flow(expires_at);
CREATE INDEX ix_auth_flow_browser_session_token_hash ON auth_flow(browser_session_token_hash);
CREATE INDEX ix_auth_flow_user_id ON auth_flow(user_id);

CREATE TABLE browser_session_flash (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    browser_session_token_hash TEXT NOT NULL REFERENCES browser_session(token_hash) ON UPDATE CASCADE ON DELETE CASCADE,
    category TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX ix_browser_session_flash_token_hash ON browser_session_flash(browser_session_token_hash);
