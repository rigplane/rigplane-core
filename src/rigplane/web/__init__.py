"""icom-lan Web UI — WebSocket + HTTP server package.

Provides a built-in web interface for real-time spectrum/waterfall display,
radio control, and audio streaming accessible from any browser on the LAN.

Entrypoint::

    from icom_lan.web.server import WebServer, WebConfig
    server = WebServer(radio, WebConfig(port=8080))
    await server.serve_forever()
"""

from .server import WebConfig, WebServer, run_web_server  # noqa: TID251

__all__ = ["WebConfig", "WebServer", "run_web_server"]
