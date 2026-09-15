CREATE TABLE login_failure (
    username TEXT NOT NULL,
    ip TEXT NOT NULL,
    failure_count INTEGER NOT NULL CHECK (failure_count >= 1),
    window_started_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (username, ip)
);
CREATE INDEX ix_login_failure_window_started_at ON login_failure(window_started_at);
