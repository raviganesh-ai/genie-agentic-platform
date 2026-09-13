"""Deterministic cost resolution through the public Azure Retail Prices API."""
from __future__ import annotations

import re
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
                    query_amount, source_url = await self._lookup(client, query)
                except httpx.HTTPError:
                    continue
                if query_amount is None:
                    continue
                resolved_count += 1
                monthly_amount += query_amount
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
        retail_service_name = query.retail_service_name or query.service_name
        clauses = [
            f"serviceName eq '{_escape_filter(retail_service_name)}'",
            f"armRegionName eq '{_escape_filter(query.arm_region_name)}'",
            "priceType eq 'Consumption'",
        ]
        if query.product_name:
            clauses.append(f"productName eq '{_escape_filter(query.product_name)}'")
        if query.sku_name:
            clauses.append(f"skuName eq '{_escape_filter(query.sku_name)}'")
        if query.meter_name:
            clauses.append(f"meterName eq '{_escape_filter(query.meter_name)}'")
        response, items = await self._request_items(client, clauses)
        if not items:
            search_term = _service_search_term(retail_service_name)
            broad_clauses = [
                (
                    f"(contains(serviceName, '{_escape_filter(search_term)}') or "
                    f"contains(productName, '{_escape_filter(search_term)}'))"
                ),
                "priceType eq 'Consumption'",
            ]
            response, items = await self._request_items(client, broad_clauses)
        item = _best_price_item(items, query)
        if item is None:
            return None, str(response.url)
        return _calculate_tiered_amount(items, item, query.units_per_month), str(
            response.url
        )

    async def _request_items(
        self,
        client: httpx.AsyncClient,
        clauses: list[str],
    ) -> tuple[httpx.Response, list[dict[str, Any]]]:
        response = await client.get(
            self._endpoint,
            params={"$filter": " and ".join(clauses)},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        items = payload.get("Items")
        if not isinstance(items, list):
            return response, []
        return response, [item for item in items if isinstance(item, dict)]


def _escape_filter(value: str) -> str:
    return value.replace("'", "''")


def _normalized_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if token not in {"azure", "microsoft", "service"}
    }


def _service_search_term(value: str) -> str:
    tokens = [
        token
        for token in re.findall(r"[A-Za-z0-9]+", value)
        if token.casefold() not in {"azure", "microsoft", "service"}
    ]
    return " ".join(tokens) or value


def _best_price_item(
    items: list[dict[str, Any]],
    query: PricingQuery,
) -> dict[str, Any] | None:
    priced_items = [
        item for item in items if isinstance(item.get("retailPrice"), (int, float))
    ]
    if not priced_items:
        return None

    expected_service = _normalized_tokens(query.retail_service_name or query.service_name)
    expected_product = _normalized_tokens(query.product_name)
    expected_sku = _normalized_tokens(query.sku_name)
    expected_meter = _normalized_tokens(query.meter_name)
    expected_unit = _normalized_tokens(query.unit_of_measure)
    assumption = _normalized_tokens(query.assumption)

    base_items = [item for item in priced_items if _tier_minimum(item) == 0]

    def score(item: dict[str, Any]) -> int:
        service = _normalized_tokens(str(item.get("serviceName", "")))
        product = _normalized_tokens(str(item.get("productName", "")))
        sku = _normalized_tokens(str(item.get("skuName", "")))
        meter = _normalized_tokens(str(item.get("meterName", "")))
        unit = _normalized_tokens(str(item.get("unitOfMeasure", "")))
        region = str(item.get("armRegionName", "")).casefold()
        match_score = (
            8 * len(expected_meter & meter)
            + 6 * len(expected_sku & sku)
            + 5 * len(expected_product & product)
            + 4 * len(expected_service & (service | product))
            + 3 * len(expected_unit & unit)
            + len(assumption & (meter | product | sku))
            + _region_affinity(query.arm_region_name, region)
            + (1 if not region or region == "global" else 0)
        )
        return match_score

    return max(base_items or priced_items, key=score)


def _region_affinity(requested_region: str, retail_region: str) -> int:
    requested = re.sub(r"[^a-z0-9]", "", requested_region.casefold())
    retail = retail_region.casefold()
    if retail == requested_region.casefold():
        return 4
    public_us_prefixes = (
        "eastus",
        "westus",
        "centralus",
        "northcentralus",
        "southcentralus",
        "westcentralus",
        "canada",
    )
    if requested.startswith(public_us_prefixes) and retail == "zone 1":
        return 3
    return 0


def _tier_minimum(item: dict[str, Any]) -> float:
    value = item.get("tierMinimumUnits", 0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _calculate_tiered_amount(
    items: list[dict[str, Any]],
    selected_item: dict[str, Any],
    units: float,
) -> float | None:
    meter_id = selected_item.get("meterId")
    identity_fields = (
        "serviceName",
        "productName",
        "skuName",
        "meterName",
        "armRegionName",
        "unitOfMeasure",
    )
    tiers = [
        item
        for item in items
        if isinstance(item.get("retailPrice"), (int, float))
        and (
            (meter_id and item.get("meterId") == meter_id)
            or (
                not meter_id
                and all(item.get(field) == selected_item.get(field) for field in identity_fields)
            )
        )
    ]
    applicable_tiers = sorted(
        (item for item in tiers if _tier_minimum(item) < units),
        key=_tier_minimum,
    )
    if not applicable_tiers:
        return None

    amount = 0.0
    for index, tier in enumerate(applicable_tiers):
        lower_bound = _tier_minimum(tier)
        upper_bound = (
            _tier_minimum(applicable_tiers[index + 1])
            if index + 1 < len(applicable_tiers)
            else units
        )
        amount += (min(units, upper_bound) - lower_bound) * float(tier["retailPrice"])
    return amount