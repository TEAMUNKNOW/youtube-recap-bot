"""Handler registration."""

from bot.handlers.callback_handlers import register_callback_handlers
from bot.handlers.command_handlers import register_command_handlers
from bot.handlers.error_handlers import register_error_handlers
from bot.handlers.input_handlers import register_input_handlers
from bot.handlers.shorts_handlers import register_shorts_handlers


def register_all(app) -> None:
    register_command_handlers(app)
    register_shorts_handlers(app)
    register_input_handlers(app)
    register_callback_handlers(app)
    register_error_handlers(app)
