"""Stable errors raised by the chart planning pipeline."""


class ChartValidationError(ValueError):
    """A ChartPlan or DatasetArtifact failed deterministic validation."""

    def __init__(self, code: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
