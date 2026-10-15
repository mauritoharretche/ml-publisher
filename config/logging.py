import logging
import re


class OAuthCallbackRedactionFilter(logging.Filter):
    """Evita que el servidor de desarrollo registre code/state en la URL."""
    pattern = re.compile(r"(/mercadolibre/callback/)\?[^\s\"']+")

    def filter(self, record):
        def redact(value):
            return self.pattern.sub(r"\1?[REDACTED]", value) if isinstance(value, str) else value

        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        return True
