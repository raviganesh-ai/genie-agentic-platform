"""Deterministic cost resolution through the public Azure Retail Prices API."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from app.discovery.models import CostEstimate, PricingQuery

__all__ = ["AzureRetailPricingService", "PricingService"]


class PricingService(Protocol):
    async def estimate(self, queries: list[PricingQuery]) -> CostEstimate: ...


class AzureRetailPricingService:
    """Looks up real unit prices and applies agent-proposed usage quantities."""

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._endpoint = endpoint

    async def estimate(self, queries: list[PricingQuery]) -> CostEstimate:
        if not queries:
            return CostEstimate(
                region="unknown",
                coverage="unavailable",
                assumptions=["No Azure pricing inputs were identified for this solution."],
            )

        monthly_amount = 0.0
        resolved_count = 0
        assumptions: list[str] = []
        source_urls: list[str] = []
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            for query in queries:
                assumptions.append(query.assumption)
                try:
                    price, source_url = await self._lookup(client, query)
                except httpx.HTTPError:
                    continue
                if price is None:
                    continue
                resolved_count += 1
                monthly_amount += price * query.units_per_month
                source_urls.append(source_url)

        coverage = (
            "complete"
            if resolved_count == len(queries)
            else "partial"
            if resolved_count
            else "unavailable"
        )
        amount = round(monthly_amount, 2) if resolved_count else None
        return CostEstimate(
            region=queries[0].arm_region_name,
            monthly_amount=amount,
            annual_amount=round(amount * 12, 2) if amount is not None else None,
            coverage=coverage,
            assumptions=assumptions,
            source_urls=list(dict.fromkeys(source_urls)),
            retrieved_at=datetime.now(UTC),
        )

    async def _lookup(
        self,
        client: httpx.AsyncClient,
        query: PricingQuery,
    ) -> tuple[float | None, str]:
        clauses = [
            f"serviceName eq '{_escape_filter(query.service_name)}'",
            f"armRegionName eq '{_escape_filter(query.arm_region_name)}'",
            "priceType eq 'Consumption'",
        ]
        if query.sku_name:
            clauses.append(f"skuName eq '{_escape_filter(query.sku_name)}'")
        params = {"$filter": " and ".join(clauses), "$top": "1"}
        response = await client.get(self._endpoint, params=params)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        items = payload.get("Items")
        if not isinstance(items, list) or not items:
            return None, str(response.url)
        retail_price = items[0].get("retailPrice")
        if not isinstance(retail_price, (int, float)):
            return None, str(response.url)
        return float(retail_price), str(response.url)


def _escape_filter(value: str) -> str:
    return value.replace("'", "''")