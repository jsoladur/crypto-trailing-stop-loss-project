import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

import backoff
import cachebox
from httpx import URL, AsyncClient, HTTPStatusError, NetworkError, Response, Timeout, TimeoutException
from pandas import Timedelta
from pydantic import RootModel

from crypto_trailing_stop.commons.constants import (
    BIT2ME_RETRYABLE_HTTP_STATUS_CODES,
    DEFAULT_IN_MEMORY_CACHE_TTL_IN_SECONDS,
)
from crypto_trailing_stop.commons.utils import backoff_on_backoff_handler, prepare_backoff_giveup_handler_fn
from crypto_trailing_stop.config.configuration_properties import ConfigurationProperties
from crypto_trailing_stop.infrastructure.adapters.dtos.bit2me_account_info_dto import Bit2MeAccountInfoDto
from crypto_trailing_stop.infrastructure.adapters.dtos.bit2me_market_config_dto import Bit2MeMarketConfigDto
from crypto_trailing_stop.infrastructure.adapters.dtos.bit2me_order_dto import (
    Bit2MeOrderDto,
    Bit2MeOrderSide,
    Bit2MeOrderStatus,
    Bit2MeOrderType,
    CreateNewBit2MeOrderDto,
)
from crypto_trailing_stop.infrastructure.adapters.dtos.bit2me_pagination_result_dto import Bit2MePaginationResultDto
from crypto_trailing_stop.infrastructure.adapters.dtos.bit2me_porfolio_balance_dto import Bit2MePortfolioBalanceDto
from crypto_trailing_stop.infrastructure.adapters.dtos.bit2me_tickers_dto import Bit2MeTickersDto
from crypto_trailing_stop.infrastructure.adapters.dtos.bit2me_trade_dto import Bit2MeTradeDto, Bit2MeTradeSide
from crypto_trailing_stop.infrastructure.adapters.dtos.bit2me_trading_wallet_balance import (
    Bit2MeTradingWalletBalanceDto,
)
from crypto_trailing_stop.infrastructure.adapters.remote.base import AbstractHttpRemoteAsyncService

if TYPE_CHECKING:
    from crypto_trailing_stop.infrastructure.tasks.vo.types import Timeframe

logger = logging.getLogger(__name__)


class Bit2MeRemoteService(AbstractHttpRemoteAsyncService):
    def __init__(self, configuration_properties: ConfigurationProperties) -> None:
        self._configuration_properties = configuration_properties
        self._base_url = str(self._configuration_properties.bit2me_api_base_url)
        self._api_key = self._configuration_properties.bit2me_api_key
        self._api_secret = self._configuration_properties.bit2me_api_secret
        if not self._base_url or not self._api_key or not self._api_secret:
            raise ValueError("Bit2Me API configuration is missing or incomplete.")

    async def get_account_info(self, *, client: AsyncClient | None = None) -> Bit2MeAccountInfoDto:
        response = await self._perform_http_request(url="/v3/account", client=client)
        ret = Bit2MeAccountInfoDto.model_validate_json(response.content)
        return ret

    async def get_trading_wallet_balances(
        self, symbols: list[str] | str | None = None, *, client: AsyncClient | None = None
    ) -> list[Bit2MeTradingWalletBalanceDto]:
        params = {}
        if symbols:
            params["symbols"] = ",".join(symbols if isinstance(symbols, list) else [symbols])
        response = await self._perform_http_request(url="/v1/trading/wallet/balance", params=params, client=client)
        ret = RootModel[list[Bit2MeTradingWalletBalanceDto]].model_validate_json(response.content).root
        return ret

    async def retrieve_porfolio_balance(
        self, user_currency: str, *, client: AsyncClient | None = None
    ) -> list[Bit2MePortfolioBalanceDto]:
        response = await self._perform_http_request(
            url="/v1/portfolio/balance", params={"userCurrency": user_currency.strip().upper()}, client=client
        )
        ret = RootModel[list[Bit2MePortfolioBalanceDto]].model_validate_json(response.content).root
        return ret

    async def get_accounting_summary_by_year(self, year: str, *, client: AsyncClient | None = None) -> bytes:
        response = await self._perform_http_request(
            url=f"/v1/accounting/summary/{year}",
            params={"timeZone": "Europe/Madrid", "langCode": "en", "documentType": "xlsx"},
            client=client,
        )
        return response.content

    async def get_single_tickers_by_symbol(
        self, symbol: str, *, client: AsyncClient | None = None
    ) -> Bit2MeTickersDto | None:
        response = await self._perform_http_request(url="/v2/trading/tickers", params={"symbol": symbol}, client=client)
        tickers = RootModel[list[Bit2MeTickersDto]].model_validate_json(response.content).root
        ret: Bit2MeTickersDto | None = None
        if tickers:
            ret, *_ = tickers
        return ret

    async def get_tickers_by_symbols(self, *, client: AsyncClient | None = None) -> list[Bit2MeTickersDto]:
        response = await self._perform_http_request(url="/v2/trading/tickers", client=client)
        tickers_list = RootModel[list[Bit2MeTickersDto]].model_validate_json(response.content).root
        ret = [tickers for tickers in tickers_list if tickers.close is not None]
        return ret

    async def get_orders(
        self,
        *,
        side: Bit2MeOrderSide | None = None,
        order_type: Bit2MeOrderType | None = None,
        status: list[Bit2MeOrderStatus] | Bit2MeOrderStatus | None = None,
        symbol: str | None = None,
        client: AsyncClient | None = None,
    ) -> list[Bit2MeOrderDto]:
        status = status or []
        status = status if isinstance(status, (list, set, tuple, frozenset)) else [status]
        params = {"direction": "desc"}
        if status:
            params["status_in"] = ",".join([s.value if isinstance(s, Enum) else str(s) for s in status])
        if side:
            params["side"] = side
        if order_type:
            params["orderType"] = order_type
        if symbol:
            params["symbol"] = symbol
        response = await self._perform_http_request(url="/v1/trading/order", params=params, client=client)
        orders: list[Bit2MeOrderDto] = RootModel[list[Bit2MeOrderDto]].model_validate_json(response.content).root
        return orders

    async def get_order_by_id(self, id: str, *, client: AsyncClient | None = None) -> Bit2MeOrderDto:
        response = await self._perform_http_request(url=f"/v1/trading/order/{id}", client=client)
        ret = Bit2MeOrderDto.model_validate_json(response.content)
        return ret

    async def get_trades(
        self, *, side: Bit2MeTradeSide | None = None, symbol: str | None = None, client: AsyncClient | None = None
    ) -> list[Bit2MeTradeDto]:
        params = {"direction": "desc"}
        if side:
            params["side"] = side
        if symbol:
            params["symbol"] = symbol
        response = await self._perform_http_request(url="/v1/trading/trade", params=params, client=client)
        pagination_result = Bit2MePaginationResultDto[Bit2MeTradeDto].model_validate_json(response.content)
        ret = pagination_result.data or []
        return ret

    async def create_order(
        self, order: CreateNewBit2MeOrderDto, *, client: AsyncClient | None = None
    ) -> Bit2MeOrderDto:
        order_as_dict: dict[str, Any] = order.model_dump(mode="json", by_alias=True, exclude_none=True)
        response = await self._perform_http_request(
            method="POST", url="/v1/trading/order", body=order_as_dict, client=client
        )
        order = Bit2MeOrderDto.model_validate_json(response.content)
        return order

    async def cancel_order_by_id(self, id: str, *, client: AsyncClient | None = None) -> None:
        await self._perform_http_request(method="DELETE", url=f"/v1/trading/order/{id}", client=client)

    async def fetch_ohlcv(
        self, symbol: str, timeframe: "Timeframe", limit: int = 251, *, client: AsyncClient | None = None
    ) -> list[list[Any]]:
        interval = self._convert_timeframe_to_interval(timeframe)
        now = datetime.now(UTC)
        start_time = now - timedelta(minutes=limit * interval)
        response = await self._perform_http_request(
            method="GET",
            url="/v1/trading/candle",
            params={
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
                "startTime": int(start_time.timestamp() * 1000),
                "endTime": int(now.timestamp() * 1000),
            },
            client=client,
        )
        ohlcv = response.json()
        return ohlcv

    @cachebox.cachedmethod(
        cachebox.TTLCache(0, ttl=DEFAULT_IN_MEMORY_CACHE_TTL_IN_SECONDS),
        key_maker=lambda _, __: "bit2me_trading_market_config",
    )
    async def get_trading_market_config_list(
        self, *, client: AsyncClient | None = None
    ) -> dict[str, Bit2MeMarketConfigDto]:
        response = await self._perform_http_request(url="/v1/trading/market-config", client=client)
        market_config_list = RootModel[list[Bit2MeMarketConfigDto]].model_validate_json(response.content).root
        ret = {market_config.symbol: market_config for market_config in market_config_list}
        return ret

    async def get_http_client(self) -> AsyncClient:
        return AsyncClient(
            base_url=self._base_url, headers={"X-API-KEY": self._api_key}, timeout=Timeout(10, connect=5, read=60)
        )

    @backoff.on_exception(
        backoff.fibo,
        exception=(ValueError, NetworkError, TimeoutException),
        max_value=5,
        max_tries=7,
        jitter=backoff.random_jitter,
        giveup=prepare_backoff_giveup_handler_fn(BIT2ME_RETRYABLE_HTTP_STATUS_CODES),
        on_backoff=backoff_on_backoff_handler,
    )
    async def _perform_http_request(
        self,
        *,
        method: str = "GET",
        url: URL | str = "/",
        params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        body: Any | None = None,
        client: AsyncClient = None,
        **kwargs,
    ) -> Response:
        response = await super()._perform_http_request(
            method=method, url=url, params=params, headers=headers, body=body, client=client, **kwargs
        )
        return response

    async def _apply_request_interceptor(
        self,
        *,
        method: str = "GET",
        url: str = "/",
        params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        body: Any | None = None,
    ) -> tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]:
        params, headers = await super()._apply_request_interceptor(
            method=method, url=url, params=params, headers=headers, body=body
        )
        nonce = headers["x-nonce"] = str(int(time.time() * 1000))  # UTC timestamp in milliseconds
        headers["api-signature"] = await self._generate_api_signature(nonce, url, params, body)
        return params, headers

    async def _apply_response_interceptor(
        self,
        *,
        method: str = "GET",
        url: str = "/",
        params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        body: Any | None = None,
        response: Response,
    ) -> Response:
        params = params or {}
        headers = headers or {}
        try:
            response.raise_for_status()
            return await super()._apply_response_interceptor(
                method=method, url=url, params=params, headers=headers, body=body, response=response
            )
        except HTTPStatusError as e:
            raise ValueError(
                f"Bit2Me API error: HTTP {method} {self._build_full_url(url, params)} "
                + f"- Status code: {response.status_code} - {response.text}",
                response,
            ) from e

    async def _generate_api_signature(self, nonce: str, url: str, params: dict[str, Any] | None, body: Any) -> str:
        url = self._build_full_url(url, params)
        message_to_sign = f"{nonce}:{url}"
        if body:  # pragma: no cover
            message_to_sign += f":{json.dumps(body, separators=(',', ':'))}"
        sha256_hash = hashlib.sha256()
        hash_digest = sha256_hash.update(message_to_sign.encode("utf-8"))
        hash_digest = sha256_hash.digest()
        # Create HMAC-SHA512
        hmac_obj = hmac.new(self._api_secret.encode(), hash_digest, hashlib.sha512)
        hmac_digest = base64.b64encode(hmac_obj.digest()).decode()
        return hmac_digest

    def _convert_timeframe_to_interval(self, timeframe: "Timeframe") -> int:
        # The interval of entries in minutes: 1, 5, 15, 30, 60 (1 hour), 240 (4 hours), 1440 (1 day)
        td = Timedelta(timeframe)
        ret = int(td.total_seconds() // 60)
        return ret
