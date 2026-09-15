"""Deterministic cost resolution through the public Azure Retail Prices API."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from app.discovery.models import (
    CostEstimate,
    PricingAlternate,
    PricingCommitment,
    PricingLineItem,
    PricingQuery,
)

__all__ = ["AzureRetailPricingService", "PricingService"]

# 2023-01-01-preview is required to receive the embedded ``savingsPlan`` array
# on eligible Consumption items. It is additive/backwards compatible with the
# GA response shape for every other field this service reads.
_API_VERSION = "2023-01-01-preview"
_RESERVATION_TERMS: dict[str, PricingCommitment] = {
    "1 Year": "reserved_1yr",
    "3 Years": "reserved_3yr",
}
_SAVINGS_PLAN_TERMS: dict[str, PricingCommitment] = {
    "1 Year": "savings_plan_1yr",
    "3 Years": "savings_plan_3yr",
}


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
        line_items: list[PricingLineItem] = []
        async with httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            for query in queries:
                assumptions.append(query.assumption)
                try:
                    query_amount, source_url, alternates = await self._lookup(client, query)
                except httpx.HTTPError:
                    line_items.append(PricingLineItem(service_name=query.service_name))
                    continue
                line_items.append(
                    PricingLineItem(
                        service_name=query.service_name,
                        monthly_amount=(
                            round(query_amount, 2) if query_amount is not None else None
                        ),
                        alternates=alternates,
                        source_url=source_url,
                    )
                )
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
            line_items=line_items,
            retrieved_at=datetime.now(UTC),
        )

    async def _lookup(
        self,
        client: httpx.AsyncClient,
        query: PricingQuery,
    ) -> tuple[float | None, str, list[PricingAlternate]]:
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
            return None, str(response.url), []
        amount = _calculate_tiered_amount(items, item, query.units_per_month)
        alternates = _savings_plan_alternates(item, query.units_per_month)
        alternates.extend(await self._lookup_reservation_alternates(client, item, query))
        return amount, str(response.url), alternates

    async def _lookup_reservation_alternates(
        self,
        client: httpx.AsyncClient,
        item: dict[str, Any],
        query: PricingQuery,
    ) -> list[PricingAlternate]:
        arm_sku_name = item.get("armSkuName")
        if not isinstance(arm_sku_name, str) or not arm_sku_name:
            return []
        clauses = [
            f"armRegionName eq '{_escape_filter(query.arm_region_name)}'",
            f"armSkuName eq '{_escape_filter(arm_sku_name)}'",
            "priceType eq 'Reservation'",
        ]
        try:
            _, reservation_items = await self._request_items(client, clauses)
        except httpx.HTTPError:
            return []
        alternates: list[PricingAlternate] = []
        for reservation_item in reservation_items:
            commitment = _RESERVATION_TERMS.get(reservation_item.get("reservationTerm", ""))
            price = reservation_item.get("retailPrice")
            if commitment is None or not isinstance(price, (int, float)):
                continue
            term_years = 3 if commitment == "reserved_3yr" else 1
            monthly_amount = round(float(price) / (term_years * 12), 2)
            alternates.append(
                PricingAlternate(commitment=commitment, monthly_amount=monthly_amount)
            )
        return alternates

    async def _request_items(
        self,
        client: httpx.AsyncClient,
        clauses: list[str],
    ) -> tuple[httpx.Response, list[dict[str, Any]]]:
        response = await client.get(
            self._endpoint,
            params={"$filter": " and ".join(clauses), "api-version": _API_VERSION},
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


def _savings_plan_alternates(
    item: dict[str, Any],
    units: float,
) -> list[PricingAlternate]:
    """Reads the ``savingsPlan`` array embedded in a Consumption item, if present.

    Only returned by the Azure Retail Prices API for eligible meters (mostly
    compute SKUs) when queried with ``api-version=2023-01-01-preview``. Savings
    Plan rates are flat (not tiered), so the monthly amount is a direct
    unit-price multiplication using the same units_per_month as the on-demand
    estimate for that meter.
    """
    savings_plan = item.get("savingsPlan")
    if not isinstance(savings_plan, list):
        return []
    alternates: list[PricingAlternate] = []
    for entry in savings_plan:
        if not isinstance(entry, dict):
            continue
        commitment = _SAVINGS_PLAN_TERMS.get(entry.get("term", ""))
        unit_price = entry.get("unitPrice")
        if commitment is None or not isinstance(unit_price, (int, float)):
            continue
        alternates.append(
            PricingAlternate(
                commitment=commitment,
                monthly_amount=round(float(unit_price) * units, 2),
            )
        )
    return alternates