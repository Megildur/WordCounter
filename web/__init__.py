from __future__ import annotations
import logging
from aiohttp import web
from aiohttp.abc import AbstractAccessLogger
from cogs.utils.config import SITE_URL, WEB_HOST, WEB_PORT
from web import dashboard, pages
from web.core import ROOT, site_middleware

log = logging.getLogger("web")


class ErrorAccessLogger(AbstractAccessLogger):
    def log(self, request, response, time) -> None:
        if response.status >= 500:
            self.logger.warning("%s %s returned %s in %.0f ms", request.method, request.path, response.status, time * 1000)


def create_app(bot) -> web.Application:
    app = web.Application(middlewares=[site_middleware])
    app["bot"] = bot
    app.router.add_static("/static", str(ROOT / "static"), name="static")
    app.router.add_routes(pages.routes)
    app.router.add_routes(dashboard.routes)
    return app


async def setup(bot) -> None:
    runner = web.AppRunner(create_app(bot), access_log_class=ErrorAccessLogger)
    await runner.setup()
    try:
        await web.TCPSite(runner, WEB_HOST, WEB_PORT).start()
    except OSError as error:
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
