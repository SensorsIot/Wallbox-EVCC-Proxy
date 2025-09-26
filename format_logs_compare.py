#!/usr/bin/env python3
"""
OCPP Log Formatter - Side-by-side comparison format
Shows received vs sent messages aligned for easy comparison
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
        direction = 'wallbox->evcc'
        is_fixed = True
    elif '[client->target]' in line:
        direction = 'wallbox->evcc'
        is_fixed = False
    elif '[target->client]' in line:
        direction = 'evcc->wallbox'
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

def format_message_inline(message_str, max_width=120):
    """Format message in a single line"""
    try:
        message = json.loads(message_str)
        # Compact JSON format
        compact = json.dumps(message, separators=(',', ':'))
        if len(compact) <= max_width:
            return compact
        else:
            # Truncate and add ellipsis
            return compact[:max_width-3] + "..."
    except:
        return message_str[:max_width]

def group_message_pairs(log_entries):
    """Group related messages for comparison"""
    pairs = []
    pending_requests = {}  # msg_id -> entry

    for entry in log_entries:
        try:
            message = json.loads(entry['message'])
            if len(message) < 2:
                continue

            msg_type = message[0]
            msg_id = str(message[1])

            if msg_type == 2:  # Call (request)
                if entry['direction'] == 'evcc->wallbox':
                    # EVCC sending request to wallbox
                    pending_requests[msg_id] = entry
                else:
                    # Wallbox sending request to EVCC (like MeterValues)
                    pairs.append(('request', entry, None))

            elif msg_type == 3:  # CallResult (response)
                if msg_id in pending_requests:
                    # This is a response to a pending request
                    request = pending_requests[msg_id]
                    pairs.append(('response_pair', request, entry))
                    del pending_requests[msg_id]
                else:
                    # This is a response to a wallbox request
                    pairs.append(('response', entry, None))

        except (json.JSONDecodeError, IndexError, KeyError):
            pairs.append(('single', entry, None))

    return pairs

def format_logs_compare(log_file_path):
    """Format logs in side-by-side comparison format"""
    print("OCPP MESSAGE FLOW - WALLBOX ↔ EVCC COMMUNICATION")
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

        # Group related messages
        pairs = group_message_pairs(log_entries)

        for pair_type, entry1, entry2 in pairs:
            if pair_type == 'response_pair':
                # Request-Response pair
                req_msg = format_message_inline(entry1['message'])
                resp_msg = format_message_inline(entry2['message'])

                fixed_marker = " (FIXED)" if entry2['is_fixed'] else ""

                print(f"EVCC→WALLBOX: {req_msg}")
                print(f"WALLBOX→EVCC{fixed_marker}: {resp_msg}")
                print("             " + "─" * 100)
                print()

            elif pair_type == 'request':
                # Standalone request (like MeterValues from wallbox)
                req_msg = format_message_inline(entry1['message'])
                fixed_marker = " (FIXED)" if entry1['is_fixed'] else ""

                print(f"WALLBOX→EVCC{fixed_marker}: {req_msg}")
                print("             " + "─" * 100)
                print()

            elif pair_type == 'response':
                # Standalone response (to wallbox request)
                resp_msg = format_message_inline(entry1['message'])

                print(f"EVCC→WALLBOX: {resp_msg}")
                print("             " + "─" * 100)
                print()

    except FileNotFoundError:
        print(f"❌ Error: Log file '{log_file_path}' not found")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error reading log file: {e}")
        sys.exit(1)

def main():
    """Main function with command line argument handling"""
    if len(sys.argv) < 2:
        print("Usage: ./format_logs_compare.py <log_file>")
        print()
        print("Example:")
        print("  ./format_logs_compare.py ocpp_messages.log")
        sys.exit(1)

    log_file = sys.argv[1]
    format_logs_compare(log_file)

if __name__ == "__main__":
    main()