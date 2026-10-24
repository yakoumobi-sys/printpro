"""ASGI entrypoint for hosts that look for a module-level ``app`` (Vercel).

Locally, keep using ``./run.sh`` or ``python -m printpro serve``.
"""

from printpro.server import create_app, default_workspace

app = create_app(default_workspace())
