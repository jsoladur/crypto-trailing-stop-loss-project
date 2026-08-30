import logging
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from faker import Faker
from pytest_httpserver import HTTPServer
from pytest_httpserver.httpserver import HandlerType

from crypto_trailing_stop.config.dependencies import get_application_container
from crypto_trailing_stop.infrastructure.adapters.dtos.bit2me_account_info_dto import Bit2MeAccountInfoDto, Profile
from crypto_trailing_stop.infrastructure.adapters.remote.operating_exchange.enums import OperatingExchangeEnum
from crypto_trailing_stop.infrastructure.services.global_summary_service import GlobalSummaryService
from tests.helpers.httpserver_pytest import Bit2MeAPIRequestMatcher
from tests.helpers.httpserver_pytest.utils import prepare_httpserver_retrieve_portfolio_balance_mock
from tests.helpers.object_mothers import Bit2MeSummaryXlsxObjectMother

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def should_calculate_global_summary_properly(
    faker: Faker, integration_test_jobs_disabled_env: tuple[HTTPServer, str]
) -> None:
    _, httpserver, api_key, api_secret, operating_exchange, *_ = integration_test_jobs_disabled_env
    global_summary_service: GlobalSummaryService = (
        get_application_container().infrastructure_container().services_container().global_summary_service()
    )
    if operating_exchange == OperatingExchangeEnum.BIT2ME:
        _prepare_httpserver_mock(faker, httpserver, operating_exchange, api_key, api_secret)
        global_summary = await global_summary_service.get_global_summary()

        logger.info(repr(global_summary))

        assert global_summary is not None

        httpserver.check_assertions()
    else:
        with pytest.raises(ValueError):
            await global_summary_service.get_global_summary()


def _prepare_httpserver_mock(
    faker: Faker, httpserver: HTTPServer, operating_exchange: OperatingExchangeEnum, api_key: str, api_secret: str
) -> None:
    registration_year = datetime.now(UTC).year - 1

    # Get account registration date
    httpserver.expect(
        Bit2MeAPIRequestMatcher("/bit2me-api/v3/account", method="GET").set_api_key_and_secret(api_key, api_secret),
        handler_type=HandlerType.ONESHOT,
    ).respond_with_json(
        Bit2MeAccountInfoDto(
            registrationDate=faker.date_time_between_dates(
                datetime_start=datetime(registration_year, 1, 1),
                datetime_end=datetime(registration_year, 12, 31, 23, 59, 59),
            ),
            profile=Profile(currency_code="EUR"),
        ).model_dump(mode="json", by_alias=True)
    )
    # Get summary of each year in order to calculate currency metrics
    for current_year in range(registration_year, datetime.now(UTC).year + 1):
        excel_filecontent = Bit2MeSummaryXlsxObjectMother.create(year=current_year)
        httpserver.expect(
            Bit2MeAPIRequestMatcher(
                f"/bit2me-api/v1/accounting/summary/{current_year}",
                method="GET",
                query_string=urlencode(
                    {"timeZone": "Europe/Madrid", "langCode": "en", "documentType": "xlsx"}, doseq=False
                ),
            ).set_api_key_and_secret(api_key, api_secret),
            handler_type=HandlerType.ONESHOT,
        ).respond_with_data(excel_filecontent)

    # Portfolio balance
    prepare_httpserver_retrieve_portfolio_balance_mock(faker, httpserver, operating_exchange, api_key, api_secret)
