import gzip
import zlib
from http import HTTPStatus
from starlette.types import ASGIApp, Receive, Scope, Send


MAX_DECOMPRESSED_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_RAW_BODY_SIZE = 10 * 1024 * 1024  # 10 MB


class BodyMiddleware:
    """
    ASGI middleware to handle request body parsing, including decompression
    of gzipped bodies and protection against gzip bomb expansion.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def _create_processed_body_receiver(self, body_bytes: bytes) -> Receive:
        """
        Creates a new `receive` callable that yields the given body_bytes once,
        and then empty chunks, effectively replacing the original request body stream.
        """
        processed_body_yielded = False

        async def _receive_processed_body() -> dict:
            nonlocal processed_body_yielded
            if not processed_body_yielded:
                processed_body_yielded = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        return _receive_processed_body

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_encoding = None
        for header_name, header_value in scope["headers"]:
            if header_name.lower() == b"content-encoding":
                content_encoding = header_value.decode("latin-1").lower()
                break

        if content_encoding == "gzip":
            await self._handle_gzipped_body(scope, receive, send)
        else:
            await self._handle_plain_body(scope, receive, send)

    async def _handle_gzipped_body(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Handles requests with 'Content-Encoding: gzip', performing decompression
        and applying a maximum decompressed size limit.
        """
        decompressor = gzip.Decompressor()
        decompressed_data = bytearray()
        decompressed_size = 0

        try:
            while True:
                message = await receive()
                if message["type"] == "http.request":
                    chunk = message.get("body", b"")

                    if chunk:
                        try:
                            decompressed_chunk = decompressor.decompress(chunk)
                        except zlib.error as e:
                            await self._send_error_response(send, HTTPStatus.BAD_REQUEST, f"Invalid gzip data: {e}")
                            return

                        decompressed_data.extend(decompressed_chunk)
                        decompressed_size = len(decompressed_data)

                        if decompressed_size > MAX_DECOMPRESSED_SIZE:
                            await self._send_error_response(
                                send,
                                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                "Decompressed request body exceeds maximum allowed size."
                            )
                            return

                    if not message.get("more_body", False):
                        try:
                            remaining_decompressed = decompressor.flush()
                        except zlib.error as e:
                            await self._send_error_response(send, HTTPStatus.BAD_REQUEST, f"Invalid gzip data (flush error): {e}")
                            return

                        decompressed_data.extend(remaining_decompressed)
                        decompressed_size = len(decompressed_data)

                        if decompressed_size > MAX_DECOMPRESSED_SIZE:
                            await self._send_error_response(
                                send,
                                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                "Decompressed request body exceeds maximum allowed size after final flush."
                            )
                            return

                        scope["body"] = bytes(decompressed_data)
                        new_receive = await self._create_processed_body_receiver(scope["body"])
                        
                        await self.app(scope, new_receive, send)
                        return

                elif message["type"] == "http.disconnect":
                    await self._send_error_response(send, HTTPStatus.BAD_REQUEST, "Client disconnected during body reception.")
                    return
        except Exception as e:
            await self._send_error_response(send, HTTPStatus.INTERNAL_SERVER_ERROR, f"An unexpected error occurred during gzipped body processing: {e}")
            return

    async def _handle_plain_body(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Handles requests with no specific Content-Encoding (plain body),
        applying a maximum raw body size limit.
        """
        raw_body = bytearray()
        raw_body_length = 0

        try:
            while True:
                message = await receive()
                if message["type"] == "http.request":
                    chunk = message.get("body", b"")
                    raw_body.extend(chunk)
                    raw_body_length = len(raw_body)

                    if raw_body_length > MAX_RAW_BODY_SIZE:
                        await self._send_error_response(
                            send,
                            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            "Request body exceeds maximum allowed size."
                        )
                        return

                    if not message.get("more_body", False):
                        scope["body"] = bytes(raw_body)
                        new_receive = await self._create_processed_body_receiver(scope["body"])
                        
                        await self.app(scope, new_receive, send)
                        return

                elif message["type"] == "http.disconnect":
                    await self._send_error_response(send, HTTPStatus.BAD_REQUEST, "Client disconnected during body reception.")
                    return
        except Exception as e:
            await self._send_error_response(send, HTTPStatus.INTERNAL_SERVER_ERROR, f"An unexpected error occurred during plain body processing: {e}")
            return

    async def _send_error_response(self, send: Send, status_code: HTTPStatus, detail: str) -> None:
        """
        Sends an HTTP error response to the client and closes the connection.
        """
        await send(
            {
                "type": "http.response.start",
                "status": status_code.value,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"content-length", str(len(detail)).encode("latin-1")),
                    (b"connection", b"close"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": detail.encode("utf-8"),
                "more_body": False,
            }
        )