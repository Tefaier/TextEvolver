CREATE TABLE user_account (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    last_entry TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    setting_limit INTEGER NOT NULL DEFAULT 5
);

CREATE TABLE user_password (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES user_account(id) ON DELETE CASCADE,
    encoded_password BLOB NOT NULL CHECK (length(encoded_password) >= 16),
    nonce BLOB NOT NULL CHECK (length(nonce) = 12),
    key_version INTEGER NOT NULL CHECK (key_version >= 1),
    CONSTRAINT uq_user_password_nonce UNIQUE (nonce)
);
CREATE INDEX ix_user_password_key_version_id ON user_password(key_version, id);

CREATE TABLE setting (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    public BOOLEAN NOT NULL DEFAULT FALSE,
    clean_empty BOOLEAN NOT NULL DEFAULT FALSE,
    convert_to_utf BOOLEAN NOT NULL DEFAULT FALSE,
    use_comma_separator BOOLEAN NOT NULL DEFAULT FALSE,
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

CREATE TABLE processing_job (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    setting_id INTEGER NOT NULL REFERENCES setting(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
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
