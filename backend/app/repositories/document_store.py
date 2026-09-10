"""Shared document-store abstraction for durable Genie state."""
from __future__ import annotations

from typing import Any, Protocol

from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential

__all__ = ["CosmosDocumentStore", "DocumentStore"]


class DocumentStore(Protocol):
    async def upsert(self, document: dict[str, Any]) -> None: ...

    async def read(self, *, document_id: str, partition_key: str) -> dict[str, Any] | None: ...

    async def query(
        self,
        *,
        query: str,
        parameters: list[dict[str, Any]],
        partition_key: str,
    ) -> list[dict[str, Any]]: ...

    async def delete(self, *, document_id: str, partition_key: str) -> None: ...

    async def close(self) -> None: ...


class CosmosDocumentStore:
    """Cosmos SQL document store authenticated only with managed identity."""

    def __init__(
        self,
        *,
        endpoint: str,
        database_name: str,
        container_name: str,
    ) -> None:
        self._credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=self._credential)
        self._container = self._client.get_database_client(database_name).get_container_client(
            container_name
        )

    async def upsert(self, document: dict[str, Any]) -> None:
        await self._container.upsert_item(document)

    async def read(self, *, document_id: str, partition_key: str) -> dict[str, Any] | None:
        try:
            result = await self._container.read_item(
                item=document_id,
                partition_key=partition_key,
            )
        except CosmosResourceNotFoundError:
            return None
        return dict(result)

    async def query(
        self,
        *,
        query: str,
        parameters: list[dict[str, Any]],
        partition_key: str,
    ) -> list[dict[str, Any]]:
        items = self._container.query_items(
            query=query,
            parameters=parameters,
            partition_key=partition_key,
        )
        return [dict(item) async for item in items]

    async def delete(self, *, document_id: str, partition_key: str) -> None:
        try:
            await self._container.delete_item(item=document_id, partition_key=partition_key)
        except CosmosResourceNotFoundError:
            return

    async def close(self) -> None:
        await self._client.close()
        await self._credential.close()