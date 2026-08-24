from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app import cli
from app.schemas.frozen import Usage


class RecordingGateway:
    def __init__(self):
        self.calls = []

    async def invoke(self, slot, prompt_version, input_view, output_schema):
        self.calls.append((slot, prompt_version, type(input_view).__name__, output_schema.__name__))
        return output_schema.model_construct(), Usage(
            model_slot=slot, prompt_tokens=0, output_tokens=0, ctx_chars=0
        )


@pytest.mark.asyncio
async def test_preflight_uses_composed_model_backend_without_full_runtime(monkeypatch, capsys):
    gateway = RecordingGateway()

    @asynccontextmanager
    async def fake_model_runtime():
        yield SimpleNamespace(gateway=gateway)

    monkeypatch.setattr(cli, "compose_local_model_runtime", fake_model_runtime)

    assert await cli._preflight() == 0
    assert [(slot, node) for slot, node, _, _ in gateway.calls] == [
        ("SMALL", "n1/v1"), ("SMALL", "n3/v1"), ("SMALL", "n7/v1"),
        ("LARGE", "n8/v1"), ("LARGE", "n9/v1"), ("LARGE", "n10/v1"),
        ("MID", "n11/v1"),
    ]
    assert "Anthropic" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_preflight_failure_does_not_print_provider_secret(monkeypatch, capsys):
    class FailingGateway:
        async def invoke(self, *_args, **_kwargs):
            raise RuntimeError("MLAPI_API_KEY=super-secret-test-value")

    @asynccontextmanager
    async def fake_model_runtime():
        yield SimpleNamespace(gateway=FailingGateway())

    monkeypatch.setattr(cli, "compose_local_model_runtime", fake_model_runtime)

    assert await cli._preflight() == 1
    assert "super-secret-test-value" not in capsys.readouterr().out
