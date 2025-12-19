#!/usr/bin/env python3
"""
OCPP Log Formatter - Request/Response Pairs
Shows received and transmitted messages with timestamps in aligned format
"""

import json
import re
import sys
from datetime import datetime

def parse_log_line(line):
    """Parse a single log line"""
    # Extract timestamp
    timestamp_match = re.match(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})', line)
    if not timestamp_match:
        return None

    timestamp = timestamp_match.group(1)

    # Extract direction and determine if fixed
    if '[client->target-FIXED]' in line:
        direction = 'client->target'
        is_fixed = True
    elif '[client->target]' in line:
        direction = 'client->target'
        is_fixed = False
    elif '[target->client]' in line:
        direction = 'target->client'
        is_fixed = False
    else:
        return None

    # Extract message
    msg_start = line.find('] ') + 2
    if msg_start < 2:
        return None

    message_str = line[msg_start:].strip()

    return {
        'timestamp': timestamp,
        'direction': direction,
        'is_fixed': is_fixed,
        'message': message_str,
        'raw_line': line.strip()
    }

def get_message_info(message_str):
    """Extract message type and ID"""
    try:
        message = json.loads(message_str)
        if len(message) >= 2:
            msg_type = message[0]
            msg_id = str(message[1])

            if msg_type == 2:  # Call
                action = message[2] if len(message) > 2 else "Unknown"
                return msg_type, msg_id, action
            else:
                return msg_type, msg_id, None
        return None, None, None
    except:
        return None, None, None

def format_message_aligned(timestamp, direction, is_fixed, message_str, max_width=120):
    """Format a message line with proper alignment"""
    fixed_suffix = " (FIXED)" if is_fixed else ""
    direction_str = f"[{direction}{fixed_suffix}]"

    # Truncate message if too long
    if len(message_str) > max_width:
        message_str = message_str[:max_width-3] + "..."

    return f"{timestamp} - {direction_str} {message_str}"

def group_request_response_pairs(log_entries):
    """Group request-response pairs"""
    pairs = []
    pending_requests = {}  # msg_id -> entry

    for entry in log_entries:
        msg_type, msg_id, action = get_message_info(entry['message'])

        if msg_type == 2:  # Call (request)
            if entry['direction'] == 'target->client':
                # EVCC sending request to wallbox - store for pairing
                pending_requests[msg_id] = entry
            else:
                # Wallbox sending request to EVCC (like MeterValues)
                pairs.append({
                    'type': 'wallbox_request',
                    'request': entry,
                    'response': None
                })

        elif msg_type == 3:  # CallResult (response)
            if msg_id in pending_requests:
                # This is a response to an EVCC request
                request = pending_requests[msg_id]
                pairs.append({
                    'type': 'evcc_request',
                    'request': request,
                    'response': entry
                })
                del pending_requests[msg_id]
            else:
                # This is a response to a wallbox request - find the matching request
                for i, pair in enumerate(pairs):
                    if (pair['type'] == 'wallbox_request' and
                        pair['response'] is None and
                        get_message_info(pair['request']['message'])[1] == msg_id):
                        pairs[i]['response'] = entry
                        break
                else:
                    # Orphaned response
                    pairs.append({
                        'type': 'orphaned_response',
                        'request': None,
                        'response': entry
                    })

    return pairs

def format_logs_pairs(log_file_path):
    """Format logs showing request-response pairs"""
    print("OCPP MESSAGE FLOW - REQUEST/RESPONSE PAIRS")
    print("=" * 140)
    print()

    try:
        with open(log_file_path, 'r') as f:
            lines = f.readlines()

        # Parse all log entries
        log_entries = []
        for line in lines:
            parsed = parse_log_line(line.strip())
            if parsed:
                log_entries.append(parsed)

        # Group into request-response pairs
        pairs = group_request_response_pairs(log_entries)

        for pair in pairs:
            if pair['type'] == 'evcc_request':
                # EVCC -> Wallbox request-response
                print("📤 EVCC → WALLBOX")
                print("─" * 140)

                req = pair['request']
                resp = pair['response']

                req_line = format_message_aligned(req['timestamp'], req['direction'], req['is_fixed'], req['message'])
                resp_line = format_message_aligned(resp['timestamp'], resp['direction'], resp['is_fixed'], resp['message'])

                print(f"REQUEST : {req_line}")
                print(f"RESPONSE: {resp_line}")
                print()

            elif pair['type'] == 'wallbox_request':
                # Wallbox -> EVCC request-response
                print("📥 WALLBOX → EVCC")
                print("─" * 140)

                req = pair['request']
                resp = pair['response']

                req_line = format_message_aligned(req['timestamp'], req['direction'], req['is_fixed'], req['message'])
                print(f"REQUEST : {req_line}")

                if resp:
                    resp_line = format_message_aligned(resp['timestamp'], resp['direction'], resp['is_fixed'], resp['message'])
                    print(f"RESPONSE: {resp_line}")
                else:
                    print("RESPONSE: [No response found]")
                print()

    except FileNotFoundError:
        print(f"❌ Error: Log file '{log_file_path}' not found")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error reading log file: {e}")
        sys.exit(1)

def main():
    """Main function"""
    if len(sys.argv) < 2:
        print("Usage: ./format_logs_pairs.py <log_file>")
        print()
        print("Shows OCPP messages in request-response pairs with timestamps")
        print()
        print("Example:")
        print("  ./format_logs_pairs.py ocpp_messages.log")
        sys.exit(1)

    log_file = sys.argv[1]
    format_logs_pairs(log_file)

if __name__ == "__main__":
    main()