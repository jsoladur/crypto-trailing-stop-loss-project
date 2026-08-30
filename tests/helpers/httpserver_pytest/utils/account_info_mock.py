import logging
from datetime import UTC, datetime

from faker import Faker
from pytest_httpserver import HTTPServer
from pytest_httpserver.httpserver import HandlerType

from crypto_trailing_stop.infrastructure.adapters.dtos.bit2me_account_info_dto import Bit2MeAccountInfoDto, Profile
from crypto_trailing_stop.infrastructure.adapters.remote.operating_exchange.enums import OperatingExchangeEnum
from crypto_trailing_stop.infrastructure.adapters.remote.operating_exchange.vo import AccountInfo
from tests.helpers.httpserver_pytest import Bit2MeAPIRequestMatcher

logger = logging.getLogger(__name__)


def prepare_httpserver_account_info_mock(
    faker: Faker, httpserver: HTTPServer, operating_exchange: OperatingExchangeEnum, api_key: str, api_secret: str
) -> AccountInfo:
    match operating_exchange:
        case OperatingExchangeEnum.BIT2ME:
            registration_year = datetime.now(UTC).year - 1
            bit2me_account_info = Bit2MeAccountInfoDto(
                registration_date=faker.date_time_between_dates(
                    datetime_start=datetime(registration_year, 1, 1),
                    datetime_end=datetime(registration_year, 12, 31, 23, 59, 59),
                ),
                profile=Profile(currency_code="EUR"),
            )
            account_info = AccountInfo(
                registration_date=bit2me_account_info.registration_date,
                currency_code=bit2me_account_info.profile.currency_code,
            )
            # Get account registration date
            httpserver.expect(
                Bit2MeAPIRequestMatcher("/bit2me-api/v3/account", method="GET").set_api_key_and_secret(
                    api_key, api_secret
                ),
                handler_type=HandlerType.ONESHOT,
            ).respond_with_json(bit2me_account_info.model_dump(mode="json", by_alias=True))
        case OperatingExchangeEnum.MEXC:
            account_info = AccountInfo(registration_date=None, currency_code="USDT")
        case _:
            raise ValueError(f"Unknown exchange: {operating_exchange}")
    return account_info
