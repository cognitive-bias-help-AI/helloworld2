import pytest
from pydantic import TypeAdapter, ValidationError

from app.runtime.ids import CROCKFORD_ULID_ALPHABET, generate_ulid
from app.schemas.frozen import ULID


def test_legacy_UUID_truncation_output은_frozen_ULID가_거부한다():
    with pytest.raises(ValidationError):
        TypeAdapter(ULID).validate_python("BF7C22D505924C80B4199698D1")


def test_generated_ULID는_frozen_contract와_Crockford_alphabet을_만족한다():
    adapter = TypeAdapter(ULID)
    values = [generate_ulid() for _ in range(10_000)]

    assert len(values) == len(set(values))
    assert all(len(value) == 26 for value in values)
    assert all(set(value) <= set(CROCKFORD_ULID_ALPHABET) for value in values)
    assert all(value[0] in "01234567" for value in values)
    assert all(adapter.validate_python(value) == value for value in values)
