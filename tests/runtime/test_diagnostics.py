from pydantic import BaseModel, ValidationError

from app.assemblers.semantic_extraction import SemanticAssemblyError
from app.diagnostics import safe_exception_fields


def test_semantic_assembly_error_exposes_only_safe_fields():
    error = SemanticAssemblyError(
        "span_mismatch", slot_id=4, semantic_kind="EXTERNAL_ASSERTION", segment_id="free_text:0",
        detail="secret user text must not appear",
    )
    assert safe_exception_fields(error) == {
        "exception_type": "SemanticAssemblyError",
        "category": "span_mismatch",
        "slot_id": 4,
        "semantic_kind": "EXTERNAL_ASSERTION",
        "segment_id": "free_text:0",
    }


def test_file_not_found_reports_safe_artifact_name_only():
    fields = safe_exception_fields(FileNotFoundError("C:/private/secret/krx_stock_master.json"))
    assert fields == {"exception_type": "FileNotFoundError", "artifact": "krx_stock_master.json"}
    assert "private" not in str(fields)


def test_validation_error_reports_types_and_locations_without_input_values():
    class Input(BaseModel):
        value: int
    try:
        Input(value="secret")
    except ValidationError as error:
        fields = safe_exception_fields(error)
    assert fields["exception_type"] == "ValidationError"
    assert fields["errors"] == [{"type": "int_parsing", "loc": ["value"]}]
    assert "secret" not in str(fields)


def test_unknown_exception_reports_type_only():
    assert safe_exception_fields(RuntimeError("API_KEY=secret")) == {"exception_type": "RuntimeError"}
