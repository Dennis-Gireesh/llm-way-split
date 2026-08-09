from __future__ import annotations


class WaySplitError(Exception):
    """Base class for errors safe to translate into user-facing messages."""


class ConfigurationError(WaySplitError):
    pass


class DocumentError(WaySplitError):
    pass


class ModelEndpointError(WaySplitError):
    pass


class ModelResponseError(WaySplitError):
    pass


class DuplicateStatementError(WaySplitError):
    def __init__(self, run_id: str) -> None:
        super().__init__("This exact statement has already been processed.")
        self.run_id = run_id


class PostingBlockedError(WaySplitError):
    pass


class ConfirmationError(WaySplitError):
    pass


class DestinationError(WaySplitError):
    pass
