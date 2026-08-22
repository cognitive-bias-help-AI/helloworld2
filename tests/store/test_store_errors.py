from app.gateway.execution import ProviderExecutionError
from app.store.errors import (
    StoreConflictError,
    StoreError,
    StoreLineageError,
    StorePersistenceError,
)


def test_store_error_taxonomy_preserves_semantic_value_error_compatibility():
    assert issubclass(StoreConflictError, StoreError)
    assert issubclass(StoreConflictError, ValueError)
    assert issubclass(StoreLineageError, StoreError)
    assert issubclass(StoreLineageError, ValueError)
    assert issubclass(StorePersistenceError, StoreError)
    assert not issubclass(StorePersistenceError, ValueError)
    assert not issubclass(StorePersistenceError, ProviderExecutionError)
