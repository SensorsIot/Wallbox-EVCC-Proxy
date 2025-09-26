#!/usr/bin/env python3
"""
OCPP Log Formatter - Makes OCPP message logs human-readable
Formats the raw OCPP proxy logs to show clear message flow between wallbox and EVCC
"""

import json
import re
import sys
from datetime import datetime

def format_ocpp_message(message_str):
    """Format an OCPP message for better readability"""
    try:
        # Parse the JSON array
        message = json.loads(message_str)
        if isinstance(message, list) and len(message) >= 2:
            msg_type = message[0]
            msg_id = message[1]

            if msg_type == 2:  # Call
                action = message[2] if len(message) > 2 else "Unknown"
                payload = message[3] if len(message) > 3 else {}
                return f"CALL [{msg_id}] {action}", payload
            elif msg_type == 3:  # CallResult
                payload = message[2] if len(message) > 2 else {}
                return f"RESULT [{msg_id}]", payload
            elif msg_type == 4:  # CallError
                error_code = message[2] if len(message) > 2 else "Unknown"
                error_desc = message[3] if len(message) > 3 else ""
                return f"ERROR [{msg_id}] {error_code}: {error_desc}", {}

        return "UNKNOWN MESSAGE", {}
    except (json.JSONDecodeError, IndexError):
        return "MALFORMED MESSAGE", {}

def format_payload(payload, indent=2):
    """Format payload in a readable way"""
    if not payload:
        return ""

    # For simple payloads, format inline
    if len(str(payload)) < 100:
        return json.dumps(payload, separators=(',', ':'))

    # For complex payloads, format with indentation
    return json.dumps(payload, indent=indent, separators=(',', ': '))

def parse_log_line(line):
    """Parse a single log line"""
    # Extract timestamp
    timestamp_match = re.match(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})', line)
    if not timestamp_match:
        return None

    timestamp = timestamp_match.group(1)

    # Extract direction
    if '[client->target-FIXED]' in line:
        direction = '🔧 WALLBOX → EVCC (FIXED)'
        color = '\033[93m'  # Yellow
    elif '[client->target]' in line:
        direction = '📤 WALLBOX → EVCC'
        color = '\033[92m'  # Green
    elif '[target->client]' in line:
        direction = '📥 EVCC → WALLBOX'
        color = '\033[94m'  # Blue
    else:
        return None

    # Extract message
    msg_start = line.find('] ') + 2
    if msg_start < 2:
        return None

    message_str = line[msg_start:].strip()
    msg_type, payload = format_ocpp_message(message_str)

    return {
        'timestamp': timestamp,
        'direction': direction,
        'color': color,
        'msg_type': msg_type,
        'payload': payload,
        'raw_message': message_str
    }

def format_logs(log_file_path, show_payload=True, show_raw=False):
    """Format the entire log file"""
    print("=" * 80)
    print("🔌 OCPP MESSAGE FLOW - WALLBOX ↔ EVCC COMMUNICATION")
    print("=" * 80)
    print()

    try:
        with open(log_file_path, 'r') as f:
            lines = f.readlines()

        message_count = 0
        for line in lines:
            parsed = parse_log_line(line.strip())
            if not parsed:
                continue

            message_count += 1

            # Print header with color
            print(f"{parsed['color']}{parsed['timestamp']} - {parsed['direction']}")
            print(f"  {parsed['msg_type']}\033[0m")  # Reset color

            # Print payload if requested and exists
            if show_payload and parsed['payload']:
                formatted_payload = format_payload(parsed['payload'])
                print(f"  📋 Payload: {formatted_payload}")

            # Print raw message if requested
            if show_raw:
                print(f"  🔍 Raw: {parsed['raw_message']}")

            print()  # Empty line between messages

        print("=" * 80)
        print(f"📊 Total messages processed: {message_count}")
        print("🔧 FIXED = Timestamp or other corrections applied by proxy")
        print("=" * 80)

    except FileNotFoundError:
        print(f"❌ Error: Log file '{log_file_path}' not found")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error reading log file: {e}")
        sys.exit(1)

def main():
    """Main function with command line argument handling"""
    if len(sys.argv) < 2:
        print("Usage: ./format_logs.py <log_file> [options]")
        print()
        print("Options:")
        print("  --no-payload    Don't show message payloads")
        print("  --show-raw      Show raw JSON messages")
        print()
        print("Example:")
        print("  ./format_logs.py ocpp_messages.log")
        print("  ./format_logs.py ocpp_messages.log --no-payload")
        sys.exit(1)

    log_file = sys.argv[1]
    show_payload = '--no-payload' not in sys.argv
    show_raw = '--show-raw' in sys.argv

    format_logs(log_file, show_payload, show_raw)

if __name__ == "__main__":
    main()