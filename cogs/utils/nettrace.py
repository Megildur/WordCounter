"""Log lines that say which phase of a request to Discord was slow: looking up its address, opening the connection, or waiting for its answer.

A press answered a second or two late shows "This interaction failed" even though nothing in the bot was slow. `trace_config` is handed to discord.py as `http_trace`, and writes a line only for a phase that took too long, naming the host and the kind of request but never the IDs or tokens in its address.
"""

from __future__ import annotations
import logging
import time
from typing import Any
from urllib.parse import urlsplit
import aiohttp

log = logging.getLogger(__name__)

LOOKUP_SLOW = 1.0
CONNECT_SLOW = 1.0
REQUEST_SLOW = 2.0


def route_of(url: Any) -> str:
    """The host and path of a request with every long or numeric part left out, for the log. Interaction and webhook tokens are in those parts, and they are secrets."""
    parts = urlsplit(str(url))
    path = "/".join("…" if len(segment) > 12 or segment.isdigit() else segment for segment in parts.path.split("/"))
    return f"{parts.hostname}{path}"


def _since(context: Any, name: str) -> float:
    return time.monotonic() - getattr(context, name, time.monotonic())


def trace_config() -> aiohttp.TraceConfig:
    trace = aiohttp.TraceConfig()

    async def lookup_start(session: Any, context: Any, params: Any) -> None:
        context.lookup_started = time.monotonic()

    async def lookup_end(session: Any, context: Any, params: Any) -> None:
        took = _since(context, "lookup_started")
        if took >= LOOKUP_SLOW:
            log.warning("Looking up the address of %s took %.1f s", params.host, took)

    async def connect_start(session: Any, context: Any, params: Any) -> None:
        context.connect_started = time.monotonic()

    async def connect_end(session: Any, context: Any, params: Any) -> None:
        took = _since(context, "connect_started")
        if took >= CONNECT_SLOW:
            log.warning("Opening a connection to Discord took %.1f s", took)

    async def request_start(session: Any, context: Any, params: Any) -> None:
        context.request_started = time.monotonic()

    async def request_end(session: Any, context: Any, params: Any) -> None:
        took = _since(context, "request_started")
        if took >= REQUEST_SLOW:
            log.warning("A %s request to %s took %.1f s to be answered (status %s)", params.method, route_of(params.url), took, params.response.status)

    trace.on_dns_resolvehost_start.append(lookup_start)
    trace.on_dns_resolvehost_end.append(lookup_end)
    trace.on_connection_create_start.append(connect_start)
    trace.on_connection_create_end.append(connect_end)
    trace.on_request_start.append(request_start)
    trace.on_request_end.append(request_end)
    return trace
