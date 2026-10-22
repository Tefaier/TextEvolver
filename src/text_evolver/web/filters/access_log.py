import logging


class AuthenticationQueryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            path = record.args[2]
            if isinstance(path, str) and path.startswith(("/auth/", "/reset-password")):
                args = list(record.args)
                args[2] = path.split("?", 1)[0]
                record.args = tuple(args)
        return True


def configure_access_log() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, AuthenticationQueryFilter) for item in logger.filters):
        logger.addFilter(AuthenticationQueryFilter())
