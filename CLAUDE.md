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

**Key Message Transformations:**

1. **URL Path Cleaning** (`clean_url_path`): Fixes malformed URLs like `ws://host:port//path` → `ws://host:port/path`

2. **Timestamp Fixing** (`fix_timestamp`, `_fix_timestamps_in_dict`): Replaces invalid timestamps (`0000-00-00T00:00:00.000Z`) with current UTC time

3. **IdTag Length Fixing** (`_fix_idtag_length`): Truncates or converts IdTag fields that exceed OCPP's 20-character limit

4. **Power Conversion** (`_multiply_watts_by_10`): Multiplies watt values in MeterValues by 10 to correct wallbox reporting errors

5. **Ampere to Watt Conversion** (`_convert_amperes_to_watts`): Converts SetChargingProfile commands from Amperes to Watts using 690 W/A ratio (based on empirical measurements)

6. **SetChargingProfile Standardization** (`_standardize_set_charging_profile`): Rewrites SetChargingProfile messages into a standardized format that the wallbox accepts

7. **Message Blocking** (`_should_block_message`): Blocks ChangeConfiguration commands except for OCPP B.7 configuration keys (LocalPreAuthorize, HeartbeatInterval, etc.)

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

### Running the Proxy

**Manual run with default settings:**
```bash
./ocpp_proxy.py --listen-port 8888 --target-host 192.168.0.202 --target-port 8887
```

**With debug logging:**
```bash
./ocpp_proxy.py --listen-port 8888 --target-host 192.168.0.202 --target-port 8887 --debug
```

**As systemd service:**
```bash
sudo systemctl start ocpp-proxy.service
sudo systemctl status ocpp-proxy.service
sudo systemctl restart ocpp-proxy.service
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

As of the latest firmware, most message transformations are **DISABLED** (`ocpp_proxy.py:267-271`). The proxy now runs in pure passthrough mode, only performing:
- URL path passthrough (no cleaning needed since firmware V1.17.9)
- TriggerMessage rejection workaround (for wallbox firmware flaw)
- BootNotification forwarding and tracking
- B.7 configuration key filtering (still active)

Disabled transformations (commented out):
- Timestamp fixing (`_fix_timestamps_in_dict`)
- IdTag length fixing (`_fix_idtag_length`)
- Power multiplication (`_multiply_watts_by_10`)
- Ampere-to-watt conversion (`_convert_amperes_to_watts`)
- SetChargingProfile standardization (`_standardize_set_charging_profile`)

These can be re-enabled by uncommenting lines 268-270 if needed for different wallbox models.

### OCPP Message Structure

OCPP 1.6 uses JSON arrays with different structures:
- **Call**: `[2, MessageId, Action, Payload]`
- **CallResult**: `[3, MessageId, Payload]`
- **CallError**: `[4, MessageId, ErrorCode, ErrorDescription, ErrorDetails]`

When modifying messages, preserve the array structure and message type.

### Ampere to Watt Conversion Factor

The conversion uses **690 W/A** based on empirical measurements from the wallbox (6A observed = ~4100W). This is NOT the theoretical calculation but the actual wallbox behavior. Do not change this factor without real-world testing.

### SetChargingProfile Message Format

The wallbox requires a very specific format for SetChargingProfile messages. The `_standardize_set_charging_profile` method defines the exact structure with hardcoded values like `chargingProfileId: 231`, `stackLevel: 0`, and `numberPhases: 3`. These values are derived from what the wallbox actually accepts.

### Configuration Key Filtering

Only OCPP B.7 configuration keys are allowed through. This prevents EVCC from changing critical wallbox settings that might break functionality. The allowed B.7 keys are explicitly listed in `_should_block_message`.

### TriggerMessage Workaround (`ocpp_proxy.py:785-798`)

Some wallboxes reject EVCC's TriggerMessage commands (used to request BootNotification). The proxy works around this firmware flaw by:

1. **Extracting boot info**: Captures wallbox identification from GetConfiguration responses
2. **Intercepting rejections**: When the wallbox rejects TriggerMessage, the proxy changes the rejection to "Accepted" before forwarding to EVCC
3. **Sending BootNotification**: The proxy sends the BootNotification to EVCC on behalf of the wallbox using the stored boot info

This allows EVCC to properly register wallboxes even when they don't support TriggerMessage correctly.

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

**Cleaning EVCC Database (Fixing Corrupted State):**

If EVCC shows errors like "charger [db:2] cannot create charger 'db:2': timeout", the database has corrupted entries. To reset:

**Method 1: Stop/Start (try this first):**
```bash
# 1. Stop EVCC add-on
ha addons stop 49686a9f_evcc

# 2. Wait for container to stop (check with: docker ps)

# 3. Start EVCC add-on (it will recreate database from evcc.yaml)
ha addons start 49686a9f_evcc

# 4. Monitor logs to confirm clean start
docker logs -f addon_49686a9f_evcc
```

**Method 2: Uninstall/Reinstall (if stop/start doesn't work):**
```bash
# 1. Backup configuration file
cp /addon_configs/49686a9f_evcc/evcc.yaml /tmp/evcc.yaml.backup

# 2. Uninstall EVCC add-on (removes corrupted database)
ha addons uninstall 49686a9f_evcc

# 3. Reinstall EVCC add-on
ha addons install 49686a9f_evcc

# 4. Start EVCC with fresh database
ha addons start 49686a9f_evcc

# 5. Monitor logs to confirm clean start
docker logs -f addon_49686a9f_evcc
```

The database is stored in a Docker volume and persists between restarts. Method 1 usually works, but if the database is severely corrupted, Method 2 (uninstall/reinstall) completely removes the old database and starts fresh. The configuration file at `/addon_configs/49686a9f_evcc/evcc.yaml` is preserved during uninstall.


## Important Paths

- Main proxy script: `/home/OCPP-Proxy/ocpp_proxy.py`
- OCPP message logs: `/home/OCPP-Proxy/ocpp_messages.log` (rotates at 10MB, 5 backups)
- Systemd service: `/etc/systemd/system/ocpp-proxy.service`
- Documentation: `/home/OCPP-Proxy/Wallbox-EVCC-Proxy-FSD.md`
- mo functional description in claude.md