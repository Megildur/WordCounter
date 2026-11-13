"""The website, served by aiohttp from the bot's own process.

`setup` (this package loads as a bot extension) starts it on WEB_HOST and WEB_PORT beside the bot, and `teardown` stops it. `web/pages.py` holds the public pages and the login, `web/dashboard.py` the signed-in dashboard, `web/swearjar.py` the swear jar settings, and `web/core.py` the templates and middleware. Pages are Jinja templates with no JavaScript.
"""

from __future__ import annotations
import asyncio
import logging
from aiohttp import web
from aiohttp.abc import AbstractAccessLogger
from cogs.utils.config import SITE_URL, WEB_HOST, WEB_PORT
from web import dashboard, pages, swearjar
from web.core import ROOT, load_templates, site_middleware

log = logging.getLogger("web")


class ErrorAccessLogger(AbstractAccessLogger):
    """Logs only responses with a 5xx status, so the log isn't filled by every page view."""
    def log(self, request, response, time) -> None:
        if response.status >= 500:
            self.logger.warning("%s %s returned %s in %.0f ms", request.method, request.path, response.status, time * 1000)


class ProbeFilter(logging.Filter):
    """Drops the "Pause on PRI/Upgrade" error aiohttp logs when a client opens an HTTP/2 connection to this HTTP/1 server, as internet scanners do."""
    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[1] if record.exc_info else None
        return "Pause on PRI/Upgrade" not in str(error)


def create_app(bot) -> web.Application:
    app = web.Application(middlewares=[site_middleware])
    app["bot"] = bot
    app.router.add_static("/static", str(ROOT / "static"), name="static")
    app.router.add_routes(pages.routes)
    app.router.add_routes(dashboard.routes)
    app.router.add_routes(swearjar.routes)
    return app


async def setup(bot) -> None:
    # Templates are compiled up front, in a thread, so the first page isn't slow and a broken template fails at startup.
    await asyncio.to_thread(load_templates)
    server_log = logging.getLogger("aiohttp.server")
    # Checked first, because reloading this extension would otherwise add a second filter.
    if not any(isinstance(existing, ProbeFilter) for existing in server_log.filters):
        server_log.addFilter(ProbeFilter())
    runner = web.AppRunner(create_app(bot), access_log_class=ErrorAccessLogger)
    await runner.setup()
    try:
        await web.TCPSite(runner, WEB_HOST, WEB_PORT).start()
    except OSError as error:
        # If the port is taken the bot keeps running without its website.
        log.error("Website couldn't start on %s:%s: %s", WEB_HOST, WEB_PORT, error)
        await runner.cleanup()
        return
    bot.web_runner = runner
    log.info("Website running on %s:%s for %s", WEB_HOST, WEB_PORT, SITE_URL)


async def teardown(bot) -> None:
    runner = getattr(bot, "web_runner", None)
    if runner is not None:
        bot.web_runner = None
        await runner.cleanup()
