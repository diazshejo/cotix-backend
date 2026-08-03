class IntegrationError(Exception):
    error_code = "integration_error"


class IntegrationConnectionError(IntegrationError):
    error_code = "connection_error"


class IntegrationValidationError(IntegrationError):
    error_code = "validation_error"


class IntegrationMappingError(IntegrationError):
    error_code = "mapping_error"
