#!/usr/bin/env python3
"""
Log File Merger - Merges evcc log files with OCPP message logs based on timestamp overlap
"""

import os
import re
import glob
from datetime import datetime
from typing import List, Tuple, Optional, Dict

class LogEntry:
    def __init__(self, timestamp: datetime, original_line: str, source_file: str, log_type: str):
        self.timestamp = timestamp
        self.original_line = original_line.strip()
        self.source_file = source_file
        self.log_type = log_type  # 'evcc' or 'ocpp'

    def __lt__(self, other):
        return self.timestamp < other.timestamp

class LogMerger:
    def __init__(self, directory: str = '.'):
        self.directory = directory
        self.evcc_files = []
        self.ocpp_files = []

    def find_log_files(self):
        """Find all evcc and OCPP log files in the directory"""
        # Find evcc log files
        evcc_pattern = os.path.join(self.directory, '*evcc*.log*')
        self.evcc_files = glob.glob(evcc_pattern)

        # Find OCPP log files
        ocpp_pattern = os.path.join(self.directory, 'ocpp_messages.log*')
        self.ocpp_files = glob.glob(ocpp_pattern)

        print(f"Found {len(self.evcc_files)} evcc log file(s):")
        for f in self.evcc_files:
            print(f"  - {f}")
        print(f"Found {len(self.ocpp_files)} OCPP log file(s):")
        for f in self.ocpp_files:
            print(f"  - {f}")

    def parse_evcc_timestamp(self, line: str) -> Optional[datetime]:
        """Parse evcc log timestamp: [ocpp  ] TRACE 2025/10/01 13:20:41"""
        pattern = r'\[.*?\]\s+\w+\s+(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})'
        match = re.search(pattern, line)
        if match:
            try:
                return datetime.strptime(match.group(1), '%Y/%m/%d %H:%M:%S')
            except ValueError:
                return None
        return None

    def parse_ocpp_timestamp(self, line: str) -> Optional[datetime]:
        """Parse OCPP log timestamp: 2025-10-01 05:03:29,854"""
        pattern = r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}),\d+'
        match = re.search(pattern, line)
        if match:
            try:
                return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
            except ValueError:
                return None
        return None

    def get_file_timestamp_range(self, filepath: str, is_evcc: bool) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Get the first and last timestamp from a log file"""
        first_ts = None
        last_ts = None

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if is_evcc:
                        ts = self.parse_evcc_timestamp(line)
                    else:
                        ts = self.parse_ocpp_timestamp(line)

                    if ts:
                        if first_ts is None:
                            first_ts = ts
                        last_ts = ts
        except Exception as e:
            print(f"Error reading {filepath}: {e}")

        return first_ts, last_ts

    def find_overlapping_ocpp_files(self, evcc_start: datetime, evcc_end: datetime) -> List[str]:
        """Find OCPP files that overlap with the evcc timestamp range"""
        overlapping_files = []

        for ocpp_file in self.ocpp_files:
            ocpp_start, ocpp_end = self.get_file_timestamp_range(ocpp_file, is_evcc=False)

            if ocpp_start and ocpp_end:
                # Check for overlap: files overlap if start1 <= end2 and start2 <= end1
                if evcc_start <= ocpp_end and ocpp_start <= evcc_end:
                    overlapping_files.append(ocpp_file)
                    print(f"  Overlap found: {ocpp_file} ({ocpp_start} - {ocpp_end})")

        return overlapping_files

    def read_log_entries(self, filepath: str, is_evcc: bool,
                        start_filter: Optional[datetime] = None,
                        end_filter: Optional[datetime] = None) -> List[LogEntry]:
        """Read all log entries from a file with optional time filtering"""
        entries = []
        filename = os.path.basename(filepath)
        log_type = 'evcc' if is_evcc else 'ocpp'

        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if is_evcc:
                        ts = self.parse_evcc_timestamp(line)
                    else:
                        ts = self.parse_ocpp_timestamp(line)

                    if ts:
                        # Apply time filtering if specified
                        if start_filter and ts < start_filter:
                            continue
                        if end_filter and ts > end_filter:
                            continue

                        entries.append(LogEntry(ts, line, filename, log_type))

        except Exception as e:
            print(f"Error reading {filepath}: {e}")

        return entries

    def merge_logs(self, evcc_file: str, output_file: str = 'merged_logs.txt'):
        """Merge evcc log with overlapping OCPP logs"""
        print(f"\n=== Processing evcc file: {evcc_file} ===")

        # Get evcc timestamp range
        evcc_start, evcc_end = self.get_file_timestamp_range(evcc_file, is_evcc=True)
        if not evcc_start or not evcc_end:
            print(f"Could not determine timestamp range for {evcc_file}")
            return

        print(f"evcc time range: {evcc_start} - {evcc_end}")

        # Find overlapping OCPP files
        print("Looking for overlapping OCPP files:")
        overlapping_ocpp_files = self.find_overlapping_ocpp_files(evcc_start, evcc_end)

        if not overlapping_ocpp_files:
            print("No overlapping OCPP files found!")
            return

        # Read all log entries
        all_entries = []

        # Read evcc entries (these are the leading/primary entries)
        print(f"\nReading evcc entries from {evcc_file}...")
        evcc_entries = self.read_log_entries(evcc_file, is_evcc=True)
        all_entries.extend(evcc_entries)
        print(f"  Loaded {len(evcc_entries)} evcc entries")

        # Read overlapping OCPP entries
        for ocpp_file in overlapping_ocpp_files:
            print(f"Reading OCPP entries from {ocpp_file}...")
            # Filter OCPP entries to the evcc time range for better focus
            ocpp_entries = self.read_log_entries(ocpp_file, is_evcc=False,
                                               start_filter=evcc_start,
                                               end_filter=evcc_end)
            all_entries.extend(ocpp_entries)
            print(f"  Loaded {len(ocpp_entries)} OCPP entries (filtered to evcc time range)")

        # Sort all entries by timestamp
        print(f"\nSorting {len(all_entries)} total entries by timestamp...")
        all_entries.sort()

        # Write merged output
        print(f"Writing merged log to {output_file}...")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"# Merged Log File - Generated on {datetime.now()}\n")
            f.write(f"# evcc source: {evcc_file} ({evcc_start} - {evcc_end})\n")
            f.write(f"# OCPP sources: {', '.join([os.path.basename(f) for f in overlapping_ocpp_files])}\n")
            f.write(f"# Total entries: {len(all_entries)}\n")
            f.write("#" + "="*80 + "\n\n")

            for entry in all_entries:
                # Format: [TIMESTAMP] [SOURCE:TYPE] ORIGINAL_LINE
                f.write(f"[{entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] "
                       f"[{entry.source_file}:{entry.log_type.upper()}] "
                       f"{entry.original_line}\n")

        print(f"✅ Merged log created: {output_file}")
        print(f"   Total entries: {len(all_entries)}")
        print(f"   evcc entries: {len(evcc_entries)}")
        print(f"   OCPP entries: {len(all_entries) - len(evcc_entries)}")

def main():
    """Main function"""
    print("OCPP + evcc Log Merger")
    print("=" * 50)

    merger = LogMerger()
    merger.find_log_files()

    if not merger.evcc_files:
        print("❌ No evcc log files found!")
        return

    if not merger.ocpp_files:
        print("❌ No OCPP log files found!")
        return

    # Process each evcc file
    for evcc_file in merger.evcc_files:
        base_name = os.path.splitext(os.path.basename(evcc_file))[0]
        output_file = f"merged_{base_name}.txt"
        merger.merge_logs(evcc_file, output_file)

if __name__ == "__main__":
    main()