#!/usr/bin/env python3
"""
WebSocket Proxy to clean malformed URLs from wallbox before forwarding to evcc
Fixes the double slash issue: ws://192.168.0.150:8887//AcTec001 -> ws://192.168.0.150:8887/AcTec001
Also handles OCPP subprotocol negotiation and fixes invalid timestamps
"""

import asyncio
import websockets
import logging
import logging.handlers
import argparse
import json
import re
from datetime import datetime
from websockets.legacy.server import serve

# Configure console logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configure file logging for OCPP messages
ocpp_logger = logging.getLogger('ocpp_messages')
ocpp_logger.setLevel(logging.INFO)
ocpp_handler = logging.handlers.RotatingFileHandler(
    '/home/OCPP-Proxy/ocpp_messages.log',
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5
)
ocpp_formatter = logging.Formatter('%(asctime)s - %(message)s')
ocpp_handler.setFormatter(ocpp_formatter)
ocpp_logger.addHandler(ocpp_handler)
ocpp_logger.propagate = False  # Don't send to root logger

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

    def fix_timestamp(self, message):
        """Fix malformed timestamps in OCPP messages"""
        try:
            # Parse the OCPP message
            data = json.loads(message)

            # Check if this is a message array with different structures:
            # [MessageType, MessageId, Action, Payload] (Call - length 4)
            # [MessageType, MessageId, Payload] (CallResult/CallError - length 3)
            if isinstance(data, list) and len(data) >= 3:
                if len(data) == 4:
                    # Call message: [MessageType, MessageId, Action, Payload]
                    message_type, message_id, action, payload = data[0], data[1], data[2], data[3]
                elif len(data) == 3:
                    # CallResult/CallError message: [MessageType, MessageId, Payload]
                    message_type, message_id, payload = data[0], data[1], data[2]

                # Look for timestamp fields in the payload
                if isinstance(payload, dict):
                    self._fix_timestamps_in_dict(payload)
                    self._fix_idtag_length(payload)
                    self._multiply_watts_by_10(payload)

                # Return the fixed message
                return json.dumps(data)

            return message
        except (json.JSONDecodeError, Exception) as e:
            # If we can't parse it, return the original message
            logger.debug(f"Could not parse message for timestamp fixing: {e}")
            return message

    def _fix_timestamps_in_dict(self, data):
        """Recursively fix timestamps in dictionary"""
        if not isinstance(data, dict):
            return

        for key, value in data.items():
            if isinstance(value, str) and self._is_malformed_timestamp(value):
                # Fix the malformed timestamp
                fixed_timestamp = self._create_valid_timestamp()
                logger.debug(f"Fixed timestamp: {value} -> {fixed_timestamp}")
                data[key] = fixed_timestamp
            elif isinstance(value, dict):
                self._fix_timestamps_in_dict(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._fix_timestamps_in_dict(item)

    def _is_malformed_timestamp(self, value):
        """Check if a string is a malformed timestamp"""
        # Check for empty strings or the specific malformed pattern: 0000-00-00T00:00:00.000Z
        if not value or value.strip() == "":
            return True
        return re.match(r'^0000-00-00T00:00:00\.000Z$', value) is not None

    def _create_valid_timestamp(self):
        """Create a valid ISO8601 timestamp for current time"""
        return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-3] + 'Z'

    def _fix_idtag_length(self, data):
        """Fix IdTag fields that exceed OCPP 20 character limit"""
        if not isinstance(data, dict):
            return

        for key, value in data.items():
            if key.lower() == 'idtag' and isinstance(value, str) and len(value) > 20:
                # If IdTag is a timestamp, create a shorter unique identifier
                if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', value):
                    # Extract time components and create shorter ID
                    # Format: HHMMSS + milliseconds (up to 3 digits)
                    try:
                        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        short_id = dt.strftime('%H%M%S') + str(dt.microsecond // 1000).zfill(3)
                        # Ensure it's max 20 chars and add prefix for uniqueness
                        short_id = f"tag{short_id[:16]}"  # "tag" + 16 digits = 19 chars max
                        logger.info(f"Fixed IdTag length: {value} -> {short_id}")
                        data[key] = short_id
                    except Exception as e:
                        # Fallback: just truncate to 20 chars
                        truncated = value[:20]
                        logger.info(f"Truncated IdTag: {value} -> {truncated}")
                        data[key] = truncated
                else:
                    # For non-timestamp IdTags, just truncate
                    truncated = value[:20]
                    logger.info(f"Truncated IdTag: {value} -> {truncated}")
                    data[key] = truncated
            elif isinstance(value, dict):
                self._fix_idtag_length(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._fix_idtag_length(item)

    def _multiply_watts_by_10(self, data):
        """Multiply watts values by 10 in meter values"""
        if not isinstance(data, dict):
            return

        # Look for MeterValues messages
        if 'meterValue' in data and isinstance(data['meterValue'], list):
            for meter_value in data['meterValue']:
                if isinstance(meter_value, dict) and 'sampledValue' in meter_value:
                    if isinstance(meter_value['sampledValue'], list):
                        for sampled_value in meter_value['sampledValue']:
                            if (isinstance(sampled_value, dict) and
                                sampled_value.get('unit') == 'W' and
                                'value' in sampled_value):
                                try:
                                    # Convert to float, multiply by 10, then convert back to string
                                    original_value = float(sampled_value['value'])
                                    new_value = original_value * 10
                                    sampled_value['value'] = str(int(new_value))
                                    logger.debug(f"Multiplied watts by 10: {original_value} -> {new_value}")
                                except ValueError:
                                    logger.warning(f"Could not parse watts value: {sampled_value['value']}")

        # Recursively check nested dictionaries
        for key, value in data.items():
            if isinstance(value, dict):
                self._multiply_watts_by_10(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._multiply_watts_by_10(item)

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
                original_message = message

                # Fix timestamps if message is from client to target
                if direction == "client->target":
                    fixed_message = self.fix_timestamp(message)
                    if fixed_message != message:
                        logger.info(f"Fixed timestamp in message from {direction}")
                        ocpp_logger.info(f"[{direction}-FIXED] {fixed_message}")
                        message = fixed_message
                    else:
                        ocpp_logger.info(f"[{direction}] {message}")
                else:
                    ocpp_logger.info(f"[{direction}] {message}")

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