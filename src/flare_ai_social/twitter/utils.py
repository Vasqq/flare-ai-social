import re
import asyncio
import ssl
import certifi
import urllib.parse
import requests
import logging

logger = logging.getLogger(__name__)


def resolve_tco_url_sync(tco_url: str) -> str:
    """
    Synchronously resolves a t.co URL using requests and returns the final URL.
    """
    response = requests.get(tco_url, allow_redirects=True, verify=certifi.where())
    return response.url

async def resolve_tco_url_async(tco_url: str) -> str | None:
    """
    Asynchronously resolves a t.co URL by running the synchronous resolution in a thread.
    """
    try:
        final_url = await asyncio.to_thread(resolve_tco_url_sync, tco_url)
        logger.info(f"Resolved {tco_url} to {final_url}")
        return final_url
    except Exception as e:
        logger.exception(f"Error resolving t.co URL: {tco_url}: {e}")
        return None

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
        final_url = await resolve_tco_url_async(tco_link)
        return final_url
    return None