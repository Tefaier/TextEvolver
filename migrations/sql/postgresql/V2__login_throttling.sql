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
CREATE INDEX ix_login_failure_by_account_last_failure_at
    ON login_failure_by_account(last_failure_at);
