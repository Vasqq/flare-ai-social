import time
from typing import Any, cast

import structlog
from telegram import Bot, Chat, Message, MessageEntity, Update, User
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from flare_ai_social.ai import BaseAIProvider

logger = structlog.get_logger(__name__)


ERR_API_TOKEN_NOT_PROVIDED = "Telegram API token not provided."
ERR_BOT_NOT_INITIALIZED = "Bot not initialized."
ERR_UPDATER_NOT_INITIALIZED = "Updater was not initialized"


class TelegramBot:
    def __init__(
        self,
        ai_provider: BaseAIProvider,
        api_token: str,
        allowed_user_ids: list[int] | None = None,
        polling_interval: int = 5,
    ) -> None:
        """
        Initialize the Telegram bot.

        Args:
            ai_provider: The AI provider to use for generating responses.
            api_token: Telegram Bot API token.
            allowed_user_ids: Optional list of allowed Telegram user.
                              If empty or None, all users are allowed.
            polling_interval: Time between update checks in seconds.
        """
        self.ai_provider = ai_provider
        self.api_token = api_token
        self.allowed_user_ids = (
            allowed_user_ids or []
        )  # Empty list means no restrictions
        self.polling_interval = polling_interval
        self.application: Application | None = None
        self.me: User | None = None  # Will store bot's own information

        # Track last processed update time for each chat
        self.last_processed_time: dict[int, float] = {}

        if not self.api_token:
            raise ValueError(ERR_API_TOKEN_NOT_PROVIDED)

        if self.allowed_user_ids:
            logger.info(
                "TelegramBot initialized with access restrictions",
                allowed_users_count=len(self.allowed_user_ids),
                polling_interval=polling_interval,
            )
        else:
            logger.info(
                "TelegramBot initialized without access restrictions (public bot)",
                polling_interval=polling_interval,
            )

    def _is_user_allowed(self, user_id: int) -> bool:
        """
        Check if a user is allowed to use the bot.

        Args:
            user_id: The Telegram user ID to check.

        Returns:
            True if the user is allowed, False otherwise.
        """
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids

    def _safe_dict(self, obj: object | None) -> dict[str, Any] | str | None:
        """Convert an object to a dictionary, handling None values."""
        if obj is None:
            return None
        if hasattr(obj, "to_dict"):
            return obj.to_dict()  # type: ignore[union-attr]
        return str(obj)

    def _dump_update(self, update: Update) -> dict[str, Any]:
        """Convert update to a dictionary for debugging."""
        if not update:
            return {"error": "Update is None"}
        try:
            result: dict[str, Any] = {}
            if update.message:
                message: Message = update.message
                result["message"] = {
                    "message_id": message.message_id,
                    "from_user": self._safe_dict(message.from_user),
                    "chat": self._safe_dict(message.chat),
                    "date": str(message.date),
                    "text": message.text,
                    "has_entities": bool(message.entities),
                }
                if message.entities:
                    # Cast result["message"] to dict[str, Any] for type safety
                    msg_dict = cast(dict[str, Any], result["message"])
                    msg_dict["entities"] = [
                        {
                            "type": e.type,
                            "offset": e.offset,
                            "length": e.length,
                            "text": (
                                message.text[e.offset : e.offset + e.length]
                                if message.text
                                else None
                            ),
                        }
                        for e in message.entities
                    ]
                if message.reply_to_message:
                    reply: Message = message.reply_to_message
                    result["message"]["reply_to_message"] = {
                        "message_id": reply.message_id,
                        "from_user": self._safe_dict(reply.from_user),
                        "text": reply.text,
                    }
                return result
            return result
        except Exception as e:
            logger.exception("Error dumping update")
            return {"error": str(e)}
        else:
            return {"error": "Update is None"}

    async def catch_all(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Catch-all handler to log any received updates."""
        try:
            logger.warning(
                "Catch all received updates",
                update_type=str(type(update)),
                has_message=(update.message is not None),
                chat_type=(
                    update.effective_chat.type if update.effective_chat else None
                ),
                message_text=(update.message.text if update.message else None),
            )
        except Exception:
            logger.exception("Error in catch_all handler")

    async def raw_update_handler(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Log raw update data for debugging."""
        try:
            _ = self._dump_update(update)
            if update.message and update.message.text and self.me and self.me.username:
                possible_mentions = [
                    f"@{self.me.username}",
                    self.me.username,
                    self.me.first_name,
                ]
                _ = any(
                    mention.lower() in update.message.text.lower()
                    for mention in possible_mentions
                )
        except Exception:
            logger.exception("Error in raw update handler")

    async def debug_command(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Debug command to verify bot is working."""
        if not update.effective_user or not update.message or not update.effective_chat:
            return

        if not self.me:
            try:
                self.me = await context.bot.get_me()
            except Exception:
                logger.exception("Failed to get bot info in debug command")

        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type

        logger.warning(
            "DEBUG COMMAND RECEIVED",
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=update.effective_user.id,
            bot_info=self._safe_dict(self.me),
        )

        await update.message.reply_text(
            f"Debug info:\n"
            f"- Bot username: {self.me.username if self.me else 'unknown'}\n"
            f"- Bot ID: {self.me.id if self.me else 'unknown'}\n"
            f"- Chat type: {chat_type}\n"
            f"- Chat ID: {chat_id}\n"
            f"- Message received successfully!"
        )

    async def start_command(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle the /start command."""
        if not update.effective_user or not update.message or not update.effective_chat:
            return

        user: User = update.effective_user
        user_id: int = user.id

        if not self._is_user_allowed(user_id):
            await update.message.reply_text(
                "Sorry, you're not authorized to use this bot."
            )
            logger.warning("Unauthorized access attempt", user_id=user_id)
            return

        await update.message.reply_text(
            f"👋 Hello {user.first_name}! I'm the Flare AI assistant. "
            f"Feel free to ask me anything about Flare Network."
        )
        logger.info("Start command handled", user_id=user_id)

    async def help_command(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle the /help command."""
        if not update.effective_user or not update.message or not update.effective_chat:
            return

        user_id: int = update.effective_user.id

        if not self._is_user_allowed(user_id):
            await update.message.reply_text(
                "Sorry, you're not authorized to use this bot."
            )
            logger.warning("Unauthorized help request", user_id=user_id)
            return

        help_text = (
            "🤖 *Flare AI Assistant Help*\n\n"
            "I can answer questions about Flare Network."
            "*Available commands:*\n"
            "/start - Start the conversation\n"
            "/help - Show this help message\n"
            "/debug - Show diagnostic information\n\n"
            "Simply send me a message, and I'll do my best to assist you!"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")

    async def _process_group_chat_mention(
        self, text: str, entities: tuple[MessageEntity, ...], update: Update
    ) -> tuple[bool, str]:
        """Check if bot was mentioned in group chat and return cleaned message text."""
        if not self.me or not self.me.username:
            return False, text

        # Check direct mentions using entities
        for entity in entities:
            if entity.type == "mention":
                mention_text = text[entity.offset : entity.offset + entity.length]
                bot_username = self.me.username.lower()
                mention_without_at = (
                    mention_text[1:].lower()
                    if mention_text.startswith("@")
                    else mention_text.lower()
                )
                if mention_without_at == bot_username:
                    return True, text.replace(mention_text, "").strip()

        # Check text-based mentions
        for variation in [f"@{self.me.username}", f"@{self.me.username.lower()}"]:
            if variation.lower() in text.lower():
                idx = text.lower().find(variation.lower())
                if idx >= 0:
                    actual_length = len(variation)
                    actual_mention = text[idx : idx + actual_length]
                    return True, text.replace(actual_mention, "").strip()

        # Check if message is a reply to bot
        if (
            update.message
            and update.message.reply_to_message
            and update.message.reply_to_message.from_user
            and self.me
            and update.message.reply_to_message.from_user.id == self.me.id
        ):
            return True, text

        return False, text

    async def _handle_unauthorized_access(
        self, update: Update, chat_type: str, user_id: int, chat_id: int | str
    ) -> bool:
        """Handle unauthorized user access."""
        if chat_type == "private" and update.message:
            await update.message.reply_text(
                "Sorry, you are not authorized to use this bot."
            )
        logger.warning(
            "Unauthorized message",
            user_id=user_id,
            chat_id=chat_id,
            is_group=chat_type != "private",
        )
        return True

    async def handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming messages and generate AI responses."""
        if not update.message or not update.effective_user or not update.effective_chat:
            logger.warning("Missing message, user, or chat; skipping")
            return

        self._dump_update(update)

        user: User = update.effective_user
        user_id: int = user.id
        chat: Chat = update.effective_chat
        chat_id: int | str = chat.id
        chat_type: str = chat.type

        # Get bot info if not already available
        if not self.me:
            try:
                self.me = await context.bot.get_me()
                logger.info(
                    "Bot information retrieved",
                    bot_id=self.me.id,
                    bot_username=self.me.username,
                    bot_first_name=self.me.first_name,
                )
            except Exception:
                logger.exception("Failed to get bot info")
                return

        if not update.message.text:
            logger.debug("Skipping message without text")
            return

        var_text: str = update.message.text
        is_group_chat = chat_type in ["group", "supergroup", "channel"]

        # Log message details
        entities: tuple[MessageEntity, ...] = update.message.entities
        logger.info(
            "Received message details",
            user_id=user_id,
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=update.message.message_id,
            message_text=var_text,
            has_entities=bool(entities),
            entity_count=len(entities),
            bot_username=self.me.username if self.me else "unknown",
        )

        # Handle group chat mentions
        if is_group_chat:
            is_mentioned, var_text = await self._process_group_chat_mention(
                var_text, entities, update
            )
            if not is_mentioned:
                logger.debug(
                    "Ignoring group message (not mentioned)",
                    chat_id=chat_id,
                    user_id=user_id,
                )
                return
            if not var_text:
                var_text = "Hello"
                logger.info(
                    "Empty mention received, responding with greeting",
                    chat_id=chat_id,
                    user_id=user_id,
                )

        # Check user authorization
        if not self._is_user_allowed(
            user_id
        ) and await self._handle_unauthorized_access(
            update, chat_type, user_id, chat_id
        ):
            return

        # Generate and send AI response
        logger.info(
            "Processing message",
            user_id=user_id,
            chat_id=chat_id,
            is_group=is_group_chat,
            message_text=var_text,
        )

        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            ai_response = self.ai_provider.generate_content(var_text)
            response_text = ai_response.text

            chat_id_key = int(chat_id) if isinstance(chat_id, str) else chat_id
            self.last_processed_time[chat_id_key] = time.time()
            await update.message.reply_text(response_text)
            logger.info(
                "Sent AI response",
                chat_id=chat_id,
                user_id=user_id,
                is_group=is_group_chat,
            )
        except Exception:
            logger.exception("Error generating AI response")
            await update.message.reply_text(
                "I'm having trouble processing your request. Please try again later."
            )

    async def error_handler(
        self, update: object, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle errors in the telegram bot."""
        logger.error("Telegram error", error=context.error, update=update)

    async def initialize(self) -> None:
        """Initialize the bot application."""
        logger.info("Initializing Telegram bot")

        # Build the application with default settings
        builder = Application.builder().token(self.api_token)
        self.application = builder.build()

        try:
            self.me = await Bot(self.api_token).get_me()
            logger.info(
                "Bot information retrieved",
                bot_id=self.me.id,
                bot_username=self.me.username,
                bot_first_name=self.me.first_name,
            )
        except TelegramError:
            logger.exception("Failed to get bot info")
            self.me = None

        # Add handlers in the correct order
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("debug", self.debug_command))

        # Add message handler for text messages
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )

        # Add error handler
        self.application.add_error_handler(self.error_handler)

        # Initialize the application
        await self.application.initialize()
        logger.info("Telegram bot initialized successfully")

    async def start_polling(self) -> None:
        """Start polling for updates."""
        if not self.application:
            raise RuntimeError(ERR_BOT_NOT_INITIALIZED)

        logger.info("Starting Telegram bot polling")

        await self.application.start()

        # Type assertion to help Pyright understand that updater exists
        if self.application.updater is None:
            raise RuntimeError(ERR_UPDATER_NOT_INITIALIZED)

        await self.application.updater.start_polling(
            poll_interval=self.polling_interval,
            timeout=30,
            bootstrap_retries=-1,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30,
            pool_timeout=30,
        )

    async def start(self) -> None:
        """Start the Telegram bot."""
        try:
            logger.info("Starting Telegram bot")
            await self.initialize()
            await self.start_polling()
        except KeyboardInterrupt:
            logger.info("Telegram bot stopped by user")
        except Exception:
            logger.exception("Fatal error in Telegram bot")
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Shut down the bot."""
        if self.application:
            logger.info("Shutting down Telegram bot")
            await self.application.stop()
            await self.application.shutdown()
