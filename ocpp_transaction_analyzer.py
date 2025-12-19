#!/usr/bin/env python3
"""
OCPP Transaction Analyzer - Align transactions by timestamp with direction classification
"""

import re
import json
import glob
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple

class OCPPTransaction:
    def __init__(self, timestamp: datetime, message_type: str, message_id: str,
                 payload: dict, direction: str, source: str, raw_line: str):
        self.timestamp = timestamp
        self.message_type = message_type
        self.message_id = message_id
        self.payload = payload
        self.direction = direction  # 'evcc->wallbox' or 'wallbox->evcc'
        self.source = source  # 'evcc' or 'ocpp'
        self.raw_line = raw_line

class OCPPTransactionAnalyzer:
    def __init__(self):
        self.transactions = []

    def parse_timestamp(self, line: str, source: str) -> Optional[datetime]:
        """Parse timestamp from either evcc or OCPP log"""
        if source == 'evcc':
            # evcc format: [lp-1  ] DEBUG 2025/10/01 15:05:31
            match = re.search(r'(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})', line)
            if match:
                return datetime.strptime(match.group(1), '%Y/%m/%d %H:%M:%S')
        else:
            # OCPP raw format: 2025-10-01 15:05:31,576
            match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            if match:
                return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
        return None

    def extract_json_payload(self, line: str) -> Optional[dict]:
        """Extract JSON payload from OCPP message"""
        try:
            # Look for OCPP JSON array patterns
            if 'Actec:' in line:
                actec_match = re.search(r'Actec:\s*(\[.*\])', line)
                if actec_match:
                    json_str = actec_match.group(1)
                    return json.loads(json_str)
            else:
                # For OCPP file format: extract everything after the direction indicator
                if '[target->client]' in line or '[client->target' in line:
                    # Pattern: timestamp - [direction] [JSON...] (note: client->target may have -FIXED suffix)
                    json_match = re.search(r'\[(?:target->client|client->target[^\]]*)\]\s*(\[.*\])\s*$', line)
                    if json_match:
                        json_str = json_match.group(1).strip()
                        return json.loads(json_str)

                # Fallback to simple JSON pattern
                json_match = re.search(r'(\[2,.*\])', line)
                if json_match:
                    json_str = json_match.group(1).strip()
                    return json.loads(json_str)
        except (json.JSONDecodeError, Exception):
            pass
        return None

    def determine_direction(self, line: str, message_type: str) -> str:
        """Determine message direction based on OCPP 1.6J specification"""

        # evcc-initiated messages (Central System -> Charge Point)
        evcc_initiated = [
            'SetChargingProfile', 'GetCompositeSchedule', 'ClearChargingProfile',
            'RemoteStartTransaction', 'RemoteStopTransaction', 'UnlockConnector',
            'GetConfiguration', 'ChangeConfiguration', 'Reset', 'UpdateFirmware',
            'GetDiagnostics', 'ChangeAvailability', 'ReserveNow', 'CancelReservation',
            'TriggerMessage', 'DataTransfer'
        ]

        # wallbox-initiated messages (Charge Point -> Central System)
        wallbox_initiated = [
            'MeterValues', 'StatusNotification', 'Heartbeat', 'BootNotification',
            'Authorize', 'StartTransaction', 'StopTransaction', 'FirmwareStatusNotification',
            'DiagnosticsStatusNotification', 'DataTransfer'
        ]

        if message_type in evcc_initiated:
            return 'evcc->wallbox'
        elif message_type in wallbox_initiated:
            return 'wallbox->evcc'
        else:
            # Fallback to line content analysis
            if 'send Actec:' in line or 'target->client' in line:
                return 'evcc->wallbox'
            elif 'recv Actec:' in line or 'client->target' in line:
                return 'wallbox->evcc'
            else:
                return 'unknown'

    def read_evcc_file(self, filepath: str):
        """Read evcc debug log file"""
        print(f"📖 Reading evcc file: {filepath}")

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    timestamp = self.parse_timestamp(line, 'evcc')
                    if not timestamp:
                        continue

                    # Look for OCPP messages in evcc log
                    if 'Actec:' in line and ('[' in line and ']' in line):
                        payload = self.extract_json_payload(line)
                        if payload and len(payload) >= 3:
                            message_type = payload[2] if len(payload) > 2 else 'unknown'
                            message_id = payload[1] if len(payload) > 1 else 'unknown'
                            direction = self.determine_direction(line, message_type)

                            transaction = OCPPTransaction(
                                timestamp=timestamp,
                                message_type=message_type,
                                message_id=message_id,
                                payload=payload,
                                direction=direction,
                                source='evcc',
                                raw_line=line.strip()
                            )
                            self.transactions.append(transaction)

        except Exception as e:
            print(f"❌ Error reading evcc file: {e}")

    def read_merged_file(self, filepath: str):
        """Read merged OCPP log file"""
        print(f"📖 Reading merged file: {filepath}")

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    if line.startswith('#'):
                        continue

                    timestamp = self.parse_timestamp(line, 'ocpp')
                    if not timestamp:
                        continue

                    # Look for OCPP messages (any line with JSON array containing OCPP message types)
                    if (('[' in line and ']' in line) and
                        ('SetChargingProfile' in line or 'MeterValues' in line or
                         'StatusNotification' in line or 'Heartbeat' in line or
                         'TriggerMessage' in line or 'BootNotification' in line or
                         'GetCompositeSchedule' in line)):
                        payload = self.extract_json_payload(line)
                        if payload and len(payload) >= 3:
                            message_type = payload[2] if len(payload) > 2 else 'unknown'
                            message_id = payload[1] if len(payload) > 1 else 'unknown'
                            direction = self.determine_direction(line, message_type)

                            transaction = OCPPTransaction(
                                timestamp=timestamp,
                                message_type=message_type,
                                message_id=message_id,
                                payload=payload,
                                direction=direction,
                                source='ocpp',
                                raw_line=line.strip()
                            )
                            self.transactions.append(transaction)

        except Exception as e:
            print(f"❌ Error reading merged file: {e}")

    def find_latest_evcc_file(self) -> str:
        """Find the latest evcc debug file"""
        evcc_files = glob.glob("evcc-*debug.log")
        if not evcc_files:
            raise FileNotFoundError("No evcc debug files found")

        # Sort by modification time (newest first)
        evcc_files.sort(key=os.path.getmtime, reverse=True)
        return evcc_files[0]

    def find_ocpp_files(self) -> List[str]:
        """Find OCPP log files"""
        ocpp_files = glob.glob("ocpp_messages.log*")
        if not ocpp_files:
            raise FileNotFoundError("No OCPP log files found")
        return ocpp_files

    def read_ocpp_file(self, filepath: str):
        """Read OCPP log file"""
        print(f"📖 Reading OCPP file: {filepath}")

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    timestamp = self.parse_timestamp(line, 'ocpp')
                    if not timestamp:
                        continue

                    # Look for all OCPP messages (any line with JSON array)
                    if '[' in line and ']' in line:
                        payload = self.extract_json_payload(line)
                        if payload and len(payload) >= 2:
                            # Handle both requests [2, msg_id, action, payload] and responses [3, msg_id, payload]
                            if payload[0] == 2 and len(payload) >= 3:
                                # Request message
                                message_type = payload[2] if len(payload) > 2 else 'unknown'
                                message_id = payload[1] if len(payload) > 1 else 'unknown'
                            elif payload[0] == 3 and len(payload) >= 2:
                                # Response message
                                message_type = 'Response'
                                message_id = payload[1] if len(payload) > 1 else 'unknown'
                            else:
                                continue

                            direction = self.determine_direction(line, message_type)

                            transaction = OCPPTransaction(
                                timestamp=timestamp,
                                message_type=message_type,
                                message_id=message_id,
                                payload=payload,
                                direction=direction,
                                source='ocpp',
                                raw_line=line.strip()
                            )
                            self.transactions.append(transaction)

        except Exception as e:
            print(f"❌ Error reading OCPP file: {e}")

    def analyze_transactions(self):
        """Analyze all transactions and sort by timestamp"""
        print(f"🔍 Analyzing {len(self.transactions)} transactions")

        if not self.transactions:
            print("❌ No transactions found!")
            return

        # Sort transactions by timestamp
        self.transactions.sort(key=lambda x: x.timestamp)

        print(f"✅ Found transactions from {self.transactions[0].timestamp} to {self.transactions[-1].timestamp}")

    def group_transactions_by_message_id(self):
        """Group transactions by message ID to show complete proxy flows"""
        grouped = {}
        for transaction in self.transactions:
            msg_id = transaction.message_id
            if msg_id not in grouped:
                grouped[msg_id] = []
            grouped[msg_id].append(transaction)
        return grouped

    def write_analysis_report(self):
        """Write analysis report with sequential proxy flows"""
        analysis_time = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_file = f"ocpp_transaction_analysis_{analysis_time}.txt"

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("🔍 OCPP Transaction Analysis - Sequential Proxy Flows\n")
            f.write("=" * 70 + "\n")
            f.write(f"⏰ Analysis time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"📊 Total transactions: {len(self.transactions)}\n\n")

            # Process transactions sequentially to show proxy flows
            flow_counter = 1
            i = 0

            while i < len(self.transactions):
                transaction = self.transactions[i]

                # Start a new flow for each request message (type 2)
                if (transaction.payload and len(transaction.payload) >= 3 and
                    transaction.payload[0] == 2 and transaction.message_type != 'Response'):

                    f.write(f"From {'Wallbox' if transaction.direction == 'wallbox->evcc' else 'EVCC'} {transaction.message_type} #{flow_counter:03d} {transaction.timestamp}\n")

                    # Look for related messages with the same message ID only
                    current_msg_id = transaction.message_id
                    related_transactions = [transaction]

                    # Look ahead for direct responses to this specific message
                    for j in range(i + 1, min(i + 50, len(self.transactions))):
                        next_transaction = self.transactions[j]

                        # Stop looking if we're too far in time (more than 30 seconds)
                        time_diff = (next_transaction.timestamp - transaction.timestamp).total_seconds()
                        if time_diff > 30:
                            break

                        # Include only direct responses to this exact message ID
                        if next_transaction.message_id == current_msg_id:
                            related_transactions.append(next_transaction)

                    # Write all related transactions
                    for trans in related_transactions:
                        # Extract direction indicator from raw line
                        direction_indicator = ""
                        if '[target->client]' in trans.raw_line:
                            direction_indicator = "[target->client]"
                        elif '[client->target' in trans.raw_line:
                            # Extract full client->target indicator (may have -FIXED)
                            match = re.search(r'(\[client->target[^\]]*\])', trans.raw_line)
                            direction_indicator = match.group(1) if match else "[client->target]"
                        elif 'send Actec:' in trans.raw_line:
                            direction_indicator = "send Actec:"
                        elif 'recv Actec:' in trans.raw_line:
                            direction_indicator = "recv Actec:"

                        # Format the JSON payload as a single line
                        json_str = json.dumps(trans.payload, separators=(',', ':'))

                        if trans.source == 'ocpp':
                            if '[target->client]' in trans.raw_line:
                                f.write(f" proxy input message: {direction_indicator} {json_str}\n")
                            elif '[client->target' in trans.raw_line:
                                f.write(f" proxy output message: {direction_indicator} {json_str}\n")
                        elif trans.source == 'evcc':
                            if 'send Actec:' in trans.raw_line:
                                f.write(f" evcc output message: {direction_indicator} {json_str}\n")
                            elif 'recv Actec:' in trans.raw_line:
                                f.write(f" evcc input message: {direction_indicator} {json_str}\n")

                    f.write("--------------------------------------------------\n\n")
                    flow_counter += 1

                i += 1

        print(f"✅ Analysis complete! Results saved to: {output_file}")
        return output_file

def main():
    """Main function"""
    analyzer = OCPPTransactionAnalyzer()

    try:
        # Find latest evcc file
        latest_evcc = analyzer.find_latest_evcc_file()
        print(f"📁 Found latest evcc file: {latest_evcc}")

        # Find OCPP files
        ocpp_files = analyzer.find_ocpp_files()
        print(f"📁 Found OCPP files: {ocpp_files}")

        # Read evcc file (even if it has no OCPP messages, it has timestamps)
        analyzer.read_evcc_file(latest_evcc)

        # Read all OCPP files and merge in memory
        for ocpp_file in ocpp_files:
            analyzer.read_ocpp_file(ocpp_file)

        # Analyze and output
        analyzer.analyze_transactions()
        output_file = analyzer.write_analysis_report()

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()