# =============================================================
# Module: Remote MCP auth helpers
#
# Keeps Ombre Brain's Claude.ai/remote-MCP authentication glue out
# of server.py so upstream merges only need a small integration point.
# =============================================================

import base64
import hashlib
import html
import hmac
import logging
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode

logger = logging.getLogger("ombre_brain")


class McpApiKeyMiddleware:
    """
    Protect remote MCP transport endpoints with:
      Authorization: Bearer <OMBRE_MCP_API_KEY>

    If OMBRE_MCP_API_KEY is empty, auth is disabled for local/dev compatibility.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        method = scope.get("method") or ""
        protected = path in ("/mcp", "/sse", "/messages") or path.startswith("/messages/")
        if not protected or method.upper() == "OPTIONS":
            await self.app(scope, receive, send)
            return

        key = os.environ.get("OMBRE_MCP_API_KEY", "").strip()
        if not key:
            logger.warning("OMBRE_MCP_API_KEY is not set; MCP auth is disabled")
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin1").lower(): v.decode("latin1")
            for k, v in scope.get("headers", [])
        }
        auth = headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token or not hmac.compare_digest(token, key):
            body = b'{"error":"Unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


_oauth_codes: dict[str, dict] = {}


def _oauth_public_base(request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"{proto}://{host}".rstrip("/")


async def _read_oauth_body(request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _oauth_cleanup_codes() -> None:
    now = time.time()
    expired = [
        code
        for code, payload in _oauth_codes.items()
        if payload.get("expires_at", 0) < now
    ]
    for code in expired:
        _oauth_codes.pop(code, None)


def _oauth_html_attr(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def _oauth_error_page(message: str, status_code: int = 400):
    from starlette.responses import HTMLResponse

    safe_message = html.escape(message, quote=False)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Ombre Brain 授权失败</title></head>
<body style="font-family: sans-serif; padding: 32px;">
  <h1>Ombre Brain 授权失败</h1>
  <p>{safe_message}</p>
</body>
</html>""",
        status_code=status_code,
    )


def register_oauth_routes(mcp) -> None:
    """Register OAuth endpoints required by Claude.ai remote MCP."""

    @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
    async def oauth_authorization_server(request):
        from starlette.responses import JSONResponse

        base = _oauth_public_base(request)
        return JSONResponse(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "registration_endpoint": f"{base}/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code"],
                "code_challenge_methods_supported": ["S256", "plain"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
        )

    @mcp.custom_route("/register", methods=["POST"])
    async def oauth_register(request):
        from starlette.responses import JSONResponse

        try:
            body = await request.json()
            if not isinstance(body, dict):
                body = {}
        except Exception:
            body = {}

        now = int(time.time())
        return JSONResponse(
            {
                **body,
                "client_id": body.get("client_id")
                or f"ob-client-{secrets.token_hex(8)}",
                "client_id_issued_at": now,
                "client_secret_expires_at": 0,
            },
            status_code=201,
        )

    @mcp.custom_route("/authorize", methods=["GET"])
    async def oauth_authorize_form(request):
        from starlette.responses import HTMLResponse

        params = request.query_params
        redirect_uri = params.get("redirect_uri", "")
        client_id = params.get("client_id", "")
        if not redirect_uri or not client_id:
            return _oauth_error_page("缺少 redirect_uri 或 client_id。", 400)

        fields = {
            "response_type": params.get("response_type", "code"),
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": params.get("scope", ""),
            "state": params.get("state", ""),
            "code_challenge": params.get("code_challenge", ""),
            "code_challenge_method": params.get("code_challenge_method", "S256"),
        }
        hidden = "\n".join(
            f'<input type="hidden" name="{_oauth_html_attr(key)}" value="{_oauth_html_attr(value)}">'
            for key, value in fields.items()
        )
        return HTMLResponse(
            f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ombre Brain 授权</title>
  <style>
    body {{ font-family: sans-serif; min-height: 100vh; margin: 0; display: grid; place-items: center; background: #101820; color: #f3ead7; }}
    main {{ width: min(420px, calc(100vw - 40px)); padding: 28px; border: 1px solid rgba(243,234,215,.24); border-radius: 18px; background: rgba(255,255,255,.06); }}
    input, button {{ width: 100%; box-sizing: border-box; font-size: 16px; padding: 12px 14px; border-radius: 10px; }}
    input {{ border: 1px solid rgba(243,234,215,.32); background: rgba(0,0,0,.24); color: #fff; }}
    button {{ margin-top: 14px; border: 0; background: #d9b56d; color: #111; font-weight: 700; cursor: pointer; }}
    p {{ color: rgba(243,234,215,.78); line-height: 1.5; }}
  </style>
</head>
<body>
  <main>
    <h1>授权 Ombre Brain</h1>
    <p>请输入 OB MCP API Key。授权成功后，Claude.ai 会使用该 Key 访问 <code>/mcp</code>。</p>
    <form method="post" action="/authorize">
      {hidden}
      <input name="api_key" type="password" autocomplete="current-password" placeholder="OB MCP API Key" required autofocus>
      <button type="submit">授权</button>
    </form>
  </main>
</body>
</html>"""
        )

    @mcp.custom_route("/authorize", methods=["POST"])
    async def oauth_authorize_submit(request):
        from starlette.responses import RedirectResponse

        data = await _read_oauth_body(request)
        configured_key = os.environ.get("OMBRE_MCP_API_KEY", "").strip()
        submitted_key = data.get("api_key", "")
        if not configured_key:
            return _oauth_error_page("服务器未配置 OMBRE_MCP_API_KEY。", 500)
        if not hmac.compare_digest(submitted_key, configured_key):
            return _oauth_error_page("API Key 不正确。", 401)

        redirect_uri = data.get("redirect_uri", "")
        client_id = data.get("client_id", "")
        response_type = data.get("response_type", "code")
        if response_type != "code" or not redirect_uri or not client_id:
            return _oauth_error_page("OAuth 请求参数不完整。", 400)

        _oauth_cleanup_codes()
        code = secrets.token_urlsafe(32)
        _oauth_codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": data.get("code_challenge", ""),
            "code_challenge_method": data.get("code_challenge_method", "S256"),
            "expires_at": time.time() + 300,
        }

        params = {"code": code}
        if data.get("state"):
            params["state"] = data["state"]
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)

    @mcp.custom_route("/token", methods=["POST"])
    async def oauth_token(request):
        from starlette.responses import JSONResponse

        data = await _read_oauth_body(request)
        if data.get("grant_type") != "authorization_code":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        _oauth_cleanup_codes()
        code = data.get("code", "")
        payload = _oauth_codes.pop(code, None)
        if not payload:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        if data.get("redirect_uri") and data.get("redirect_uri") != payload.get("redirect_uri"):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        expected_challenge = payload.get("code_challenge", "")
        if expected_challenge:
            verifier = data.get("code_verifier", "")
            method = payload.get("code_challenge_method") or "S256"
            if method == "S256":
                digest = hashlib.sha256(verifier.encode("utf-8")).digest()
                actual_challenge = (
                    base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
                )
            else:
                actual_challenge = verifier
            if not verifier or not hmac.compare_digest(
                actual_challenge, expected_challenge
            ):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)

        key = os.environ.get("OMBRE_MCP_API_KEY", "").strip()
        if not key:
            return JSONResponse({"error": "server_error"}, status_code=500)

        return JSONResponse(
            {
                "access_token": key,
                "token_type": "Bearer",
            }
        )
