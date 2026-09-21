"""Operational GPU failures, distinct from caller validation/admission errors."""


class GpuOperationError(RuntimeError):
    pass
