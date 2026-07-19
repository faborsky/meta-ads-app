#!/usr/bin/env python3
"""Meta Ads CLI — thin entrypoint. Implementation lives in the metaads/ package.

Backward-compat re-exports for scripts that `import meta_ads_cli as cli`
(personal skills use cli._api_call / cli.META_AD_ACCOUNT_ID).
"""

from metaads.api import (  # noqa: F401
    API_BASE,
    API_VERSION,
    META_ACCESS_TOKEN,
    META_AD_ACCOUNT_ID,
    META_APP_ID,
    META_APP_SECRET,
    META_PAGE_ID,
    _api_call,
    _paginate,
)
from metaads.cli import main

if __name__ == "__main__":
    main()
