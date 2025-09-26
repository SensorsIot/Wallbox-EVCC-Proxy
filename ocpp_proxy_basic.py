#!/usr/bin/env python3
"""
WebSocket Proxy to clean malformed URLs from wallbox before forwarding to evcc
Fixes the double slash issue: ws://192.168.0.150:8887//AcTec001 -> ws://192.168.0.150:8887/AcTec001
Also handles OCPP subprotocol negotiation
"""

import asyncio
import websockets
import logging
import argparse
from websockets.legacy.server import serve

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WebSocketProxy:
    def __init__(self, listen_host="0.0.0.0", listen_port=8888, target_host="192.168.0.150", target_port=8887):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port

    def clean_url_path(self, path):
        """Clean malformed URL path by removing double slashes"""
        # Remove leading slash and clean up double slashes
        cleaned_path = path.lstrip('/')
        # Replace multiple slashes with single slash
        while '//' in cleaned_path:
            cleaned_path = cleaned_path.replace('//', '/')
        # Ensure single leading slash
        return '/' + cleaned_path

    async def handle_client(self, websocket, path):
        """Handle incoming client connection and proxy to target"""
        client_address = websocket.remote_address
        logger.info(f"New client connection from {client_address} requesting path: {path}")

        # Clean the path
        cleaned_path = self.clean_url_path(path)
        logger.info(f"Cleaned path: {cleaned_path}")

        # Build target URL
        target_url = f"ws://{self.target_host}:{self.target_port}{cleaned_path}"
        logger.info(f"Connecting to target: {target_url}")

        try:
            # Get subprotocols from the client
            try:
                client_subprotocols = getattr(websocket, 'subprotocols', [])
                logger.info(f"Client subprotocols: {client_subprotocols}")
            except:
                client_subprotocols = ["ocpp1.6"]  # Default to OCPP
                logger.info(f"Using default subprotocols: {client_subprotocols}")

            # Connect to the target server with OCPP subprotocol
            async with websockets.connect(
                target_url,
                subprotocols=["ocpp1.6"]
            ) as target_ws:
                logger.info(f"Connected to target server, starting proxy for {client_address}")
                logger.info(f"Target subprotocol: {target_ws.subprotocol}")

                # Create bidirectional proxy
                await asyncio.gather(
                    self.proxy_messages(websocket, target_ws, "client->target"),
                    self.proxy_messages(target_ws, websocket, "target->client"),
                    return_exceptions=True
                )
        except Exception as e:
            logger.error(f"Error connecting to target server {target_url}: {e}")
            await websocket.close(code=1002, reason=f"Target connection failed: {e}")

    async def proxy_messages(self, source_ws, dest_ws, direction):
        """Proxy messages between WebSocket connections"""
        try:
            async for message in source_ws:
                logger.debug(f"Proxying message ({direction}): {message[:100]}...")
                await dest_ws.send(message)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Connection closed ({direction})")
        except Exception as e:
            logger.error(f"Error proxying messages ({direction}): {e}")

    async def start_server(self):
        """Start the WebSocket proxy server"""
        logger.info(f"Starting WebSocket proxy server on {self.listen_host}:{self.listen_port}")
        logger.info(f"Proxying to {self.target_host}:{self.target_port}")

        server = await serve(
            self.handle_client,
            self.listen_host,
            self.listen_port,
            subprotocols=["ocpp1.6"]  # Support OCPP subprotocol
        )

        logger.info("WebSocket proxy server started successfully")
        return server

def main():
    parser = argparse.ArgumentParser(description="WebSocket Proxy for cleaning malformed URLs")
    parser.add_argument("--listen-host", default="0.0.0.0", help="Host to listen on (default: 0.0.0.0)")
    parser.add_argument("--listen-port", type=int, default=8888, help="Port to listen on (default: 8888)")
    parser.add_argument("--target-host", default="192.168.0.150", help="Target host (default: 192.168.0.150)")
    parser.add_argument("--target-port", type=int, default=8887, help="Target port (default: 8887)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    proxy = WebSocketProxy(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        target_host=args.target_host,
        target_port=args.target_port
    )

    async def run_proxy():
        server = await proxy.start_server()
        try:
            await server.wait_closed()
        except KeyboardInterrupt:
            logger.info("Shutting down proxy server...")
            server.close()
            await server.wait_closed()

    try:
        asyncio.run(run_proxy())
    except KeyboardInterrupt:
        logger.info("Proxy server stopped")

if __name__ == "__main__":
    main()