import asyncio
import logging
import re

import certifi
import requests

logger = logging.getLogger(__name__)


def resolve_tco_url_sync(tco_url: str) -> str:
    """
    Synchronously resolves a t.co URL using requests and returns the final URL.
    """
    response = requests.get(tco_url, allow_redirects=True, \
                            verify=certifi.where(), timeout=10)
    return response.url


async def resolve_tco_url_async(tco_url: str) -> str | None:
    """
    Asynchronously resolves a t.co URL by running the
    synchronous resolution in a thread.
    """
    try:
        final_url = await asyncio.to_thread(resolve_tco_url_sync, tco_url)
        logger.info("Resolved %s to %s", tco_url, final_url)
    except Exception:
        logger.exception("Error resolving t.co URL: %s", tco_url)
        return None
    else:
        return final_url


async def extract_link_from_text(text: str) -> str | None:
    """
    Extracts a t.co URL from the given text and returns the resolved final URL.

    Args:
        text (str): The text to search.

    Returns:
        Optional[str]: The resolved URL or None if no t.co URL is found.
    """
    pattern = r"https:\/\/t\.co\/\w+"
    match = re.search(pattern, text)
    if match:
        tco_link = match.group(0)
        return await resolve_tco_url_async(tco_link)
    return None
