#!/usr/bin/env python3
"""
Simple OCPP client to send SetChargingProfile commands to wallbox
"""

import asyncio
import websockets
import json
import random
from datetime import datetime, timezone

async def send_charging_profile(host, port, path, limit_watts):
    """Send SetChargingProfile command with specified watt limit"""

    # Generate unique message ID
    message_id = str(random.randint(1000000000, 9999999999))

    # Create current timestamp
    current_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    # Create the OCPP message
    message = [
        2,  # Call message type
        message_id,
        "SetChargingProfile",
        {
            "connectorId": 1,
            "csChargingProfiles": {
                "chargingProfileId": 0,
                "stackLevel": 0,
                "chargingProfilePurpose": "TxDefaultProfile",
                "chargingProfileKind": "Absolute",
                "chargingSchedule": {
                    "startSchedule": current_time,
                    "chargingRateUnit": "W",  # Using Watts instead of Amperes
                    "chargingSchedulePeriod": [{"startPeriod": 0, "limit": limit_watts}]
                }
            }
        }
    ]

    # WebSocket URL
    url = f"ws://{host}:{port}{path}"

    print(f"Connecting to: {url}")
    print(f"Sending SetChargingProfile with {limit_watts}W limit...")

    try:
        # Connect to websocket with OCPP subprotocol
        async with websockets.connect(url, subprotocols=["ocpp1.6"]) as websocket:
            # Send the message
            message_json = json.dumps(message)
            print(f"Sending: {message_json}")
            await websocket.send(message_json)

            # Wait for response
            response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
            print(f"Response: {response}")

            # Parse response
            try:
                response_data = json.loads(response)
                if len(response_data) >= 3 and response_data[0] == 3:  # CallResult
                    payload = response_data[2]
                    status = payload.get('status', 'Unknown')
                    print(f"Command status: {status}")
                    return status == "Accepted"
                else:
                    print(f"Unexpected response format: {response_data}")
                    return False
            except json.JSONDecodeError:
                print(f"Could not parse response as JSON: {response}")
                return False

    except asyncio.TimeoutError:
        print("Timeout waiting for response")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False

async def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description='Send OCPP SetChargingProfile command')
    parser.add_argument('--host', default='192.168.0.202', help='Wallbox host (default: 192.168.0.202)')
    parser.add_argument('--port', type=int, default=8887, help='Wallbox port (default: 8887)')
    parser.add_argument('--path', default='/AcTec001', help='WebSocket path (default: /AcTec001)')
    parser.add_argument('--limit', type=int, default=4000, help='Power limit in Watts (default: 4000)')

    args = parser.parse_args()

    success = await send_charging_profile(args.host, args.port, args.path, args.limit)
    if success:
        print("✅ Command sent successfully")
    else:
        print("❌ Command failed")

if __name__ == "__main__":
    asyncio.run(main())