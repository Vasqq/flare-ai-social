import asyncio
import calendar
import time
import uuid
from dataclasses import dataclass
from typing import Any

import aiohttp
import structlog

from flare_ai_social.ai import BaseAIProvider

logger = structlog.get_logger(__name__)


HTTP_OK = 200
HTTP_RATE_LIMIT = 429
HTTP_SERVER_ERROR = 500
ERR_TWITTER_CREDENTIALS = "Required Twitter API credentials not provided."
ERR_RAPIDAPI_KEY = "RapidAPI key not provided. Please check your settings."
FALLBACK_REPLY = "We're experiencing some difficulties."


@dataclass
class TwitterConfig:
    """Configuration for Twitter API credentials and settings"""

    bearer_token: str | None = None
    api_key: str | None = None
    api_secret: str | None = None
    access_token: str | None = None
    access_secret: str | None = None
    rapidapi_key: str | None = None
    rapidapi_host: str | None = "twitter241.p.rapidapi.com"
    accounts_to_monitor: list[str] | None = None
    polling_interval: int = 30


class TwitterBot:
    def __init__(
        self,
        ai_provider: BaseAIProvider,
        config: TwitterConfig,
    ) -> None:
        self.ai_provider = ai_provider

        # Twitter API credentials
        self.bearer_token = config.bearer_token
        self.api_key = config.api_key
        self.api_secret = config.api_secret
        self.access_token = config.access_token
        self.access_secret = config.access_secret

        # RapidAPI credentials
        self.rapidapi_key = config.rapidapi_key
        self.rapidapi_host = config.rapidapi_host

        # Check if required credentials are provided
        if not all(
            [self.api_key, self.api_secret, self.access_token, self.access_secret]
        ):
            raise ValueError(ERR_TWITTER_CREDENTIALS)

        if not self.rapidapi_key:
            raise ValueError(ERR_RAPIDAPI_KEY)

        # Monitoring parameters
        self.accounts_to_monitor = config.accounts_to_monitor or ["@privychatxyz"]
        self.polling_interval = config.polling_interval

        # API endpoints
        self.twitter_api_base = "https://api.twitter.com/2"
        self.rapidapi_search_endpoint = f"https://{self.rapidapi_host}/search-v2"

        logger.info(
            "TwitterBot initialized - monitoring mentions for accounts",
            accounts=self.accounts_to_monitor,
        )

    def _url_encode(self, value: Any) -> str:
        """Properly URL encode according to OAuth 1.0a spec (RFC 3986)"""
        import urllib.parse

        # Twitter requires RFC 3986 encoding, which is stricter than urllib's default
        return urllib.parse.quote(str(value), safe="")

    def _get_oauth1_auth(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Generate OAuth 1.0a authorization for Twitter API v2 requests"""
        import base64
        import hashlib
        import hmac

        oauth_timestamp = str(int(time.time()))
        oauth_nonce = uuid.uuid4().hex

        # Base parameters for OAuth 1.0a
        oauth_params: dict[str, Any] = {
            "oauth_consumer_key": self.api_key,
            "oauth_nonce": oauth_nonce,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": oauth_timestamp,
            "oauth_token": self.access_token,
            "oauth_version": "1.0",
        }

        # For signature base string:
        # 1. Only include query parameters, not JSON body
        all_params: dict[str, Any] = {}
        if params:
            all_params.update(params)
        all_params.update(oauth_params)

        # Create parameter string - must be sorted by key (after encoding)
        param_pairs: list[str] = []
        for k, v in sorted(all_params.items(), key=lambda x: self._url_encode(x[0])):
            encoded_key = self._url_encode(k)
            encoded_value = self._url_encode(str(v))
            param_pairs.append(f"{encoded_key}={encoded_value}")

        param_string = "&".join(param_pairs)

        # Create signature base string
        base_url = url.split("?")[0]
        base_string = (
            f"{method.upper()}&{self._url_encode(base_url)}&"
            f"{self._url_encode(param_string)}"
        )

        # Create signing key
        signing_key = (
            f"{self._url_encode(self.api_secret)}&"
            f"{self._url_encode(self.access_secret)}"
        )

        # Generate signature
        signature = base64.b64encode(
            hmac.new(
                signing_key.encode("utf-8"), base_string.encode("utf-8"), hashlib.sha1
            ).digest()
        ).decode("utf-8")

        # Add signature to oauth parameters
        oauth_params["oauth_signature"] = signature

        # Format the Authorization header
        auth_header_parts: list[str] = []
        for k, v in sorted(oauth_params.items()):
            auth_header_parts.append(f'{self._url_encode(k)}="{self._url_encode(v)}"')

        return "OAuth " + ", ".join(auth_header_parts)

    def _get_twitter_api_headers(
        self, method: str, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """Generate headers for Twitter API v2 requests with OAuth 1.0a"""
        # Ensure all header values are strings.
        return {
            "Authorization": self._get_oauth1_auth(method, url, params),
            "Content-Type": "application/json",
        }

    def _get_rapidapi_headers(self) -> dict[str, str]:
        """Generate headers for RapidAPI requests"""
        return {
            "x-rapidapi-host": self.rapidapi_host or "",
            "x-rapidapi-key": self.rapidapi_key or "",
        }

    async def post_tweet(
        self, text: str, retry_count: int = 0, max_retries: int = 3
    ) -> str | None:
        """Post a new tweet using Twitter API v2 with retry logic"""
        url = f"{self.twitter_api_base}/tweets"
        payload = {"text": text}

        try:
            headers = self._get_twitter_api_headers("POST", url)
            timeout = aiohttp.ClientTimeout(total=30)

            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                ) as response,
            ):
                if response.status in [HTTP_OK, 201]:
                    result = await response.json()
                    tweet_id = result["data"]["id"]
                    logger.info("Tweet posted successfully: %s", tweet_id)
                    return tweet_id

                error_text = await response.text()
                should_retry = retry_count < max_retries and (
                    response.status >= HTTP_SERVER_ERROR
                    or response.status == HTTP_RATE_LIMIT
                )

                if should_retry:
                    delay_multiplier = 10 if response.status == HTTP_RATE_LIMIT else 2
                    retry_delay = (2**retry_count) * delay_multiplier
                    logger.warning(
                        "Twitter API error, retrying",
                        status=response.status,
                        retry_count=retry_count + 1,
                        max_retries=max_retries,
                    )
                    await asyncio.sleep(retry_delay)
                    return await self.post_tweet(text, retry_count + 1, max_retries)

                logger.error(
                    "Failed to post tweet",
                    status=response.status,
                    error=error_text,
                )

        except TimeoutError:
            if retry_count < max_retries:
                retry_delay = (2**retry_count) * 5
                logger.warning(
                    "Twitter API connection timeout, retrying",
                    retry_delay=retry_delay,
                    retry_count=retry_count + 1,
                    max_retries=max_retries,
                )
                await asyncio.sleep(retry_delay)
                return await self.post_tweet(text, retry_count + 1, max_retries)
            logger.exception("Twitter API connection timeout")
        except Exception:
            logger.exception("Error posting tweet")

        return None

    async def post_reply(
        self,
        reply_text: str,
        tweet_id_to_reply_to: str,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> str | None:
        """Post a reply to a specific tweet using Twitter API v2 with retry logic"""
        url = f"{self.twitter_api_base}/tweets"
        payload = {
            "text": reply_text,
            "reply": {"in_reply_to_tweet_id": tweet_id_to_reply_to},
        }

        try:
            headers = self._get_twitter_api_headers("POST", url)

            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response,
            ):
                if response.status in [HTTP_OK, 201]:
                    result = await response.json()
                    logger.info("Reply posted successfully")
                    return result["data"]["id"]

                error_text = await response.text()
                should_retry = retry_count < max_retries and (
                    response.status == HTTP_RATE_LIMIT
                    or isinstance(response, TimeoutError)
                )

                if should_retry:
                    delay_multiplier = 10 if response.status == HTTP_RATE_LIMIT else 5

                    retry_delay = (2**retry_count) * delay_multiplier
                    logger.warning(
                        "Twitter API error, retrying",
                        status=response.status,
                        retry_count=retry_count + 1,
                        max_retries=max_retries,
                    )
                    await asyncio.sleep(retry_delay)
                    return await self.post_reply(
                        reply_text,
                        tweet_id_to_reply_to,
                        retry_count + 1,
                        max_retries,
                    )

                logger.error(
                    "Failed to post reply",
                    status=response.status,
                    error=error_text,
                )

        except Exception as e:
            should_retry = retry_count < max_retries
            if should_retry:
                retry_delay = (2**retry_count) * 3
                logger.warning(
                    "Error posting reply, retrying",
                    error=str(e),
                    retry_count=retry_count + 1,
                    max_retries=max_retries,
                )
                await asyncio.sleep(retry_delay)
                return await self.post_reply(
                    reply_text,
                    tweet_id_to_reply_to,
                    retry_count + 1,
                    max_retries,
                )
            logger.exception("Failed to post reply after max retries")

        return None

    async def search_twitter(
        self,
        keyword: str,
        session: aiohttp.ClientSession,
        retry_count: int = 0,
        max_retries: int = 3,
    ) -> list[dict[str, Any]]:
        """Search Twitter using new RapidAPI endpoint with a recent time filter"""
        params = {"query": keyword, "count": "20", "type": "Latest"}

        try:
            async with session.get(
                self.rapidapi_search_endpoint,
                headers=self._get_rapidapi_headers(),
                params=params,
            ) as response:
                if response.status == HTTP_OK:
                    result = await response.json()
                    return self._extract_tweets_from_response(result)
                if response.status == HTTP_RATE_LIMIT and retry_count < max_retries:
                    error_text = await response.text()
                    retry_delay = (2**retry_count) * 2
                    logger.warning(
                        "Rate limit exceeded (429), retrying in %d seconds",
                        retry_delay,
                        retry_count=retry_count + 1,
                        max_retries=max_retries,
                        error=error_text,
                    )
                    await asyncio.sleep(retry_delay)
                    return await self.search_twitter(
                        keyword, session, retry_count + 1, max_retries
                    )
                error_text = await response.text()
                logger.error(
                    "Search failed with status %d: %s",
                    response.status,
                    error_text,
                )
                return []
        except Exception:
            logger.exception("Error during search for %s", keyword)
            return []

    def _extract_tweets_from_response(
        self, response_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Extract tweets from the new API response format"""
        tweets: list[dict[str, Any]] = []
        try:
            if "result" in response_data and "timeline" in response_data["result"]:
                instructions = response_data["result"]["timeline"].get(
                    "instructions", []
                )
                for instruction in instructions:
                    if instruction.get("type") == "TimelineAddEntries":
                        entries = instruction.get("entries", [])
                        for entry in entries:
                            content = entry.get("content", {})
                            if content.get("__typename") == "TimelineTimelineItem":
                                item_content = content.get("itemContent", {})
                                if item_content.get("__typename") == "TimelineTweet":
                                    tweet_results = item_content.get(
                                        "tweet_results", {}
                                    )
                                    result = tweet_results.get("result", {})
                                    if result.get("__typename") == "Tweet":
                                        legacy_data = result.get("legacy", {})
                                        user_data = (
                                            result.get("core", {})
                                            .get("user_results", {})
                                            .get("result", {})
                                            .get("legacy", {})
                                        )
                                        tweet = {
                                            "id_str": legacy_data.get("id_str", ""),
                                            "created_at": legacy_data.get(
                                                "created_at", ""
                                            ),
                                            "full_text": legacy_data.get(
                                                "full_text", ""
                                            ),
                                            "user_id_str": legacy_data.get(
                                                "user_id_str", ""
                                            ),
                                            "entities": legacy_data.get("entities", {}),
                                            "user": {
                                                "screen_name": user_data.get(
                                                    "screen_name", ""
                                                )
                                            },
                                        }
                                        tweets.append(tweet)
        except Exception:
            logger.exception("Error extracting tweets from response")
            return []
        else:
            return tweets

    def process_tweets(
        self, tweets: list[dict[str, Any]], account: str
    ) -> list[dict[str, Any]]:
        """Process tweets to find new mentions from the last polling interval"""
        if not tweets:
            return []

        new_mentions: list[dict[str, Any]] = []
        current_time = time.time()
        time_window_ago = current_time - self.polling_interval

        for tweet in tweets:
            if not all(k in tweet for k in ["id_str", "created_at"]):
                continue

            try:
                created_time = time.strptime(
                    tweet["created_at"], "%a %b %d %H:%M:%S %z %Y"
                )
                tweet_timestamp = calendar.timegm(created_time)
                if tweet_timestamp < time_window_ago:
                    continue
            except (ValueError, KeyError):
                logger.exception("Error parsing tweet timestamp")
                continue

            mentioned = False
            for mention in tweet.get("entities", {}).get("user_mentions", []):
                if f"@{mention.get('screen_name', '').lower()}" == account.lower():
                    mentioned = True
                    break

            if not mentioned:
                continue

            logger.info(
                "Found recent mention (within last %d sec): %s",
                self.polling_interval,
                tweet["id_str"],
            )
            new_mentions.append(tweet)

        return new_mentions

    async def handle_mention(self, tweet: dict[str, Any]) -> None:
        """Handle a mention by generating AI response and replying to it"""
        tweet_id = tweet.get("id_str", "")
        username = "user"
        for mention in tweet.get("entities", {}).get("user_mentions", []):
            if mention.get("id_str") == tweet.get("user_id_str"):
                username = mention.get("screen_name", "user")
                break

        text = tweet.get("full_text", "")

        try:
            clean_text = text
            for mention in tweet.get("entities", {}).get("user_mentions", []):
                mention_text = f"@{mention.get('screen_name', '')}"
                clean_text = clean_text.replace(mention_text, "").strip()

            ai_response = self.ai_provider.generate_content(clean_text)
            response_text = ai_response.text

            max_chars = 280
            if len(response_text) > max_chars:
                response_text = response_text[: max_chars - 3] + "..."

            await self.post_reply(response_text, tweet_id)
        except Exception:
            logger.exception("Error generating AI response")
            fallback_reply = f"@{username} {FALLBACK_REPLY}"
            await self.post_reply(fallback_reply, tweet_id)

    async def monitor_mentions(self) -> None:
        """Main method to monitor mentions for all accounts"""
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    for account in self.accounts_to_monitor:
                        logger.debug("Searching for mentions of %s", account)
                        tweets = await self.search_twitter(account, session)
                        new_mentions = self.process_tweets(tweets, account)

                        if new_mentions:
                            logger.info(
                                "Found %d new mentions for %s",
                                len(new_mentions),
                                account,
                            )
                            for tweet in new_mentions:
                                await self.handle_mention(tweet)
                        else:
                            logger.debug("No new mentions found for %s", account)

                        if len(self.accounts_to_monitor) > 1:
                            await asyncio.sleep(1)

                    logger.debug(
                        "Completed mention check cycle, sleeping for %d seconds",
                        self.polling_interval,
                    )
                    await asyncio.sleep(self.polling_interval)

                except Exception:
                    logger.exception("Error in monitoring loop")
                    await asyncio.sleep(self.polling_interval * 2)

    def start(self) -> None:
        """Start the monitoring process"""
        logger.info("Starting Twitter monitoring bot")
        try:
            asyncio.run(self.monitor_mentions())
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception:
            logger.exception("Fatal error")
