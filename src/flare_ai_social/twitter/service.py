import asyncio
import calendar
import os
import ssl
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import certifi
import structlog

from flare_ai_social.ai import BaseAIProvider
from flare_ai_social.twitter import utils

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

    cookie_path: str | None = None
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

        # Twitter Bot Cookie path
        self.cookie_path = config.cookie_path

        # Conversation context linking a tweet ID to an audio file of an X space
        self.conversation_context = {}

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
            [
                self.cookie_path,
                self.api_key,
                self.api_secret,
                self.access_token,
                self.access_secret,
            ]
        ):
            raise ValueError(ERR_TWITTER_CREDENTIALS)

        if not self.rapidapi_key:
            raise ValueError(ERR_RAPIDAPI_KEY)

        # Monitoring parameters
        self.accounts_to_monitor = config.accounts_to_monitor or ["@ScribeChainFLR"]
        self.polling_interval = config.polling_interval

        # Rate limit reset time tracker for X Api V2 free tier
        self.rate_limit_reset_time: float | None = None

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
        if method.upper() == "POST":
            # Force OAuth 1.0a for POST endpoints
            return {
                "Authorization": self._get_oauth1_auth(method, url, params),
                "Content-Type": "application/json",
            }
        if self.bearer_token:
            # For GET endpoints
            return {
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
            }
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
        # Create an SSL context using certifi's CA bundle.
        ssl_context = ssl.create_default_context(cafile=certifi.where())

        try:
            headers = self._get_twitter_api_headers("POST", url)
            timeout = aiohttp.ClientTimeout(total=30)

            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url, headers=headers, json=payload, timeout=timeout, ssl=ssl_context
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
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                    ssl=ssl_context,
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
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            async with session.get(
                self.rapidapi_search_endpoint,
                headers=self._get_rapidapi_headers(),
                params=params,
                ssl=ssl_context,
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

    async def get_parent_tweet_text(self, tweet_id: Any) -> None:
        async with aiohttp.ClientSession() as session:
            tweet_details = await self.fetch_tweet_details(tweet_id, session)
            logger.info("Fetched tweet tweet raw response: %s", tweet_details)
            if tweet_details and "includes" in tweet_details:
                includes = tweet_details["includes"]
                logger.info("Fetched tweet includes: %s", includes)
                tweets_array = includes.get("tweets", [])
                if tweets_array:
                    parent_tweet_text = tweets_array[0].get("text", "")
                    logger.info("Fetched parent tweet text: %s", parent_tweet_text)
                    return parent_tweet_text
                logger.warning("No tweets found in includes.")
            else:
                logger.info("Failed to fetch tweet details for tweet: %s", tweet_id)
        return None

    async def get_replied_tweet_id(self, tweet_id: Any) -> None:
        async with aiohttp.ClientSession() as session:
            tweet_details = await self.fetch_tweet_details(tweet_id, session)
            if tweet_details and "data" in tweet_details:
                data = tweet_details["data"]
                referenced = data.get("referenced_tweets", [])
                for ref in referenced:
                    if ref.get("type") == "replied_to":
                        replied_to_id = ref.get("id")
                        logger.info("Found replied-to tweet ID: %s", replied_to_id)
                        return replied_to_id
            logger.info("Failed to extract replied-to tweet ID for tweet: %s", tweet_id)
        return None

    async def handle_mention(self, tweet: dict[str, Any]) -> None:
        # 1) Get the tweet ID where ScribeChain was mentioned
        tweet_id = tweet.get("id_str", "")

        if self.rate_limit_reset_time and time.time() < self.rate_limit_reset_time:
            logger.warning("Rate limit in effect, skipping tweet: %s", tweet_id)
            return

        # 2) Check if the tweet contains the trigger word "summarize"
        if "summarize" in tweet.get("full_text", "").lower():
            logger.info("Trigger detected in tweet: %s", tweet_id)

            # 3) Get the text containing the link to the X space from the parent tweet
            parent_tweet_text = await self.get_parent_tweet_text(tweet_id)
            if parent_tweet_text is None:
                logger.error("Parent tweet text is None for tweet: %s", tweet_id)
                return

            # 4) Extract and resolve the X Space URL from the parent tweet text.
            space_url = await utils.extract_link_from_text(parent_tweet_text)
            if space_url:
                logger.info("Extracted X Space URL: %s", space_url)
                # 5) Pass the resolved space URL to download the space audio.
                await self.download_space_audio(space_url)
            else:
                logger.warning("No X Space URL found in parent tweet: %s", tweet_id)

            # 4) Locate the downloaded .m4a file in the current directory
            m4a_file_path = next(Path().glob("*.m4a"), None)

            if not m4a_file_path:
                logger.error("No .m4a file found after donwloading space audio")
                return
            logger.info("Using audio file: %s", m4a_file_path)

            # 5) Read the audio file data
            m4a_file_path = next(Path().glob("*.m4a"), None)
            if m4a_file_path:  # Check if a file was found
                audio_file_ref = self.ai_provider.upload_audio_file(str(m4a_file_path))
            else:
                # Handle the case where no file was found. For example:
                audio_file_ref = None  # or raise an exception
                logger.warning("No m4a file found")

            # 6) Feed the audio data to Gemini
            summary_response = self.ai_provider.generate_multimodal_content(
                prompt="You will tell me what this audio clip is about",
                audio_file_ref=audio_file_ref,
            )

            # 7) Log and post the summary (if available)
            if summary_response and summary_response.text:
                logger.info("Gemini Summary: %s", summary_response.text)
                summary_tweet_id = await self.post_reply(
                    summary_response.text, tweet_id
                )
                logger.debug("Summary tweet ID: %s", summary_tweet_id)
                # Save context for follow-ups
                self.conversation_context[summary_tweet_id] = {
                    "summary": summary_response.text,
                    "audio_ref": audio_file_ref,
                }
            else:
                logger.error("No summary returned from Gemini.")
        else:
            # If the tweet is a reply, handle it in a separate method.
            await self.handle_followup(tweet)

    async def handle_followup(self, tweet: dict[str, Any]) -> None:
        tweet_id = tweet.get("id_str", "")
        in_reply_to_id = await self.get_replied_tweet_id(tweet_id)

        if not in_reply_to_id:
            logger.debug(
                "Tweet %s is not a reply to summary tweet; resuming.", tweet_id
            )
            return

        logger.debug("Tweet %s replied to %s.", tweet_id, in_reply_to_id)
        context = self.conversation_context.get(in_reply_to_id)
        if not context:
            logger.warning(
                "No conversation summary tweet found for reply: %s", tweet_id
            )
            return

        # Build a follow-up prompt that includes the summary as context.
        followup_question = tweet.get("full_text", "")
        prompt = (
            "You are ScribeChain, an AI social agent on X/Twitter with access "
            "to the full content of a specific X Space audio file. "
            "Your task is to answer the following follow-up question using "
            "only the information contained in the referenced audio file. "
            "Do not bring in any external context, opinions, or unrelated data."
            " If the question calls for specific details (such as names, quotes,"
            " or timestamps), include them only if they appear in the audio. "
            "Your answer should be clear, concise, and directly address the "
            "question based solely on the audio content provided.\n\n"
            f"Audio File Reference: {context['audio_ref']}\n\n"
            f"User's Follow-up Question: {followup_question}"
        )
        logger.info("Sending follow-up prompt: %s", prompt)

        # Use send_message to generate a follow-up answer
        response = self.ai_provider.send_message(prompt)
        if response and response.text:
            logger.info("Follow-up response: %s", response.text)
            await self.post_reply(response.text, tweet_id)
        else:
            logger.error("No response returned for follow-up question.")

    async def fetch_tweet_details(
        self, tweet_id: str, session: aiohttp.ClientSession
    ) -> dict[str, Any] | None:
        # Only request the referenced_tweets field
        fields = "referenced_tweets"
        url = (
            self.twitter_api_base
            + f"/tweets/{tweet_id}?tweet.fields={fields}"
            + "&expansions=referenced_tweets.id"
        )
        headers = self._get_twitter_api_headers("GET", url)
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        try:
            async with session.get(url, headers=headers, ssl=ssl_context) as response:
                if response.status == HTTP_OK:
                    return await response.json()
                if response.status == HTTP_RATE_LIMIT:
                    retry_after = response.headers.get("retry-after")
                    wait_time = (
                        int(retry_after) if retry_after else 900
                    )  # default to 15 minutes
                    self.rate_limit_reset_time = time.time() + wait_time
                    logger.warning(
                        "Rate limit exceeded when fetching tweet details"
                        "for tweet: {tweet_id}. Wait for {wait_time} seconds."
                    )
                    return None
                logger.error("Failed to fetch tweet details", status=response.status)
                return None
        except Exception:
            logger.exception("Error fetching tweet details")
            return None

    async def download_space_audio(self, space_url: str) -> None:
        """Download the audio from a completed X Space given its URL asynchronously."""

        if self.cookie_path is None:
            logger.error("Missing cookie path")
            return

        logger.info("Beginning X space download")
        logger.info("PATH: %s", os.environ["PATH"])
        cmd = [
            "/bin/sh",
            "-c",
            f"/app/.venv/bin/twspace_dl -i {space_url} -c {self.cookie_path}",
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                logger.info("Space audio downloaded successfully: %s", stdout.decode())
            else:
                logger.error("Error downloading space audio: %s", stderr.decode())
        except Exception:
            logger.exception("Exception while downloading space audio.")

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
