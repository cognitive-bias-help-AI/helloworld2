"""Checkpoint instrumentation using LangGraph's configured serializer."""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver


class MeasuringInMemorySaver(InMemorySaver):
    def __init__(self) -> None:
        super().__init__()
        self.serialized_sizes: list[int] = []
        self.serialized_payloads: list[bytes] = []

    def put(self, config, checkpoint, metadata, new_versions):
        _, payload = self.serde.dumps_typed(checkpoint)
        self.serialized_sizes.append(len(payload))
        self.serialized_payloads.append(payload)
        return super().put(config, checkpoint, metadata, new_versions)

    async def aput(self, config, checkpoint, metadata, new_versions):
        _, payload = self.serde.dumps_typed(checkpoint)
        self.serialized_sizes.append(len(payload))
        self.serialized_payloads.append(payload)
        return await super().aput(config, checkpoint, metadata, new_versions)
