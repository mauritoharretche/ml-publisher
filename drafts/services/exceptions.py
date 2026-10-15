class MercadoLibreError(Exception):
    """Base error safe to display; deliberately holds no sensitive payloads."""


class MercadoLibreConfigurationError(MercadoLibreError):
    pass


class MercadoLibreNetworkError(MercadoLibreError):
    pass


class MercadoLibreAuthorizationError(MercadoLibreError):
    pass


class MercadoLibreRateLimitError(MercadoLibreError):
    pass


class MercadoLibreTokenRefreshRequired(MercadoLibreAuthorizationError):
    pass
