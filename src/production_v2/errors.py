"""Small, stable error taxonomy for the isolated v2 production stack."""
from __future__ import annotations


class V2Error(Exception):
    """Base class. ``code`` is safe to put in machine-readable reports."""

    code = "V2_ERROR"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})

    def as_dict(self) -> dict:
        return {"code": self.code, "error": self.message, "details": self.details}


class DependencyError(V2Error):
    code = "DEPENDENCY_MISSING"


class ProviderError(V2Error):
    code = "PROVIDER_FAILURE"


class ResearchRejected(V2Error):
    code = "RESEARCH_REJECTED"


class ValidationError(V2Error):
    code = "VALIDATION_FAILED"


class RenderError(V2Error):
    code = "RENDER_FAILED"


class QualityRejected(V2Error):
    code = "QUALITY_REJECTED"


class UploadBlocked(V2Error):
    code = "UPLOAD_BLOCKED"


class PublicationError(V2Error):
    code = "PUBLICATION_FAILED"
