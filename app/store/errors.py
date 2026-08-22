"""Store-domain failures kept separate from provider execution failures."""


class StoreError(Exception):
    """Base failure at the persistence boundary."""


class StoreConflictError(StoreError, ValueError):
    """An immutable identity was replayed with incompatible facts."""


class StoreLineageError(StoreError, ValueError):
    """A persisted relationship is dangling or ownership-incompatible."""


class StorePersistenceError(StoreError):
    """The persistence backend is unavailable or failed operationally."""
