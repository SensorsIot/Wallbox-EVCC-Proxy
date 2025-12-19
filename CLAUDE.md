# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an OCPP WebSocket proxy service that fixes communication issues between electric vehicle wallbox chargers and EVCC (Electric Vehicle Charge Controller) systems. The proxy intercepts and modifies OCPP 1.6 messages to correct malformed data from non-compliant wallboxes.

**Network Topology:**
```
[Wallbox] ←→ [Proxy:8888] ←→ [EVCC:8887]
```

The proxy listens on port 8888 and forwards to EVCC on port 8887, applying various fixes to OCPP messages in transit.

## Core Architecture

### Main Proxy (`ocpp_proxy.py`)

The `WebSocketProxy` class implements a bidirectional WebSocket proxy with OCPP-specific message transformations:

**Message Flow Direction:**
- `client->target`: Wallbox → EVCC (applies fixes to outgoing wallbox messages)
- `target->client`: EVCC → Wallbox (applies transformations to incoming EVCC commands)

**Active Message Processing:**

The proxy operates in **pure passthrough mode** with only one active message filter:

**FirmwareStatusNotification Blocking** (`ocpp_proxy.py:711-720`): Blocks FirmwareStatusNotification messages from wallbox to EVCC since EVCC doesn't support this optional OCPP feature, eliminating unnecessary "NotSupported" error responses

All other messages are logged and forwarded unchanged in both directions (wallbox ↔ EVCC).

### Logging Architecture

Two separate logging streams:
- **Console logs**: Connection events, transformations, errors (via `logger`)
- **OCPP message logs**: Raw and transformed OCPP messages (via `ocpp_logger` → `/home/OCPP-Proxy/ocpp_messages.log`)

Log tags indicate message processing:
- `[client->target]`: Normal wallbox→EVCC message
- `[client->target-STANDARDIZED]`: Modified message
- `[client->target-BLOCKED]`: Message blocked from forwarding
- `[target->client-CONVERTED]`: Ampere→Watt conversion applied

### Web Interface (`ocpp_proxy.py:815-1677`)

Built-in HTTP server on port 8889 with two main pages:

**Messages Page** (`/`):
- Real-time OCPP message display with last 500 messages buffered
- Color-coded direction indicators (Wallbox ↔ EVCC)
- Message type parsing (CALL, RESULT, ERROR)
- Click to expand/collapse JSON payloads
- Auto-refresh every 2 seconds
- Per-wallbox message filtering via tabs

**Live Status Dashboard** (`/status`):
- Real-time electrical measurements (voltage, current, power per phase)
- Energy consumption tracking
- EVCC charging limit display
- Charging efficiency calculations
- Configuration parameter display (passively extracted from OCPP messages)
- Interactive controls:
  - **Reboot Wallbox** button: Sends OCPP Reset command (`POST /api/reboot`)
  - **Stop Transaction** button: Forces stuck transaction closure (`POST /api/stop-transaction`)
  - **Get Config** button: Queries all configuration parameters (`POST /api/get-configuration`)

The web interface extracts live data from OCPP messages without modifying them (see `_extract_status_data` method).

## Python Dependencies

The proxy requires Python 3.7+ with the following libraries:
- `websockets` - WebSocket client/server implementation
- `aiohttp` - Async HTTP server for web interface

Install dependencies:
```bash
pip3 install websockets aiohttp
```

The proxy uses only Python standard library modules otherwise (`asyncio`, `json`, `logging`, etc.).

## Development Commands

### Initial Setup and Starting the Proxy

**First-time setup (create systemd service):**
```bash
# Create the systemd service file
sudo tee /etc/systemd/system/ocpp-proxy.service > /dev/null << 'EOF'
[Unit]
Description=OCPP WebSocket Proxy
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/OCPP-Proxy
ExecStart=/home/OCPP-Proxy/ocpp_proxy.py --listen-port 8888 --target-host 192.168.0.202 --target-port 8887
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd, enable and start the service
sudo systemctl daemon-reload
sudo systemctl enable ocpp-proxy.service
sudo systemctl start ocpp-proxy.service

# Verify it's running
sudo systemctl status ocpp-proxy.service
```

The proxy will now:
- Start automatically on boot (enabled)
- Restart automatically if it crashes (RestartSec=5)
- Listen on port 8888 for wallbox connections
- Forward to EVCC on 192.168.0.202:8887
- Serve web interface on port 8889

**Service management commands:**
```bash
sudo systemctl start ocpp-proxy.service     # Start the service
sudo systemctl stop ocpp-proxy.service      # Stop the service
sudo systemctl restart ocpp-proxy.service   # Restart the service
sudo systemctl status ocpp-proxy.service    # Check status
sudo systemctl disable ocpp-proxy.service   # Disable auto-start on boot
```

**Manual run (without systemd):**
```bash
# Basic run
./ocpp_proxy.py --listen-port 8888 --target-host 192.168.0.202 --target-port 8887

# With debug logging
./ocpp_proxy.py --listen-port 8888 --target-host 192.168.0.202 --target-port 8887 --debug

# Custom web interface port
./ocpp_proxy.py --listen-port 8888 --target-host 192.168.0.202 --target-port 8887 --web-port 8889
```

**Kill manual instance if needed:**
```bash
sudo fuser -k 8888/tcp
```

### Testing

**Send test charging profile:**
```bash
./send_charging_profile.py --host 192.168.0.150 --port 8888 --path /AcTec001 --limit 4000
```

This utility sends a SetChargingProfile OCPP command directly to the wallbox through the proxy.

### Log Viewing and Analysis

**View raw OCPP message logs:**
```bash
tail -f /home/OCPP-Proxy/ocpp_messages.log
```

**View formatted logs (human-readable):**
```bash
./format_logs.py ocpp_messages.log                 # Detailed with colors
./format_logs_compare.py ocpp_messages.log         # Side-by-side comparison
./format_logs.py ocpp_messages.log --no-payload    # Hide payloads
./format_logs.py ocpp_messages.log --show-raw      # Show raw JSON
```

**Access web interface:**
```bash
# Messages viewer with real-time updates
http://192.168.0.150:8889/

# Live status dashboard with electrical measurements
http://192.168.0.150:8889/status
```

**View systemd service logs:**
```bash
sudo journalctl -u ocpp-proxy.service -f
```

**Kill running proxy (if not running as service):**
```bash
sudo fuser -k 8888/tcp
```

## Critical Implementation Details

### Current Transformation State (IMPORTANT)

The proxy runs in **pure passthrough mode** with minimal intervention:

**ACTIVE:**
- **FirmwareStatusNotification blocking** only - eliminates unsupported EVCC feature errors

**ALL OTHER TRANSFORMATIONS DISABLED:**

The following transformations exist in the codebase but are **disabled** and not executed:
- URL path cleaning (`clean_url_path`) - not needed since firmware V1.17.9
- Timestamp fixing (`_fix_timestamps_in_dict`) - commented out
- IdTag length fixing (`_fix_idtag_length`) - commented out
- Power multiplication (`_multiply_watts_by_10`) - commented out
- Ampere-to-watt conversion (`_convert_amperes_to_watts`) - commented out
- SetChargingProfile standardization (`_standardize_set_charging_profile`) - commented out
- TriggerMessage rejection workaround - removed
- BootNotification tracking/manipulation - removed
- B.7 configuration key filtering - removed

All messages (except FirmwareStatusNotification) flow through unchanged and are only logged for monitoring.

### OCPP Message Structure

OCPP 1.6 uses JSON arrays with different structures:
- **Call**: `[2, MessageId, Action, Payload]`
- **CallResult**: `[3, MessageId, Payload]`
- **CallError**: `[4, MessageId, ErrorCode, ErrorDescription, ErrorDetails]`

When modifying messages, preserve the array structure and message type.

### FirmwareStatusNotification Blocking

The only active message transformation. EVCC doesn't support the optional FirmwareStatusNotification OCPP feature. Without blocking, these messages generate "NotSupported" error responses (~6-10 messages per minute). Blocking eliminates this unnecessary traffic and reduces log noise.

## Utility Scripts

- **`format_logs.py`**: Human-readable log formatter with colors and message parsing
- **`format_logs_compare.py`**: Side-by-side comparison format for analyzing request/response pairs
- **`send_charging_profile.py`**: Test utility for sending charging profile commands
- **`ocpp_transaction_analyzer.py`**: Analyzes charging transaction patterns from logs
- **`merge_logs.py`**: Merges multiple log files for analysis

## Service Configuration

The systemd service is defined at `/etc/systemd/system/ocpp-proxy.service` with:
- Auto-restart on failure (5 second delay)
- Runs as root (required for port binding)
- Logs to systemd journal
- Working directory: `/home/OCPP-Proxy`

After modifying the service file:
```bash
sudo systemctl daemon-reload
sudo systemctl restart ocpp-proxy.service
```

## Accessing EVCC (Home Assistant)

EVCC runs as a Home Assistant add-on in a Docker container on the network. To access EVCC configuration and logs via SSH:

**SSH Connection:**
```bash
# Using SSH key authentication (recommended - secure)
ssh root@192.168.0.202

# First time setup: Copy your SSH public key to Home Assistant
ssh-copy-id root@192.168.0.202
```

**Important EVCC Paths (inside Home Assistant):**
- Main config: `/homeassistant/evcc.yaml` or `/addon_configs/49686a9f_evcc/evcc.yaml`
- Database: `/data/evcc.db` (SQLite, inside Docker container)
- Docker container: `addon_49686a9f_evcc`

**View EVCC Logs:**
```bash
# Via Docker (from SSH session)
docker logs -f addon_49686a9f_evcc

# Via Home Assistant CLI
ha addons logs 49686a9f_evcc

# Restart EVCC add-on
ha addons restart 49686a9f_evcc
```

**Check EVCC Configuration:**
```bash
cat /homeassistant/evcc.yaml
```

Example charger configuration in evcc.yaml:
```yaml
chargers:
  - name: actec_wallbox
    type: ocpp
    stationid: Actec    # Must match wallbox's station ID
```

**Network Setup:**
- EVCC host: 192.168.0.202
- EVCC OCPP port: 8887
- Wallbox connects to: `ws://192.168.0.202:8887/[StationID]`

**EVCC Data Storage Locations:**

EVCC stores persistent data in multiple locations that can cause old configurations to persist:

1. **Configuration File** (YAML):
   - Host path: `/addon_configs/49686a9f_evcc/evcc.yaml`
   - Container path: `/config/evcc.yaml`
   - Purpose: Main configuration (chargers, loadpoints, meters, vehicles)

2. **SQLite Database** (Primary):
   - Host path: `/mnt/data/supervisor/addons/data/49686a9f_evcc/evcc.db`
   - Container path: `/data/evcc.db`
   - Purpose: Runtime configuration, sessions, UI-based config changes
   - **CRITICAL**: This is NOT at `/data/evcc.db` on the host! It's in a Docker volume.

3. **Temporary Database Files** (Cache):
   - Host path: `/tmp/evcc.db`, `/tmp/evcc_current.db`
   - Purpose: EVCC may create temporary caches
   - These can persist across restarts and restore old configurations

**Cleaning EVCC Database (Getting Absolutely Clean Slate):**

If EVCC shows errors like "charger [db:2] cannot create charger 'db:2': timeout" or loads old configurations (like the Elecq charger), the database has corrupted entries that persist across normal restarts.

**PROPER Method to Delete All EVCC Data:**
```bash
# 1. Stop EVCC add-on
ha addons stop 49686a9f_evcc

# 2. Delete database from Docker volume (NOT from /data directly!)
docker run --rm -v /mnt/data/supervisor/addons/data/49686a9f_evcc:/data alpine sh -c 'rm -f /data/evcc.db* && ls -la /data/'

# 3. Delete any temporary database files on host
rm -f /tmp/evcc*.db*

# 4. OPTIONAL: Reset configuration to minimal/empty
echo "log: debug" > /addon_configs/49686a9f_evcc/evcc.yaml

# 5. Start EVCC with completely clean slate
ha addons start 49686a9f_evcc

# 6. Monitor logs to confirm clean start (no old chargers like "AE104ABG00029B")
docker logs -f addon_49686a9f_evcc
```

**Why Simple `/data/evcc.db` Deletion Doesn't Work:**

The path `/data/evcc.db` doesn't exist on the Home Assistant host filesystem - it's inside the Docker container, mounted from `/mnt/data/supervisor/addons/data/49686a9f_evcc/`. Trying to delete `/data/evcc.db` from the host will fail with "No such file or directory".

**Verification:**
```bash
# Check database is gone (should show only options.json)
docker run --rm -v /mnt/data/supervisor/addons/data/49686a9f_evcc:/data alpine ls -la /data/

# Check no temp databases exist
ls -la /tmp/evcc*.db* 2>&1
```

**Alternative Method (Uninstall/Reinstall):**
```bash
# Complete nuclear option - removes everything
ha addons uninstall 49686a9f_evcc
ha addons install 49686a9f_evcc
```

The database is stored in a Docker volume and persists between normal restarts. Using `ha addons stop` and deleting from the Docker volume ensures all persistent data is removed.


## Important Paths

- Main proxy script: `/home/OCPP-Proxy/ocpp_proxy.py`
- OCPP message logs: `/home/OCPP-Proxy/ocpp_messages.log` (rotates at 10MB, 5 backups)
- Systemd service: `/etc/systemd/system/ocpp-proxy.service`
- Documentation: `/home/OCPP-Proxy/Wallbox-EVCC-Proxy-FSD.md`
- mo functional description in claude.md
- only push manually
- only update claude.md manually