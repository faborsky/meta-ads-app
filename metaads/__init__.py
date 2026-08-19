"""Meta Ads CLI package."""
import warnings

# Silence urllib3's LibreSSL/OpenSSL warning BEFORE requests/urllib3 is imported
# by any submodule; must run first so stdout/stderr stays clean.
warnings.filterwarnings("ignore", message=r".*OpenSSL.*")

__version__ = "2.3.0"
