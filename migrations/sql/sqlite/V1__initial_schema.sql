CREATE TABLE user_account (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    last_entry TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    setting_limit INTEGER NOT NULL DEFAULT 5
);

CREATE TABLE setting (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    name VARCHAR(64) NOT NULL,
    public BOOLEAN NOT NULL DEFAULT FALSE,
    clean_empty BOOLEAN NOT NULL DEFAULT FALSE,
    convert_to_utf BOOLEAN NOT NULL DEFAULT FALSE,
    use_comma_separator BOOLEAN NOT NULL DEFAULT FALSE,
    expect_feet BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_setting_owner_id ON setting(owner_id);
CREATE INDEX ix_setting_public_name ON setting(public, name);

CREATE TABLE fandom (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    setting_id INTEGER NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    name VARCHAR(64) NOT NULL,
    active BOOLEAN NOT NULL DEFAULT FALSE,
    separation INTEGER NOT NULL DEFAULT 1,
    support_value_1 BOOLEAN NOT NULL DEFAULT FALSE,
    support_value_2 BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_fandom_setting_id ON fandom(setting_id);

CREATE TABLE unit_conversion (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    setting_id INTEGER NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    phrase_from VARCHAR(64) NOT NULL,
    phrase_to VARCHAR(64) NOT NULL,
    conversion FLOAT NOT NULL,
    can_be_word BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_unit_conversion_setting_id ON unit_conversion(setting_id);

CREATE TABLE phrase_conversion (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    setting_id INTEGER NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    phrase_from VARCHAR(64) NOT NULL,
    phrase_to VARCHAR(64) NOT NULL,
    direct BOOLEAN NOT NULL DEFAULT FALSE,
    mutations BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ix_phrase_conversion_setting_id ON phrase_conversion(setting_id);

CREATE TABLE image_conversion (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    setting_id INTEGER NOT NULL REFERENCES setting(id) ON DELETE CASCADE,
    phrase VARCHAR(64) NOT NULL,
    separation INTEGER NOT NULL DEFAULT 1,
    explanation VARCHAR(64) NOT NULL,
    mutations BOOLEAN NOT NULL DEFAULT FALSE,
    images TEXT NOT NULL
);
CREATE INDEX ix_image_conversion_setting_id ON image_conversion(setting_id);

CREATE TABLE processing_job (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    setting_id INTEGER NOT NULL REFERENCES setting(id) ON DELETE RESTRICT,
    status VARCHAR(16) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    cancellation_requested BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    heartbeat_at TIMESTAMP,
    error_message TEXT
);
CREATE INDEX ix_processing_job_created_status ON processing_job(created_at, status);
CREATE INDEX ix_processing_job_user_id ON processing_job(user_id);
CREATE UNIQUE INDEX ux_processing_job_active_user ON processing_job(user_id)
    WHERE status IN ('queued', 'running');
