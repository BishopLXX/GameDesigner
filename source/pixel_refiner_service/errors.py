from __future__ import annotations


class PixelRefinerServiceError(RuntimeError):
    status_code = 400


class ModelPackageError(PixelRefinerServiceError):
    status_code = 503


class BackendUnavailableError(ModelPackageError):
    status_code = 503


class RequestValidationError(PixelRefinerServiceError):
    status_code = 400
