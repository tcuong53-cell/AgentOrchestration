import re
from typing import List, Tuple, Optional, Set
from starlette.types import ASGIApp, Scope, Receive, Send, Message
from starlette.responses import PlainTextResponse, Response


class CORSMiddleware:
    """
    ASGI middleware for handling Cross-Origin Resource Sharing (CORS) requests.

    This middleware intercepts HTTP requests to apply CORS rules. It prioritizes
    the handling of OPTIONS preflight requests, ensuring they are validated
    and responded to early, preventing them from traversing further down
    the middleware stack or reaching the main application logic unnecessarily.
    This prevents potential issues where preflight requests might bypass or
    interfere with authentication or other security measures intended for
    actual data requests.
    """

    def __init__(
        self,
        app: ASGIApp,
        allow_origins: List[str] = None,
        allow_methods: List[str] = None,
        allow_headers: List[str] = None,
        allow_credentials: bool = False,
        allow_origin_regex: Optional[str] = None,
        expose_headers: List[str] = None,
        max_age: int = 600,
    ):
        self.app = app
        self.allow_origins = allow_origins or []
        
        # Store methods and headers as sets for O(1) lookup performance.
        # Convert to uppercase for methods and lowercase for headers for consistent comparison.
        default_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"]
        self._allow_methods_set: Set[str] = {m.upper() for m in (allow_methods or default_methods)}
        self._allow_headers_set: Set[str] = {h.lower() for h in (allow_headers or [])}
        
        self.allow_credentials = allow_credentials
        self.allow_origin_regex = re.compile(allow_origin_regex) if allow_origin_regex else None
        self.expose_headers = expose_headers or [] # This list is for actual responses
        self.max_age = max_age

        self._allow_all_origins = "*" in self.allow_origins
        self._allow_all_methods = "*" in self._allow_methods_set
        self._allow_all_headers = "*" in self._allow_headers_set

        # Pre-compute static base headers for preflight responses (currently only max-age)
        self._preflight_response_static_headers: List[Tuple[bytes, bytes]] = []
        self._preflight_response_static_headers.append(
            (b"access-control-max-age", str(self.max_age).encode("latin-1"))
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]
        }
        origin = request_headers.get("origin")
        method = scope["method"].upper() # Convert method to upper case early for consistency
        
        # Determine if origin is allowed. This logic applies to both preflight and actual requests.
        is_origin_allowed = False
        if origin:
            if self._allow_all_origins:
                is_origin_allowed = True
            elif origin in self.allow_origins:
                is_origin_allowed = True
            elif self.allow_origin_regex and self.allow_origin_regex.match(origin):
                is_origin_allowed = True
        
        # --- Critical Fix: Handle OPTIONS preflight requests first and exit early ---
        if method == "OPTIONS":
            access_control_request_method = request_headers.get("access-control-request-method")
            access_control_request_headers = request_headers.get("access-control-request-headers")

            if access_control_request_method:  # Presence of this header indicates a preflight request
                # 1. Validate Origin
                if not is_origin_allowed:
                    response = PlainTextResponse("CORS origin not allowed", status_code=403)
                    await response(scope, receive, send)
                    return

                # Prepare dynamic preflight response headers by starting with static ones
                response_headers = list(self._preflight_response_static_headers)

                # 2. Add Access-Control-Allow-Origin
                # Always echo the specific origin if allowed. Using '*' with credentials is not allowed.
                # Echoing is generally safer and more flexible.
                if origin: # is_origin_allowed implies origin is not None
                    response_headers.append((b"access-control-allow-origin", origin.encode("latin-1")))
                if self.allow_credentials:
                    response_headers.append((b"access-control-allow-credentials", b"true"))

                # 3. Validate and add Access-Control-Allow-Methods
                requested_methods = {m.strip().upper() for m in access_control_request_method.split(",")}
                
                # Determine methods that are both requested and allowed
                allowed_methods_to_respond: Set[str] = set()
                if self._allow_all_methods:
                    allowed_methods_to_respond = requested_methods # If all methods allowed, echo all requested
                else:
                    allowed_methods_to_respond = requested_methods.intersection(self._allow_methods_set)
                
                # If the set of allowed methods to respond does not cover all requested methods,
                # then some requested methods were not allowed. Fail the preflight.
                if not requested_methods.issubset(allowed_methods_to_respond):
                    response = PlainTextResponse("CORS method(s) not allowed", status_code=403)
                    await response(scope, receive, send)
                    return
                
                # If validation passes, add the header with the allowed methods (which are a subset of requested)
                if allowed_methods_to_respond:
                    response_headers.append(
                        (b"access-control-allow-methods", b", ".join([m.encode("latin-1") for m in sorted(list(allowed_methods_to_respond))]))
                    )

                # 4. Validate and add Access-Control-Allow-Headers
                if access_control_request_headers:
                    requested_headers = {h.strip().lower() for h in access_control_request_headers.split(",")}
                    
                    # Determine headers that are both requested and allowed
                    allowed_headers_to_respond: Set[str] = set()
                    if self._allow_all_headers:
                        allowed_headers_to_respond = requested_headers # If all headers allowed, echo all requested
                    else:
                        allowed_headers_to_respond = requested_headers.intersection(self._allow_headers_set)

                    # If the set of allowed headers to respond does not cover all requested headers,
                    # then some requested headers were not allowed. Fail the preflight.
                    if not requested_headers.issubset(allowed_headers_to_respond):
                        response = PlainTextResponse("CORS header(s) not allowed", status_code=403)
                        await response(scope, receive, send)
                        return
                    
                    # If validation passes, add the header with the allowed headers (which are a subset of requested)
                    if allowed_headers_to_respond:
                        response_headers.append(
                            (b"access-control-allow-headers", b", ".join([h.encode("latin-1") for h in sorted(list(allowed_headers_to_respond))]))
                        )
                
                # Preflight successful, send 200 OK response and terminate
                response = Response(status_code=200, headers=response_headers)
                await response(scope, receive, send)
                return
            # If it's an OPTIONS request but NOT a preflight (no Access-Control-Request-Method header),
            # it's treated as a simple request and falls through to the next block for actual requests.

        # For actual requests (GET, POST, etc.) or non-preflight OPTIONS requests:
        # If the origin is not allowed AND an origin header was present (i.e., it's a cross-origin request
        # but not permitted by CORS configuration), we do NOT add CORS headers. The browser will then
        # block the response due to missing Access-Control-Allow-Origin, but the request still reaches
        # the application. This allows the application to handle non-CORS requests as it sees fit
        # (e.g., return its own 401/403 based on authentication/authorization logic).
        if not is_origin_allowed and origin:
            await self.app(scope, receive, send)
            return

        # If origin IS allowed (or it's not a cross-origin request), we need to add CORS headers
        # to the actual response that comes from the application.
        # This requires overriding the send function to intercept the response headers.
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                message_headers = message["headers"]
                
                # Only add CORS response headers if the origin was present and allowed by middleware.
                if origin and is_origin_allowed:
                    response_origin_header_value = origin.encode("latin-1")
                    # If _allow_all_origins is true AND credentials are not allowed,
                    # we could send 'Access-Control-Allow-Origin: *'. However, echoing the specific
                    # origin is always valid and consistent with allow_credentials=True scenarios,
                    # so we prefer echoing for simplicity and robustness.
                    message_headers.append((b"access-control-allow-origin", response_origin_header_value))
                
                if self.allow_credentials and is_origin_allowed:
                    message_headers.append((b"access-control-allow-credentials", b"true"))
                
                if self.expose_headers and is_origin_allowed:
                    message_headers.append((b"access-control-expose-headers", b", ".join([h.encode("latin-1") for h in self.expose_headers])))
            await send(message)

        await self.app(scope, receive, send_wrapper)