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
import random
from datetime import datetime
from websockets.legacy.server import serve
from aiohttp import web
from collections import deque
import threading

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
    def __init__(self, listen_host="0.0.0.0", listen_port=8888, target_host="192.168.0.150", target_port=8887, web_port=8889):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self.web_port = web_port
        # Circular buffer for recent messages (keep last 500)
        self.message_buffer = deque(maxlen=500)
        self.buffer_lock = threading.Lock()
        # Live status data
        self.live_status = {
            'wallbox': {
                'voltage': {'L1': 0.0, 'L2': 0.0, 'L3': 0.0},
                'current': {'L1': 0.0, 'L2': 0.0, 'L3': 0.0},
                'power': {'L1': 0.0, 'L2': 0.0, 'L3': 0.0, 'total': 0.0},
                'energy': 0.0,
                'status': 'Unknown',
                'connector_id': None,
                'error_code': 'Unknown',
                'status_info': '',
                'status_timestamp': None,
                'vendor_id': '',
                'vendor_error_code': '',
                'transaction_id': None,
                'configuration': {},
                'last_update': None
            },
            'evcc': {
                'charging_limit': 0.0,
                'charging_unit': 'W',
                'last_command': None,
                'last_update': None
            }
        }
        self.status_lock = threading.Lock()
        # Track all wallboxes that need periodic boot notifications
        # Wallboxes are added when they send BootNotification, removed when EVCC acknowledges
        self.pending_boot_wallboxes = {}  # {station_id: {'boot_info': {...}, 'last_sent': timestamp, 'evcc_confirmed': False}}
        self.boot_lock = threading.Lock()
        # Track pending TriggerMessage requests for StatusNotification
        self.pending_trigger_requests = {}  # {station_id: {'message_id': id, 'requested_message': type, 'timestamp': datetime, 'connector_id': int}}
        self.trigger_lock = threading.Lock()
        # Track connector status per wallbox
        self.connector_status = {}  # {station_id: {'status': 'Available', 'error_code': 'NoError', 'connector_id': 1, 'transaction_id': None}}

    def _add_message_to_buffer(self, direction, message, tag="", station_id=""):
        """Add message to circular buffer for web interface"""
        with self.buffer_lock:
            self.message_buffer.append({
                'timestamp': datetime.now().isoformat(),
                'direction': direction,
                'message': message,
                'tag': tag,
                'station_id': station_id
            })
        # Also extract live status data
        self._extract_status_data(direction, message)

    def _extract_status_data(self, direction, message):
        """Extract live status data from OCPP messages"""
        try:
            parsed = json.loads(message)
            if not isinstance(parsed, list) or len(parsed) < 3:
                return

            msg_type = parsed[0]
            timestamp = datetime.now().isoformat()

            with self.status_lock:
                # Messages from wallbox (client->target)
                if direction == "client->target":
                    # Check for GetConfiguration response (CallResult message)
                    if msg_type == 3 and len(parsed) >= 3:
                        payload = parsed[2]
                        if isinstance(payload, dict) and 'configurationKey' in payload:
                            # GetConfiguration response
                            config_keys = payload['configurationKey']
                            if isinstance(config_keys, list):
                                for config in config_keys:
                                    if isinstance(config, dict) and 'key' in config and 'value' in config:
                                        self.live_status['wallbox']['configuration'][config['key']] = config.get('value', '')
                                        logger.debug(f"Tracked configuration: {config['key']} = {config.get('value', '')}")

                    # Check for Call messages from wallbox
                    if msg_type == 2 and len(parsed) >= 4:  # Call message
                        action = parsed[2]
                        payload = parsed[3]

                        # MeterValues - extract voltage, current, power
                        if action == "MeterValues" and isinstance(payload, dict):
                            if 'transactionId' in payload:
                                self.live_status['wallbox']['transaction_id'] = payload['transactionId']

                            if 'meterValue' in payload and isinstance(payload['meterValue'], list):
                                for meter_value in payload['meterValue']:
                                    if 'sampledValue' in meter_value and isinstance(meter_value['sampledValue'], list):
                                        for sample in meter_value['sampledValue']:
                                            if not isinstance(sample, dict):
                                                continue

                                            measurand = sample.get('measurand', '')
                                            value = float(sample.get('value', 0))
                                            phase = sample.get('phase', '')
                                            unit = sample.get('unit', '')

                                            # Voltage
                                            if measurand == 'Voltage' and 'L' in phase:
                                                self.live_status['wallbox']['voltage'][phase] = value

                                            # Current
                                            elif measurand == 'Current.Import' and 'L' in phase:
                                                self.live_status['wallbox']['current'][phase] = value

                                            # Power
                                            elif measurand == 'Power.Active.Import' and 'L' in phase:
                                                self.live_status['wallbox']['power'][phase] = value

                                            # Energy
                                            elif measurand == 'Energy.Active.Import.Register':
                                                self.live_status['wallbox']['energy'] = value

                            # Calculate total power
                            self.live_status['wallbox']['power']['total'] = (
                                self.live_status['wallbox']['power']['L1'] +
                                self.live_status['wallbox']['power']['L2'] +
                                self.live_status['wallbox']['power']['L3']
                            )
                            self.live_status['wallbox']['last_update'] = timestamp

                        # StatusNotification - extract charging status and parameters
                        elif action == "StatusNotification" and isinstance(payload, dict):
                            if 'status' in payload:
                                self.live_status['wallbox']['status'] = payload['status']
                            if 'connectorId' in payload:
                                self.live_status['wallbox']['connector_id'] = payload['connectorId']
                            if 'errorCode' in payload:
                                self.live_status['wallbox']['error_code'] = payload['errorCode']
                            if 'info' in payload:
                                self.live_status['wallbox']['status_info'] = payload['info']
                            if 'timestamp' in payload:
                                self.live_status['wallbox']['status_timestamp'] = payload['timestamp']
                            if 'vendorId' in payload:
                                self.live_status['wallbox']['vendor_id'] = payload['vendorId']
                            if 'vendorErrorCode' in payload:
                                self.live_status['wallbox']['vendor_error_code'] = payload['vendorErrorCode']
                            self.live_status['wallbox']['last_update'] = timestamp

                        # StartTransaction
                        elif action == "StartTransaction" and isinstance(payload, dict):
                            if 'transactionId' in payload:
                                self.live_status['wallbox']['transaction_id'] = payload['transactionId']
                            self.live_status['wallbox']['status'] = 'Charging'
                            self.live_status['wallbox']['last_update'] = timestamp

                        # StopTransaction
                        elif action == "StopTransaction":
                            self.live_status['wallbox']['transaction_id'] = None
                            self.live_status['wallbox']['status'] = 'Available'
                            self.live_status['wallbox']['last_update'] = timestamp

                # Messages from EVCC (target->client)
                elif direction == "target->client":
                    if msg_type == 2 and len(parsed) >= 4:  # Call message
                        action = parsed[2]
                        payload = parsed[3]

                        # ChangeConfiguration - track configuration changes
                        if action == "ChangeConfiguration" and isinstance(payload, dict):
                            if 'key' in payload and 'value' in payload:
                                self.live_status['wallbox']['configuration'][payload['key']] = payload['value']
                                logger.debug(f"Tracked configuration change: {payload['key']} = {payload['value']}")

                        # SetChargingProfile - extract charging limit
                        if action == "SetChargingProfile" and isinstance(payload, dict):
                            self.live_status['evcc']['last_command'] = 'SetChargingProfile'
                            if 'csChargingProfiles' in payload:
                                profiles = payload['csChargingProfiles']
                                if isinstance(profiles, dict) and 'chargingSchedule' in profiles:
                                    schedule = profiles['chargingSchedule']
                                    if isinstance(schedule, dict):
                                        if 'chargingRateUnit' in schedule:
                                            self.live_status['evcc']['charging_unit'] = schedule['chargingRateUnit']
                                        if 'chargingSchedulePeriod' in schedule and isinstance(schedule['chargingSchedulePeriod'], list):
                                            if len(schedule['chargingSchedulePeriod']) > 0:
                                                period = schedule['chargingSchedulePeriod'][0]
                                                if isinstance(period, dict) and 'limit' in period:
                                                    self.live_status['evcc']['charging_limit'] = float(period['limit'])
                            self.live_status['evcc']['last_update'] = timestamp

                        # RemoteStartTransaction
                        elif action == "RemoteStartTransaction":
                            self.live_status['evcc']['last_command'] = 'RemoteStartTransaction'
                            self.live_status['evcc']['last_update'] = timestamp

                        # RemoteStopTransaction
                        elif action == "RemoteStopTransaction":
                            self.live_status['evcc']['last_command'] = 'RemoteStopTransaction'
                            self.live_status['evcc']['charging_limit'] = 0.0
                            self.live_status['evcc']['last_update'] = timestamp

        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
            logger.debug(f"Error extracting status data: {e}")

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
                    # DISABLED: Pure passthrough mode - no transformations
                    # self._fix_timestamps_in_dict(payload)
                    # self._fix_idtag_length(payload)
                    # self._multiply_watts_by_10(payload)
                    pass

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

    def _add_power_active_import(self, data, action):
        """Calculate and inject Power.Active.Import if missing in MeterValues"""
        if not isinstance(data, dict):
            return

        # Only process MeterValues messages
        if action != "MeterValues":
            return

        # Look for MeterValues messages
        if 'meterValue' in data and isinstance(data['meterValue'], list):
            for meter_value in data['meterValue']:
                if isinstance(meter_value, dict) and 'sampledValue' in meter_value:
                    if isinstance(meter_value['sampledValue'], list):
                        sampled_values = meter_value['sampledValue']

                        # Check if Power.Active.Import already exists
                        has_power = any(
                            sv.get('measurand') == 'Power.Active.Import'
                            for sv in sampled_values if isinstance(sv, dict)
                        )

                        if has_power:
                            continue  # Already have power values, skip

                        # Collect current and voltage values for each phase
                        currents = {}  # {phase: value}
                        voltages = {}  # {phase: value}

                        for sv in sampled_values:
                            if not isinstance(sv, dict):
                                continue

                            measurand = sv.get('measurand', '')
                            phase = sv.get('phase', '')
                            value_str = sv.get('value', '')

                            try:
                                value = float(value_str)

                                if measurand == 'Current.Import' and phase in ['L1', 'L2', 'L3']:
                                    currents[phase] = value
                                elif measurand == 'Voltage' and phase in ['L1', 'L2', 'L3']:
                                    voltages[phase] = value
                            except (ValueError, TypeError):
                                continue

                        # Calculate total power if we have data for all three phases
                        if len(currents) == 3 and len(voltages) == 3:
                            total_power = 0.0
                            for phase in ['L1', 'L2', 'L3']:
                                if phase in currents and phase in voltages:
                                    # Power = Voltage × Current
                                    total_power += voltages[phase] * currents[phase]

                            # Add Power.Active.Import to sampledValue array
                            power_sample = {
                                "value": str(int(total_power)),
                                "context": sv.get('context', 'Sample.Periodic'),  # Use same context as other values
                                "format": "Raw",
                                "measurand": "Power.Active.Import",
                                "unit": "W"
                            }
                            sampled_values.append(power_sample)
                            logger.info(f"Injected Power.Active.Import: {int(total_power)}W (calculated from current and voltage)")

    def _convert_amperes_to_watts(self, message_data):
        """Convert ampere limits to watt limits in SetChargingProfile commands"""
        if not isinstance(message_data, list) or len(message_data) < 4:
            return

        # Check if this is a SetChargingProfile command
        if message_data[2] == "SetChargingProfile":
            payload = message_data[3]
            if isinstance(payload, dict) and 'csChargingProfiles' in payload:
                cs_profiles = payload['csChargingProfiles']
                if isinstance(cs_profiles, dict) and 'chargingSchedule' in cs_profiles:
                    schedule = cs_profiles['chargingSchedule']
                    if (isinstance(schedule, dict) and
                        schedule.get('chargingRateUnit') == 'A' and
                        'chargingSchedulePeriod' in schedule):
                        # Convert from Amperes to Watts
                        periods = schedule['chargingSchedulePeriod']
                        if isinstance(periods, list):
                            for period in periods:
                                if isinstance(period, dict) and 'limit' in period:
                                    ampere_limit = period['limit']
                                    if isinstance(ampere_limit, (int, float)) and ampere_limit >= 0:
                                        # Convert A to W using observed real-world data
                                        # Based on actual measurements: 6A = ~4100W
                                        # Conversion factor: 690 W/A
                                        # Special case: 0A -> 0W
                                        watt_limit = int(ampere_limit * 690) if ampere_limit > 0 else 0
                                        period['limit'] = watt_limit
                                        logger.info(f"Converted charging limit: {ampere_limit}A -> {watt_limit}W")

                            # Change the rate unit to Watts
                            schedule['chargingRateUnit'] = 'W'
                            logger.info("Changed chargingRateUnit from A to W")

    def _standardize_set_charging_profile(self, message_data):
        """Standardize SetChargingProfile message format for wallbox"""
        if not isinstance(message_data, list) or len(message_data) < 4:
            return

        # Check if this is a SetChargingProfile command going to wallbox (client->target direction)
        if message_data[2] == "SetChargingProfile":
            payload = message_data[3]

            # Extract the limit value from incoming message
            limit_value = 0.0
            if isinstance(payload, dict) and 'csChargingProfiles' in payload:
                cs_profiles = payload['csChargingProfiles']
                if (isinstance(cs_profiles, dict) and
                    'chargingSchedule' in cs_profiles and
                    isinstance(cs_profiles['chargingSchedule'], dict) and
                    'chargingSchedulePeriod' in cs_profiles['chargingSchedule'] and
                    isinstance(cs_profiles['chargingSchedule']['chargingSchedulePeriod'], list) and
                    len(cs_profiles['chargingSchedule']['chargingSchedulePeriod']) > 0):

                    first_period = cs_profiles['chargingSchedule']['chargingSchedulePeriod'][0]
                    if isinstance(first_period, dict) and 'limit' in first_period:
                        limit_value = first_period['limit']

            # Create standardized payload format
            standardized_payload = {
                "connectorId": 0,
                "csChargingProfiles": {
                    "chargingProfileId": 231,
                    "stackLevel": 0,
                    "chargingProfilePurpose": "TxDefaultProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": {
                        "chargingRateUnit": "W",
                        "chargingSchedulePeriod": [
                            {
                                "startPeriod": 0,
                                "limit": float(limit_value),
                                "numberPhases": 3
                            }
                        ],
                        "duration": None,
                        "startSchedule": None,
                        "minChargingRate": None
                    },
                    "transactionId": None,
                    "recurrencyKind": None,
                    "validFrom": None,
                    "validTo": None
                }
            }

            # Replace the payload with standardized format
            message_data[3] = standardized_payload
            logger.info(f"Standardized SetChargingProfile message with limit: {limit_value}")

    def _should_block_message(self, message_data):
        """Check if message should be blocked (not forwarded to wallbox)"""
        if not isinstance(message_data, list) or len(message_data) < 4:
            return False

        # Check for ChangeConfiguration commands
        if message_data[2] == "ChangeConfiguration":
            payload = message_data[3]
            if isinstance(payload, dict) and 'key' in payload:
                config_key = payload['key']

                # B.7 configuration keys that should be ALLOWED (not blocked)
                b7_keys = {
                    'LocalPreAuthorize',
                    'StopTransactionOnEVSideDisconnect',
                    'LocalAuthorizeOffline',
                    'AuthorizeRemoteTxRequests',
                    'HeartbeatInterval',
                    'MeterValueSampleInterval',
                    'MeterValuesAlignedData',
                    'MeterValuesSampledData',
                    'ClockAlignedDataInterval',
                    'ConnectionTimeOut',
                    'ResetRetries'
                }

                if config_key in b7_keys:
                    logger.info(f"Allowing B.7 ChangeConfiguration for key: {config_key}")
                    return False  # Don't block B.7 calls
                else:
                    logger.info(f"Blocking regular ChangeConfiguration for key: {config_key}")
                    return True  # Block non-B.7 calls

        return False

    async def _send_config_after_boot(self, websocket, target_ws):
        """Wait for BootNotification response, then send config commands"""
        # Wait longer to ensure EVCC has processed BootNotification and sent its response
        # This prevents conflicts with EVCC's own configuration commands
        await asyncio.sleep(5)
        await self._send_config_commands(websocket, target_ws)

    async def _send_config_commands(self, websocket, target_ws):
        """Send ChangeConfiguration commands to wallbox after BootNotification"""
        import random

        # Configuration commands to send
        configs = [
            ("LocalPreAuthorize", "true"),
            ("LocalAuthorizeOffline", "false"),
            ("LocalAuthListEnabled", "false"),
            ("AuthorizeRemoteTxRequests", "false")
        ]

        logger.info("Sending configuration changes to wallbox...")

        for key, value in configs:
            # Generate unique message ID
            msg_id = str(random.randint(1000000000, 9999999999))

            # Create ChangeConfiguration message
            config_message = [2, msg_id, "ChangeConfiguration", {"key": key, "value": value}]
            message_json = json.dumps(config_message)

            logger.info(f"Sending ChangeConfiguration: {key}={value}")
            ocpp_logger.info(f"[proxy->client-CONFIG] {message_json}")

            # Update live status with this configuration
            with self.status_lock:
                self.live_status['wallbox']['configuration'][key] = value

            # Send to wallbox
            await websocket.send(message_json)

            # Wait a bit before sending next command
            await asyncio.sleep(0.5)

    async def _send_boot_notification_now(self, station_id, boot_info):
        """Send a BootNotification immediately to EVCC"""
        import random
        try:
            target_url = f"ws://{self.target_host}:{self.target_port}{station_id}"
            logger.info(f"Sending immediate BootNotification to EVCC for {station_id}")
            async with websockets.connect(target_url, subprotocols=["ocpp1.6"], open_timeout=2) as evcc_ws:
                msg_id = str(random.randint(1000000000, 9999999999))
                boot_msg = [2, msg_id, "BootNotification", boot_info]
                await evcc_ws.send(json.dumps(boot_msg))
                ocpp_logger.info(f"[IMMEDIATE-BOOT] Sent to EVCC for {station_id}: {json.dumps(boot_msg)}")

                # Wait for response
                try:
                    response = await asyncio.wait_for(evcc_ws.recv(), timeout=5.0)
                    ocpp_logger.info(f"[IMMEDIATE-BOOT] EVCC response for {station_id}: {response}")

                    # Parse response to see if accepted
                    resp_data = json.loads(response)
                    if isinstance(resp_data, list) and len(resp_data) >= 3 and resp_data[0] == 3:
                        # CallResult received
                        payload = resp_data[2]
                        if isinstance(payload, dict) and payload.get('status') == 'Accepted':
                            logger.info(f"EVCC immediately accepted BootNotification for {station_id}")
                            with self.boot_lock:
                                if station_id in self.pending_boot_wallboxes:
                                    self.pending_boot_wallboxes[station_id]['evcc_confirmed'] = True
                                    self.pending_boot_wallboxes[station_id]['last_sent'] = datetime.now()
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout waiting for EVCC response for immediate BootNotification {station_id}")
        except Exception as e:
            logger.debug(f"Could not send immediate BootNotification to EVCC for {station_id}: {e}")

    async def _send_startup_boot_notifications(self):
        """Send BootNotifications for known wallboxes immediately after proxy starts"""
        # Hardcoded wallbox information
        wallboxes = [
            {
                "path": "/Actec",
                "delay": 0,  # Send immediately
                "boot_info": {
                    "chargePointModel": "AcTec SmartCharger",
                    "chargePointVendor": "AcTec",
                    "chargePointSerialNumber": "Actec",
                    "firmwareVersion": "V1.0.0"
                }
            },
            {
                "path": "/AE104ABG00029B",
                "delay": 10,  # Send 10 seconds later
                "boot_info": {
                    "chargePointModel": "AE104",
                    "chargePointVendor": "ELECQ",
                    "chargePointSerialNumber": "AE104ABG00029B",
                    "firmwareVersion": "EPRO001_V1.2.0(7-1761213148)"
                }
            }
        ]

        for wallbox in wallboxes:
            await asyncio.sleep(wallbox["delay"])
            try:
                # Connect to EVCC for this wallbox
                target_url = f"ws://{self.target_host}:{self.target_port}{wallbox['path']}"
                logger.info(f"Sending startup BootNotification for {wallbox['path']} to {target_url}")

                async with websockets.connect(target_url, subprotocols=["ocpp1.6"], open_timeout=5) as ws:
                    msg_id = str(random.randint(1000000000, 9999999999))
                    boot_msg = [2, msg_id, "BootNotification", wallbox["boot_info"]]
                    await ws.send(json.dumps(boot_msg))
                    logger.info(f"Sent BootNotification to EVCC for {wallbox['path']}: {json.dumps(boot_msg)}")

                    # Wait for response
                    try:
                        response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        logger.info(f"EVCC response for {wallbox['path']}: {response}")
                    except asyncio.TimeoutError:
                        logger.warning(f"Timeout waiting for EVCC response for {wallbox['path']}")
            except Exception as e:
                logger.error(f"Failed to send BootNotification for {wallbox['path']}: {e}")

    async def _background_tasks(self):
        """Simple background loop for monitoring tasks (no periodic BootNotifications per OCPP protocol)"""
        while True:
            try:
                await asyncio.sleep(60)  # Monitor every 60 seconds

                # Just log status of tracked wallboxes
                with self.boot_lock:
                    if self.pending_boot_wallboxes:
                        logger.debug(f"Tracking {len(self.pending_boot_wallboxes)} wallboxes for BootNotification handling")

            except Exception as e:
                logger.error(f"Background task error: {e}")
                await asyncio.sleep(5)

    async def handle_client(self, websocket, path):
        """Handle incoming client connection and proxy to target"""
        client_address = websocket.remote_address
        logger.info(f"New client connection from {client_address} requesting path: {path}")

        # DISABLED: Path cleaning (firmware V1.17.9 no longer needs this)
        # cleaned_path = self.clean_url_path(path)
        cleaned_path = path  # Pass through unchanged
        logger.info(f"Path (no cleaning): {cleaned_path}")

        # Build target URL
        target_url = f"ws://{self.target_host}:{self.target_port}{cleaned_path}"
        logger.info(f"Attempting to connect to target: {target_url}")

        # Get subprotocols from the client
        try:
            client_subprotocols = getattr(websocket, 'subprotocols', [])
            logger.info(f"Client subprotocols: {client_subprotocols}")
        except:
            client_subprotocols = ["ocpp1.6"]  # Default to OCPP
            logger.info(f"Using default subprotocols: {client_subprotocols}")

        # Try to connect to target server
        target_ws = None
        try:
            target_ws = await websockets.connect(
                target_url,
                subprotocols=["ocpp1.6"]
            )
            logger.info(f"Connected to target server, starting bidirectional proxy for {client_address}")
            logger.info(f"Target subprotocol: {target_ws.subprotocol}")

            # Store connections for config commands
            self.current_target_ws = target_ws
            self.current_client_ws = websocket

            # Create bidirectional proxy
            try:
                await asyncio.gather(
                    self.proxy_messages(websocket, target_ws, "client->target", cleaned_path),
                    self.proxy_messages(target_ws, websocket, "target->client", cleaned_path),
                    return_exceptions=True
                )
            except Exception as proxy_error:
                logger.warning(f"Proxy error for {client_address}: {proxy_error}")
            finally:
                # If EVCC connection drops, close wallbox connection too
                logger.info(f"Closing wallbox connection {client_address} - bidirectional proxy ended")
                await websocket.close(1011, "Backend connection lost")
                await target_ws.close()
        except Exception as e:
            logger.warning(f"Cannot connect to target server {target_url}: {e}")
            logger.info(f"Disconnecting wallbox {client_address} - EVCC is not available")

            # Close wallbox connection immediately - let it retry later when EVCC is back
            await websocket.close(1011, "EVCC backend not available")
            return

    def _track_trigger_message(self, station_id, message_id, connector_id, wallbox_ws, evcc_ws):
        """Track TriggerMessage request and schedule StatusNotification response if wallbox doesn't respond"""
        with self.trigger_lock:
            self.pending_trigger_requests[station_id] = {
                'message_id': message_id,
                'connector_id': connector_id,
                'timestamp': datetime.now(),
                'wallbox_ws': wallbox_ws,
                'evcc_ws': evcc_ws
            }
            logger.info(f"Tracked TriggerMessage from EVCC for {station_id}, connector {connector_id}")

        # Schedule task to send StatusNotification if wallbox doesn't respond in 3 seconds
        asyncio.create_task(self._send_status_notification_if_needed(station_id, message_id, connector_id, wallbox_ws, evcc_ws))

    async def _send_status_notification_if_needed(self, station_id, message_id, connector_id, wallbox_ws, evcc_ws):
        """Send StatusNotification to EVCC if wallbox doesn't respond within timeout"""
        await asyncio.sleep(3)  # Wait 3 seconds for wallbox to respond

        with self.trigger_lock:
            # Check if trigger request still pending (wallbox hasn't responded)
            if station_id in self.pending_trigger_requests:
                pending = self.pending_trigger_requests[station_id]
                if pending['message_id'] == message_id:
                    # Wallbox didn't respond, send StatusNotification on its behalf
                    logger.info(f"Wallbox {station_id} didn't respond to TriggerMessage, sending StatusNotification")

                    # Get current status or default to Available
                    status_info = self.connector_status.get(station_id, {
                        'status': 'Available',
                        'error_code': 'NoError',
                        'connector_id': connector_id,
                        'transaction_id': None
                    })

                    # Generate unique message ID for StatusNotification
                    status_msg_id = str(random.randint(1000000, 9999999))

                    # Create StatusNotification message
                    status_notification = [
                        2,  # CALL message
                        status_msg_id,
                        "StatusNotification",
                        {
                            "connectorId": connector_id,
                            "status": status_info['status'],
                            "errorCode": status_info['error_code'],
                            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                        }
                    ]

                    status_json = json.dumps(status_notification)

                    # Send to EVCC as if it came from wallbox
                    try:
                        await evcc_ws.send(status_json)
                        ocpp_logger.info(f"[client->target-SYNTHETIC] {status_json}")
                        self._add_message_to_buffer("client->target", status_json, "SYNTHETIC", station_id)
                        logger.info(f"Sent synthetic StatusNotification to EVCC for {station_id}")

                        # Also need to send TriggerMessage response to wallbox saying Accepted
                        trigger_response = [
                            3,  # CALL RESULT
                            message_id,
                            {"status": "Accepted"}
                        ]
                        trigger_response_json = json.dumps(trigger_response)
                        await wallbox_ws.send(trigger_response_json)
                        ocpp_logger.info(f"[target->client-SYNTHETIC] {trigger_response_json}")
                        logger.info(f"Sent synthetic TriggerMessage response to wallbox {station_id}")

                    except Exception as e:
                        logger.error(f"Error sending synthetic StatusNotification: {e}")

                    # Remove from pending
                    del self.pending_trigger_requests[station_id]

    def _track_status_notification(self, station_id, parsed_message):
        """Track StatusNotification from wallbox and clear pending trigger"""
        if len(parsed_message) >= 4:
            payload = parsed_message[3]
            if isinstance(payload, dict):
                # Update connector status
                connector_id = payload.get('connectorId', 1)
                status = payload.get('status', 'Unknown')
                error_code = payload.get('errorCode', 'NoError')

                self.connector_status[station_id] = {
                    'status': status,
                    'error_code': error_code,
                    'connector_id': connector_id,
                    'transaction_id': self.connector_status.get(station_id, {}).get('transaction_id')
                }
                logger.info(f"Tracked StatusNotification from {station_id}: connector {connector_id} status={status}")

                # Clear pending trigger request since wallbox responded
                with self.trigger_lock:
                    if station_id in self.pending_trigger_requests:
                        del self.pending_trigger_requests[station_id]
                        logger.info(f"Cleared pending TriggerMessage for {station_id} (wallbox responded)")

    def _track_transaction_start(self, station_id, parsed_message):
        """Track StartTransaction to update connector status"""
        if len(parsed_message) >= 4:
            payload = parsed_message[3]
            if isinstance(payload, dict):
                connector_id = payload.get('connectorId', 1)
                if station_id not in self.connector_status:
                    self.connector_status[station_id] = {}
                self.connector_status[station_id]['status'] = 'Charging'
                self.connector_status[station_id]['connector_id'] = connector_id
                logger.info(f"Tracked StartTransaction from {station_id}: connector {connector_id} → Charging")

    def _track_transaction_stop(self, station_id, parsed_message):
        """Track StopTransaction to update connector status"""
        if len(parsed_message) >= 4:
            payload = parsed_message[3]
            if isinstance(payload, dict):
                if station_id not in self.connector_status:
                    self.connector_status[station_id] = {}
                self.connector_status[station_id]['status'] = 'Available'
                self.connector_status[station_id]['transaction_id'] = None
                logger.info(f"Tracked StopTransaction from {station_id} → Available")

    async def proxy_messages(self, source_ws, dest_ws, direction, station_id=""):
        """Proxy messages between WebSocket connections"""
        try:
            async for message in source_ws:
                # Check if we should block this message
                should_block = False

                if direction == "client->target":
                    # Messages from wallbox to evcc
                    try:
                        parsed_message = json.loads(message)

                        # FirmwareStatusNotification blocking DISABLED - allowing all messages through
                        # (Previously blocked, now forwarding to test EVCC compatibility)

                        # Track StatusNotification responses
                        if (isinstance(parsed_message, list) and
                            len(parsed_message) >= 4 and
                            parsed_message[0] == 2 and
                            parsed_message[2] == "StatusNotification"):
                            # Wallbox sent StatusNotification - track it and remove pending trigger
                            self._track_status_notification(station_id, parsed_message)

                        # Track transaction states
                        elif (isinstance(parsed_message, list) and
                              len(parsed_message) >= 4 and
                              parsed_message[0] == 2):
                            action = parsed_message[2]
                            if action == "StartTransaction":
                                self._track_transaction_start(station_id, parsed_message)
                            elif action == "StopTransaction":
                                self._track_transaction_stop(station_id, parsed_message)

                    except (json.JSONDecodeError, Exception) as e:
                        logger.debug(f"Could not parse message for blocking check: {e}")

                elif direction == "target->client":
                    # Messages from EVCC to wallbox
                    try:
                        parsed_message = json.loads(message)

                        # Intercept ChangeAvailability - wallbox doesn't support this optional command
                        if (isinstance(parsed_message, list) and
                            len(parsed_message) >= 4 and
                            parsed_message[0] == 2 and
                            parsed_message[2] == "ChangeAvailability"):
                            message_id = parsed_message[1]
                            # Log the intercepted command
                            ocpp_logger.info(f"[{direction}-AUTO-RESPONSE] {message}")
                            self._add_message_to_buffer(direction, message, "AUTO-RESPONSE", station_id)
                            logger.info(f"Intercepted ChangeAvailability from EVCC - sending auto-response and forwarding to wallbox")

                            # Send acceptance response on behalf of wallbox
                            response = [3, message_id, {"status": "Accepted"}]
                            response_json = json.dumps(response)
                            ocpp_logger.info(f"[client->target-AUTO-RESPONSE] {response_json}")
                            self._add_message_to_buffer("client->target", response_json, "AUTO-RESPONSE", station_id)
                            await source_ws.send(response_json)
                            logger.info(f"Sent ChangeAvailability response (Accepted) to EVCC on behalf of wallbox")

                        # Intercept GetConfiguration - provide minimal supported configuration
                        if (isinstance(parsed_message, list) and
                            len(parsed_message) >= 4 and
                            parsed_message[0] == 2 and
                            parsed_message[2] == "GetConfiguration"):
                            message_id = parsed_message[1]
                            payload = parsed_message[3]
                            # Log the intercepted command
                            ocpp_logger.info(f"[{direction}-AUTO-RESPONSE] {message}")
                            self._add_message_to_buffer(direction, message, "AUTO-RESPONSE", station_id)
                            logger.info(f"Intercepted GetConfiguration from EVCC - sending auto-response and forwarding to wallbox")

                            # Build configuration response with OCPP B.7 keys
                            requested_keys = payload.get('key', []) if isinstance(payload, dict) else []
                            config_keys = [
                                {"key": "HeartbeatInterval", "readonly": False, "value": "60"},
                                {"key": "LocalPreAuthorize", "readonly": False, "value": "true"},
                                {"key": "LocalAuthorizeOffline", "readonly": False, "value": "false"},
                                {"key": "LocalAuthListEnabled", "readonly": False, "value": "false"},
                                {"key": "AuthorizeRemoteTxRequests", "readonly": False, "value": "false"}
                            ]

                            # If specific keys requested, filter to those keys
                            if requested_keys:
                                config_keys = [k for k in config_keys if k['key'] in requested_keys]

                            response = [3, message_id, {
                                "configurationKey": config_keys,
                                "unknownKey": []
                            }]
                            response_json = json.dumps(response)
                            ocpp_logger.info(f"[client->target-AUTO-RESPONSE] {response_json}")
                            self._add_message_to_buffer("client->target", response_json, "AUTO-RESPONSE", station_id)
                            await source_ws.send(response_json)
                            logger.info(f"Sent GetConfiguration response to EVCC with {len(config_keys)} configuration keys")

                        # Intercept TriggerMessage requests from EVCC
                        elif (isinstance(parsed_message, list) and
                            len(parsed_message) >= 4 and
                            parsed_message[0] == 2 and
                            parsed_message[2] == "TriggerMessage"):
                            payload = parsed_message[3]
                            requested_message = payload.get('requestedMessage') if isinstance(payload, dict) else None

                            # Handle TriggerMessage for BootNotification
                            # DISABLED: Testing wallbox behavior without auto-response
                            if requested_message == 'BootNotification':
                                message_id = parsed_message[1]
                                # Log the intercepted command
                                ocpp_logger.info(f"[{direction}-AUTO-RESPONSE] {message}")
                                self._add_message_to_buffer(direction, message, "AUTO-RESPONSE", station_id)
                                logger.info(f"Intercepted TriggerMessage(BootNotification) from EVCC - sending auto-response and forwarding to wallbox")

                                # Send TriggerMessage response (Accepted)
                                trigger_response = [3, message_id, {"status": "Accepted"}]
                                trigger_response_json = json.dumps(trigger_response)
                                ocpp_logger.info(f"[client->target-AUTO-RESPONSE] {trigger_response_json}")
                                self._add_message_to_buffer("client->target", trigger_response_json, "AUTO-RESPONSE", station_id)
                                await source_ws.send(trigger_response_json)
                                logger.info(f"Sent TriggerMessage response (Accepted) to EVCC")

                                # Generate a new message ID for the BootNotification
                                import random
                                boot_msg_id = str(random.randint(1000, 9999))

                                # Send BootNotification on behalf of wallbox (to EVCC)
                                boot_notification = [2, boot_msg_id, "BootNotification", {
                                    "chargePointModel": "EV-AC22K",
                                    "chargePointVendor": "AcTEC",
                                    "chargePointSerialNumber": "Actec",
                                    "firmwareVersion": "V1.17.9"
                                }]
                                boot_json = json.dumps(boot_notification)
                                ocpp_logger.info(f"[client->target-AUTO-RESPONSE] {boot_json}")
                                self._add_message_to_buffer("client->target", boot_json, "AUTO-RESPONSE", station_id)
                                # Send to EVCC (source_ws in target->client direction)
                                await source_ws.send(boot_json)
                                logger.info(f"Sent BootNotification to EVCC on behalf of wallbox")

                            # Handle TriggerMessage for StatusNotification
                            elif requested_message == 'StatusNotification':
                                # Store the trigger request and schedule response if wallbox doesn't reply
                                message_id = parsed_message[1]
                                connector_id = payload.get('connectorId', 1)
                                self._track_trigger_message(station_id, message_id, connector_id, dest_ws, source_ws)

                    except (json.JSONDecodeError, Exception) as e:
                        logger.debug(f"Could not parse message for auto-response: {e}")

                # Log and forward all non-blocked messages
                if not should_block:
                    ocpp_logger.info(f"[{direction}] {message}")
                    self._add_message_to_buffer(direction, message, "", station_id)
                    logger.debug(f"Proxying message ({direction}): {message[:100]}...")
                    await dest_ws.send(message)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Connection closed ({direction})")
        except Exception as e:
            logger.error(f"Error proxying messages ({direction}): {e}")

    async def handle_web_index(self, request):
        """Serve the web interface HTML page"""
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>OCPP Proxy Monitor</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #1e1e1e; color: #d4d4d4; }
        .header { background: #252526; padding: 15px 20px; border-bottom: 1px solid #3e3e42; }
        .header h1 { color: #fff; font-size: 24px; }
        .header .stats { margin-top: 8px; font-size: 14px; color: #808080; }
        .controls { background: #2d2d30; padding: 10px 20px; border-bottom: 1px solid #3e3e42; }
        .controls button { background: #0e639c; color: #fff; border: none; padding: 8px 16px; margin-right: 10px; cursor: pointer; border-radius: 3px; font-size: 14px; }
        .controls button:hover { background: #1177bb; }
        .controls button.danger { background: #d13438; }
        .controls button.danger:hover { background: #e81123; }
        .messages { padding: 20px; max-height: calc(100vh - 160px); overflow-y: auto; }
        .message { background: #252526; margin-bottom: 10px; border-radius: 4px; border: 1px solid #3e3e42; overflow: hidden; }
        .message-header { padding: 10px 15px; background: #2d2d30; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
        .message-header:hover { background: #333333; }
        .message-header .time { color: #858585; font-size: 12px; font-family: 'Consolas', 'Courier New', monospace; }
        .message-header .direction { font-weight: bold; font-size: 14px; }
        .message-header .direction.wallbox-to-evcc { color: #4ec9b0; }
        .message-header .direction.evcc-to-wallbox { color: #569cd6; }
        .message-header .tag { background: #d7ba7d; color: #1e1e1e; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: bold; }
        .message-header .tag.CONVERTED { background: #ffd700; }
        .message-header .tag.BLOCKED { background: #f48771; }
        .message-header .tag.STANDARDIZED { background: #ce9178; }
        .message-header .tag.QUEUED { background: #569cd6; }
        .message-header .tag.MONITOR-ONLY { background: #ce9178; }
        .message-header .tag.AUTO-RESPONSE { background: #569cd6; }
        .message-header .station { background: #0e639c; color: #fff; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: bold; margin-left: 8px; }
        .message-body { padding: 15px; display: none; }
        .message-body.open { display: block; }
        .message-body pre { background: #1e1e1e; padding: 12px; border-radius: 3px; overflow-x: auto; font-family: 'Consolas', 'Courier New', monospace; font-size: 13px; line-height: 1.5; }
        .message-type { font-size: 13px; color: #c586c0; margin-left: 10px; }
        .no-messages { text-align: center; padding: 40px; color: #808080; font-size: 16px; }
        .wallboxes { background: #2d2d30; padding: 10px 20px; border-bottom: 1px solid #3e3e42; display: flex; align-items: center; gap: 15px; }
        .wallboxes .label { color: #808080; font-size: 14px; }
        .wallbox-badge { background: #0e639c; color: #fff; padding: 6px 12px; border-radius: 4px; font-size: 13px; font-weight: 500; }
        .wallbox-badge.monitor { background: #d7ba7d; color: #1e1e1e; }
        .tabs { background: #2d2d30; padding: 0 20px; border-bottom: 1px solid #3e3e42; display: flex; gap: 5px; }
        .tab { padding: 12px 20px; cursor: pointer; font-size: 14px; color: #808080; border-bottom: 2px solid transparent; transition: all 0.2s; }
        .tab:hover { color: #d4d4d4; background: #333333; }
        .tab.active { color: #569cd6; border-bottom-color: #569cd6; }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>⚡ OCPP Proxy Monitor</h1>
            <div class="stats">
                <span id="message-count">0 messages</span> |
                <span id="status">Connected</span>
            </div>
        </div>
        <div style="display: flex; gap: 10px;">
            <a href="/status" style="background: #0e639c; color: #fff; padding: 8px 16px; text-decoration: none; border-radius: 3px; font-size: 14px;">📊 Status</a>
            <a href="/" style="background: #569cd6; color: #fff; padding: 8px 16px; text-decoration: none; border-radius: 3px; font-size: 14px;">📝 Messages</a>
        </div>
    </div>
    <div class="wallboxes">
        <span class="label">Connected Wallboxes:</span>
        <div id="wallbox-list">
            <span style="color: #808080;">Loading...</span>
        </div>
    </div>
    <div class="tabs" id="tabs">
        <div class="tab active" onclick="switchTab('all', this)">All Messages</div>
    </div>
    <div class="controls">
        <button onclick="refreshMessages()">🔄 Refresh</button>
        <button onclick="clearMessages()" class="danger">🗑️ Clear</button>
        <button onclick="toggleAutoRefresh()">⏱️ Auto-refresh: <span id="auto-status">ON</span></button>
    </div>
    <div class="messages" id="messages">
        <div class="no-messages">Loading messages...</div>
    </div>

    <script>
        let autoRefresh = true;
        let autoRefreshInterval = null;
        let currentFilter = 'all';
        let wallboxes = [];

        function formatTimestamp(ts) {
            const date = new Date(ts);
            return date.toLocaleTimeString() + '.' + date.getMilliseconds().toString().padStart(3, '0');
        }

        function formatOCPPMessage(msg) {
            try {
                const parsed = JSON.parse(msg);
                return JSON.stringify(parsed, null, 2);
            } catch {
                return msg;
            }
        }

        function getMessageType(msg) {
            try {
                const parsed = JSON.parse(msg);
                if (Array.isArray(parsed) && parsed.length >= 3) {
                    const msgType = parsed[0];
                    const action = parsed[2];
                    if (msgType === 2) return `CALL: ${action}`;
                    if (msgType === 3) return `RESULT`;
                    if (msgType === 4) return `ERROR: ${action}`;
                }
            } catch {}
            return '';
        }

        function toggleMessage(id) {
            const body = document.getElementById('body-' + id);
            body.classList.toggle('open');
        }

        function switchTab(filter, element) {
            currentFilter = filter;
            // Update tab UI
            document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
            if (element) {
                element.classList.add('active');
            }
            // Refresh messages with filter
            refreshMessages();
        }

        async function fetchWallboxes() {
            console.log('fetchWallboxes called');
            try {
                const response = await fetch('/wallboxes');
                console.log('Response:', response);
                if (!response.ok) {
                    throw new Error('Failed to fetch wallboxes: ' + response.status);
                }
                const data = await response.json();
                console.log('Wallboxes data:', data);
                wallboxes = data.wallboxes || [];

                // Update wallbox list
                const listEl = document.getElementById('wallbox-list');
                if (!listEl) {
                    console.error('wallbox-list element not found');
                    return;
                }

                if (wallboxes.length === 0) {
                    listEl.innerHTML = '<span style="color: #808080;">None</span>';
                } else {
                    listEl.innerHTML = wallboxes.map(wb => {
                        const className = wb.mode === 'monitor-only' ? 'wallbox-badge monitor' : 'wallbox-badge';
                        return '<span class="' + className + '">' + wb.station_id + ' (' + wb.mode + ')</span>';
                    }).join(' ');
                }

                // Update tabs
                const tabsEl = document.getElementById('tabs');
                if (!tabsEl) {
                    console.error('tabs element not found');
                    return;
                }

                let tabsHTML = `<div class="tab active" onclick="switchTab('all', this)">All Messages</div>`;
                wallboxes.forEach(wb => {
                    tabsHTML += `<div class="tab" onclick="switchTab('${wb.station_id}', this)">${wb.station_id}</div>`;
                });
                tabsEl.innerHTML = tabsHTML;
            } catch (error) {
                console.error('Error fetching wallboxes:', error);
                const listEl = document.getElementById('wallbox-list');
                if (listEl) {
                    listEl.innerHTML = '<span style="color: #f48771;">Error loading</span>';
                }
            }
        }

        async function refreshMessages() {
            try {
                const response = await fetch('/messages');
                const data = await response.json();

                const container = document.getElementById('messages');
                if (data.messages.length === 0) {
                    container.innerHTML = '<div class="no-messages">No messages yet. Waiting for OCPP traffic...</div>';
                    document.getElementById('message-count').textContent = '0 messages';
                    return;
                }

                // Filter messages by selected wallbox
                const filteredMessages = currentFilter === 'all'
                    ? data.messages
                    : data.messages.filter(msg => msg.station_id === currentFilter);

                let html = '';
                filteredMessages.forEach((msg, idx) => {
                    const directionClass = msg.direction === 'client->target' ? 'wallbox-to-evcc' : 'evcc-to-wallbox';
                    const directionText = msg.direction === 'client->target' ? '📤 Wallbox → EVCC' : '📥 EVCC → Wallbox';
                    const tag = msg.tag ? `<span class="tag ${msg.tag}">${msg.tag}</span>` : '';
                    const station = msg.station_id ? `<span class="station">${msg.station_id}</span>` : '';
                    const msgType = getMessageType(msg.message);
                    const typeSpan = msgType ? `<span class="message-type">${msgType}</span>` : '';

                    html += `
                        <div class="message">
                            <div class="message-header" onclick="toggleMessage(${idx})">
                                <div>
                                    <span class="direction ${directionClass}">${directionText}</span>
                                    ${typeSpan}
                                    ${station}
                                </div>
                                <div>
                                    ${tag}
                                    <span class="time">${formatTimestamp(msg.timestamp)}</span>
                                </div>
                            </div>
                            <div class="message-body" id="body-${idx}">
                                <pre>${formatOCPPMessage(msg.message)}</pre>
                            </div>
                        </div>
                    `;
                });

                container.innerHTML = html;
                const totalCount = data.messages.length;
                const filteredCount = filteredMessages.length;
                const countText = currentFilter === 'all'
                    ? `${totalCount} messages`
                    : `${filteredCount} of ${totalCount} messages`;
                document.getElementById('message-count').textContent = countText;
                document.getElementById('status').textContent = 'Connected';
            } catch (error) {
                document.getElementById('status').textContent = 'Connection Error';
                console.error('Error fetching messages:', error);
            }
        }

        async function clearMessages() {
            if (confirm('Clear all messages from buffer?')) {
                try {
                    await fetch('/clear', { method: 'POST' });
                    await refreshMessages();
                } catch (error) {
                    console.error('Error clearing messages:', error);
                }
            }
        }

        function toggleAutoRefresh() {
            autoRefresh = !autoRefresh;
            document.getElementById('auto-status').textContent = autoRefresh ? 'ON' : 'OFF';

            if (autoRefresh) {
                startAutoRefresh();
            } else {
                if (autoRefreshInterval) {
                    clearInterval(autoRefreshInterval);
                    autoRefreshInterval = null;
                }
            }
        }

        function startAutoRefresh() {
            if (autoRefreshInterval) clearInterval(autoRefreshInterval);
            autoRefreshInterval = setInterval(() => {
                fetchWallboxes();
                refreshMessages();
            }, 2000);
        }

        // Initial load and start auto-refresh
        fetchWallboxes();
        refreshMessages();
        startAutoRefresh();
    </script>
</body>
</html>
        """
        return web.Response(text=html, content_type='text/html')

    async def handle_messages_api(self, request):
        """API endpoint to get messages"""
        with self.buffer_lock:
            messages = list(self.message_buffer)
        return web.json_response({'messages': messages})

    async def handle_wallboxes_api(self, request):
        """API endpoint to get connected wallboxes"""
        # Get unique station IDs from recent messages
        with self.buffer_lock:
            station_ids = set()
            for msg in self.message_buffer:
                if msg.get('station_id'):
                    station_ids.add(msg['station_id'])

        wallboxes = []
        for sid in sorted(station_ids):
            # Check boot confirmation status
            with self.boot_lock:
                pending_info = self.pending_boot_wallboxes.get(sid, {})
                evcc_confirmed = pending_info.get('evcc_confirmed', False)
            wallboxes.append({
                'station_id': sid,
                'mode': 'evcc-confirmed' if evcc_confirmed else 'waiting-for-evcc'
            })

        return web.json_response({'wallboxes': wallboxes})

    async def handle_clear_api(self, request):
        """API endpoint to clear message buffer"""
        with self.buffer_lock:
            self.message_buffer.clear()
        return web.json_response({'status': 'cleared'})

    async def handle_status_api(self, request):
        """API endpoint to get live status data"""
        with self.status_lock:
            status = json.loads(json.dumps(self.live_status))  # Deep copy
        return web.json_response(status)

    async def handle_reboot_api(self, request):
        """API endpoint to reboot the wallbox"""
        import random

        try:
            # Check if we have an active client connection
            if not hasattr(self, 'current_client_ws') or self.current_client_ws is None:
                return web.json_response({'status': 'error', 'message': 'No active wallbox connection'}, status=503)

            # Generate unique message ID
            msg_id = str(random.randint(1000000000, 9999999999))

            # Create OCPP Reset message (Hard reset - forces actual reboot)
            reset_message = [2, msg_id, "Reset", {"type": "Hard"}]
            message_json = json.dumps(reset_message)

            logger.info("Sending Reset command to wallbox")
            ocpp_logger.info(f"[proxy->client-RESET] {message_json}")

            # Send to wallbox
            await self.current_client_ws.send(message_json)

            return web.json_response({'status': 'success', 'message': 'Reset command sent to wallbox'})

        except Exception as e:
            logger.error(f"Error sending reset command: {e}")
            return web.json_response({'status': 'error', 'message': str(e)}, status=500)

    async def handle_stop_transaction_api(self, request):
        """API endpoint to stop a stuck transaction"""
        import random

        try:
            # Check if we have an active client connection
            if not hasattr(self, 'current_client_ws') or self.current_client_ws is None:
                return web.json_response({'status': 'error', 'message': 'No active wallbox connection'}, status=503)

            # Get transaction ID from current status
            with self.status_lock:
                transaction_id = self.live_status['wallbox']['transaction_id']

            if not transaction_id:
                return web.json_response({'status': 'error', 'message': 'No active transaction to stop'}, status=400)

            # Generate unique message ID
            msg_id = str(random.randint(1000000000, 9999999999))

            # Create OCPP RemoteStopTransaction message
            stop_message = [2, msg_id, "RemoteStopTransaction", {"transactionId": transaction_id}]
            message_json = json.dumps(stop_message)

            logger.info(f"Sending RemoteStopTransaction command for transaction {transaction_id}")
            ocpp_logger.info(f"[proxy->client-STOP_TX] {message_json}")

            # Send to wallbox
            await self.current_client_ws.send(message_json)

            return web.json_response({'status': 'success', 'message': f'Stop transaction {transaction_id} command sent to wallbox'})

        except Exception as e:
            logger.error(f"Error sending stop transaction command: {e}")
            return web.json_response({'status': 'error', 'message': str(e)}, status=500)

    async def handle_get_configuration_api(self, request):
        """API endpoint to get wallbox configuration"""
        import random

        try:
            # Check if we have an active client connection
            if not hasattr(self, 'current_client_ws') or self.current_client_ws is None:
                return web.json_response({'status': 'error', 'message': 'No active wallbox connection'}, status=503)

            # Generate unique message ID
            msg_id = str(random.randint(1000000000, 9999999999))

            # Create OCPP GetConfiguration message (empty array = get all parameters)
            get_config_message = [2, msg_id, "GetConfiguration", {}]
            message_json = json.dumps(get_config_message)

            logger.info("Sending GetConfiguration command to wallbox")
            ocpp_logger.info(f"[proxy->client-GETCONFIG] {message_json}")

            # Send to wallbox
            await self.current_client_ws.send(message_json)

            return web.json_response({'status': 'success', 'message': 'GetConfiguration command sent to wallbox'})

        except Exception as e:
            logger.error(f"Error sending GetConfiguration command: {e}")
            return web.json_response({'status': 'error', 'message': str(e)}, status=500)

    async def handle_status_page(self, request):
        """Serve the live status dashboard page"""
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>OCPP Live Status</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #1e1e1e; color: #d4d4d4; }
        .header { background: #252526; padding: 15px 20px; border-bottom: 1px solid #3e3e42; display: flex; justify-content: space-between; align-items: center; }
        .header h1 { color: #fff; font-size: 24px; }
        .header .nav { display: flex; gap: 10px; }
        .header .nav a { background: #0e639c; color: #fff; padding: 8px 16px; text-decoration: none; border-radius: 3px; font-size: 14px; }
        .header .nav a:hover { background: #1177bb; }
        .container { padding: 20px; max-width: 1400px; margin: 0 auto; }
        .row { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }
        .card { background: #252526; border: 1px solid #3e3e42; border-radius: 8px; padding: 20px; }
        .card h2 { color: #fff; font-size: 20px; margin-bottom: 15px; border-bottom: 2px solid #0e639c; padding-bottom: 10px; }
        .card h2 .indicator { width: 12px; height: 12px; border-radius: 50%; display: inline-block; margin-right: 8px; }
        .card h2 .indicator.active { background: #4ec9b0; }
        .card h2 .indicator.inactive { background: #808080; }
        .card h2 .indicator.charging { background: #f1fa8c; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-top: 15px; }
        .metric { background: #2d2d30; padding: 15px; border-radius: 6px; border-left: 3px solid #0e639c; }
        .metric.phase1 { border-left-color: #f48771; }
        .metric.phase2 { border-left-color: #4ec9b0; }
        .metric.phase3 { border-left-color: #569cd6; }
        .metric.total { border-left-color: #f1fa8c; }
        .metric.evcc { border-left-color: #c586c0; }
        .metric-label { font-size: 12px; color: #808080; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 5px; }
        .metric-value { font-size: 28px; font-weight: bold; color: #fff; line-height: 1.2; }
        .metric-unit { font-size: 16px; color: #808080; margin-left: 4px; }
        .status-badge { display: inline-block; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: bold; text-transform: uppercase; }
        .status-badge.available { background: #4ec9b0; color: #1e1e1e; }
        .status-badge.charging { background: #f1fa8c; color: #1e1e1e; }
        .status-badge.preparing { background: #569cd6; color: #1e1e1e; }
        .status-badge.finishing { background: #ce9178; color: #1e1e1e; }
        .status-badge.unknown { background: #808080; color: #1e1e1e; }
        .status-badge.connected { background: #4ec9b0; color: #1e1e1e; }
        .status-badge.not-connected { background: #808080; color: #1e1e1e; }
        .info-row { margin-top: 15px; padding-top: 15px; border-top: 1px solid #3e3e42; font-size: 13px; color: #808080; }
        .info-row span { margin-right: 20px; }
        .no-data { text-align: center; padding: 40px; color: #808080; font-size: 16px; }
        .last-update { font-size: 11px; color: #606060; margin-top: 8px; }
        @media (max-width: 768px) { .row { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="header">
        <h1>⚡ OCPP Live Status</h1>
        <div class="nav">
            <a href="/status">📊 Status</a>
            <a href="/">📝 Messages</a>
            <button onclick="getConfiguration()" style="background: #0e639c; color: #fff; border: none; padding: 8px 16px; cursor: pointer; border-radius: 3px; font-size: 14px; font-weight: 600;">🔍 Get Config</button>
            <button onclick="rebootWallbox()" style="background: #d13438; color: #fff; border: none; padding: 8px 16px; cursor: pointer; border-radius: 3px; font-size: 14px; font-weight: 600;">🔄 Reboot Wallbox</button>
        </div>
    </div>
    <div class="container">
        <div class="row" style="grid-template-columns: 2fr 1fr 1fr;">
            <div class="card">
                <h2>
                    <span class="indicator" id="wallbox-indicator"></span>
                    Wallbox Status
                </h2>
                <div style="margin-bottom: 15px;">
                    <span class="status-badge" id="wallbox-status-badge">Unknown</span>
                    <span id="transaction-info" style="margin-left: 15px; color: #808080; font-size: 13px;"></span>
                </div>

                <div style="margin-bottom: 20px; padding: 15px; background: #2d2d30; border-radius: 6px; display: flex; align-items: center; justify-content: space-between;">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <span style="font-size: 24px;" id="ev-icon">🚗</span>
                        <div>
                            <div style="font-size: 16px; font-weight: bold; color: #fff;">Electric Vehicle</div>
                            <div style="font-size: 13px; color: #808080;" id="ev-status-text">Status unknown</div>
                        </div>
                    </div>
                    <span class="status-badge" id="ev-status-badge" style="font-size: 13px;">Unknown</span>
                </div>

                <h3 style="color: #808080; font-size: 14px; margin: 20px 0 10px 0;">Voltage</h3>
                <div class="metrics">
                    <div class="metric phase1">
                        <div class="metric-label">Phase L1</div>
                        <div class="metric-value"><span id="voltage-l1">0.0</span><span class="metric-unit">V</span></div>
                    </div>
                    <div class="metric phase2">
                        <div class="metric-label">Phase L2</div>
                        <div class="metric-value"><span id="voltage-l2">0.0</span><span class="metric-unit">V</span></div>
                    </div>
                    <div class="metric phase3">
                        <div class="metric-label">Phase L3</div>
                        <div class="metric-value"><span id="voltage-l3">0.0</span><span class="metric-unit">V</span></div>
                    </div>
                </div>

                <h3 style="color: #808080; font-size: 14px; margin: 20px 0 10px 0;">Current</h3>
                <div class="metrics">
                    <div class="metric phase1">
                        <div class="metric-label">Phase L1</div>
                        <div class="metric-value"><span id="current-l1">0.00</span><span class="metric-unit">A</span></div>
                    </div>
                    <div class="metric phase2">
                        <div class="metric-label">Phase L2</div>
                        <div class="metric-value"><span id="current-l2">0.00</span><span class="metric-unit">A</span></div>
                    </div>
                    <div class="metric phase3">
                        <div class="metric-label">Phase L3</div>
                        <div class="metric-value"><span id="current-l3">0.00</span><span class="metric-unit">A</span></div>
                    </div>
                </div>

                <h3 style="color: #808080; font-size: 14px; margin: 20px 0 10px 0;">Power</h3>
                <div class="metrics">
                    <div class="metric phase1">
                        <div class="metric-label">Phase L1</div>
                        <div class="metric-value"><span id="power-l1">0</span><span class="metric-unit">W</span></div>
                    </div>
                    <div class="metric phase2">
                        <div class="metric-label">Phase L2</div>
                        <div class="metric-value"><span id="power-l2">0</span><span class="metric-unit">W</span></div>
                    </div>
                    <div class="metric phase3">
                        <div class="metric-label">Phase L3</div>
                        <div class="metric-value"><span id="power-l3">0</span><span class="metric-unit">W</span></div>
                    </div>
                    <div class="metric total">
                        <div class="metric-label">Total Power</div>
                        <div class="metric-value"><span id="power-total">0</span><span class="metric-unit">W</span></div>
                    </div>
                </div>

                <div class="info-row">
                    <span>⚡ Energy: <strong id="energy">0</strong> Wh</span>
                    <div class="last-update">Last update: <span id="wallbox-last-update">Never</span></div>
                </div>
            </div>

            <div class="card">
                <h2>
                    <span class="indicator" id="evcc-indicator"></span>
                    EVCC Commands
                </h2>

                <div class="metrics" style="margin-top: 30px;">
                    <div class="metric evcc">
                        <div class="metric-label">Charging Limit</div>
                        <div class="metric-value"><span id="charging-limit">0</span><span class="metric-unit" id="charging-unit">W</span></div>
                    </div>
                    <div class="metric evcc">
                        <div class="metric-label">Last Command</div>
                        <div class="metric-value" style="font-size: 18px; color: #c586c0;" id="last-command">-</div>
                    </div>
                </div>

                <div class="info-row">
                    <div class="last-update">Last update: <span id="evcc-last-update">Never</span></div>
                </div>

                <div style="margin-top: 40px; padding: 20px; background: #2d2d30; border-radius: 6px;">
                    <h3 style="color: #fff; font-size: 16px; margin-bottom: 15px;">📈 Charging Statistics</h3>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px; font-size: 14px;">
                        <div>
                            <span style="color: #808080;">Efficiency:</span><br>
                            <strong style="color: #fff; font-size: 18px;" id="efficiency">-</strong>
                        </div>
                        <div>
                            <span style="color: #808080;">Avg Voltage:</span><br>
                            <strong style="color: #fff; font-size: 18px;" id="avg-voltage">0 V</strong>
                        </div>
                        <div>
                            <span style="color: #808080;">Avg Current:</span><br>
                            <strong style="color: #fff; font-size: 18px;" id="avg-current">0 A</strong>
                        </div>
                        <div>
                            <span style="color: #808080;">Power Factor:</span><br>
                            <strong style="color: #fff; font-size: 18px;" id="power-factor">-</strong>
                        </div>
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>⚙️ Configuration Parameters</h2>
                <div style="font-size: 12px; color: #606060; margin-bottom: 15px;">
                    Passively monitored from OCPP messages
                </div>
                <div id="config-params" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; color: #808080; font-size: 13px;">
                    <div style="grid-column: 1 / -1; text-align: center; padding: 30px; color: #606060;">Waiting for configuration messages...</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        function formatTime(isoString) {
            if (!isoString) return 'Never';
            const date = new Date(isoString);
            return date.toLocaleTimeString() + '.' + date.getMilliseconds().toString().padStart(3, '0');
        }

        async function updateStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();

                // Wallbox data
                const wb = data.wallbox;
                document.getElementById('voltage-l1').textContent = wb.voltage.L1.toFixed(1);
                document.getElementById('voltage-l2').textContent = wb.voltage.L2.toFixed(1);
                document.getElementById('voltage-l3').textContent = wb.voltage.L3.toFixed(1);

                document.getElementById('current-l1').textContent = wb.current.L1.toFixed(2);
                document.getElementById('current-l2').textContent = wb.current.L2.toFixed(2);
                document.getElementById('current-l3').textContent = wb.current.L3.toFixed(2);

                document.getElementById('power-l1').textContent = Math.round(wb.power.L1);
                document.getElementById('power-l2').textContent = Math.round(wb.power.L2);
                document.getElementById('power-l3').textContent = Math.round(wb.power.L3);
                document.getElementById('power-total').textContent = Math.round(wb.power.total);

                document.getElementById('energy').textContent = Math.round(wb.energy);

                // Status badge
                const statusBadge = document.getElementById('wallbox-status-badge');
                statusBadge.textContent = wb.status;
                statusBadge.className = 'status-badge ' + wb.status.toLowerCase();

                // Configuration parameters
                const configContainer = document.getElementById('config-params');
                if (wb.configuration && Object.keys(wb.configuration).length > 0) {
                    let configHtml = '';
                    // Highlight important configuration keys
                    const importantKeys = [
                        'LocalPreAuthorize', 'LocalAuthorizeOffline', 'LocalAuthListEnabled',
                        'AuthorizeRemoteTxRequests', 'StopTransactionOnEVSideDisconnect',
                        'HeartbeatInterval', 'MeterValueSampleInterval'
                    ];

                    // Show important keys first
                    importantKeys.forEach(key => {
                        if (wb.configuration[key] !== undefined) {
                            const value = wb.configuration[key];
                            const valueColor = value === 'true' ? '#4ec9b0' : (value === 'false' ? '#f48771' : '#d4d4d4');
                            configHtml += `<div><span style="color: #569cd6; font-weight: 600;">${key}:</span> <span style="color: ${valueColor};">${value}</span></div>`;
                        }
                    });

                    // Show other keys
                    Object.keys(wb.configuration).sort().forEach(key => {
                        if (!importantKeys.includes(key)) {
                            const value = wb.configuration[key];
                            configHtml += `<div><span style="color: #808080;">${key}:</span> ${value}</div>`;
                        }
                    });

                    configContainer.innerHTML = configHtml || '<div style="grid-column: 1 / -1; text-align: center; padding: 20px; color: #606060;">No configuration data</div>';
                } else {
                    configContainer.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; padding: 20px; color: #606060;">Waiting for configuration messages...</div>';
                }

                // Wallbox indicator
                const wbIndicator = document.getElementById('wallbox-indicator');
                if (wb.status === 'Charging') {
                    wbIndicator.className = 'indicator charging';
                } else if (wb.power.total > 0 || wb.status === 'Available') {
                    wbIndicator.className = 'indicator active';
                } else {
                    wbIndicator.className = 'indicator inactive';
                }

                // Transaction info
                const txInfo = document.getElementById('transaction-info');
                if (wb.transaction_id) {
                    txInfo.innerHTML = `Transaction: ${wb.transaction_id} <button onclick="stopTransaction()" style="background: #ce9178; color: #1e1e1e; border: none; padding: 4px 8px; cursor: pointer; border-radius: 3px; font-size: 11px; font-weight: 600; margin-left: 8px;">⏹️ Stop</button>`;
                } else {
                    txInfo.textContent = '';
                }

                // EV Connection Status
                const evIcon = document.getElementById('ev-icon');
                const evStatusText = document.getElementById('ev-status-text');
                const evStatusBadge = document.getElementById('ev-status-badge');

                const isConnected = wb.status !== 'Available' && wb.status !== 'Unknown';

                if (isConnected) {
                    evIcon.textContent = '🔌';
                    evStatusBadge.textContent = 'Connected';
                    evStatusBadge.className = 'status-badge connected';

                    if (wb.status === 'Charging') {
                        evStatusText.textContent = 'Vehicle connected and charging';
                    } else if (wb.status === 'Preparing') {
                        evStatusText.textContent = 'Vehicle connected, preparing to charge';
                    } else if (wb.status === 'Finishing') {
                        evStatusText.textContent = 'Vehicle connected, finishing charge';
                    } else {
                        evStatusText.textContent = 'Vehicle connected';
                    }
                } else {
                    evIcon.textContent = '🚗';
                    evStatusText.textContent = 'No vehicle connected';
                    evStatusBadge.textContent = 'Not Connected';
                    evStatusBadge.className = 'status-badge not-connected';
                }

                document.getElementById('wallbox-last-update').textContent = formatTime(wb.last_update);

                // EVCC data
                const evcc = data.evcc;
                document.getElementById('charging-limit').textContent = Math.round(evcc.charging_limit);
                document.getElementById('charging-unit').textContent = evcc.charging_unit;
                document.getElementById('last-command').textContent = evcc.last_command || '-';
                document.getElementById('evcc-last-update').textContent = formatTime(evcc.last_update);

                // EVCC indicator
                const evccIndicator = document.getElementById('evcc-indicator');
                if (evcc.charging_limit > 0) {
                    evccIndicator.className = 'indicator active';
                } else {
                    evccIndicator.className = 'indicator inactive';
                }

                // Calculate statistics
                const avgVoltage = (wb.voltage.L1 + wb.voltage.L2 + wb.voltage.L3) / 3;
                const avgCurrent = (wb.current.L1 + wb.current.L2 + wb.current.L3) / 3;
                document.getElementById('avg-voltage').textContent = avgVoltage.toFixed(1) + ' V';
                document.getElementById('avg-current').textContent = avgCurrent.toFixed(2) + ' A';

                // Efficiency (actual power vs limit)
                if (evcc.charging_limit > 0 && wb.power.total > 0) {
                    const efficiency = (wb.power.total / evcc.charging_limit * 100).toFixed(1);
                    document.getElementById('efficiency').textContent = efficiency + '%';
                } else {
                    document.getElementById('efficiency').textContent = '-';
                }

                // Power factor (simplified estimate)
                if (avgVoltage > 0 && avgCurrent > 0) {
                    const apparentPower = avgVoltage * avgCurrent * 3;
                    if (apparentPower > 0) {
                        const pf = (wb.power.total / apparentPower).toFixed(2);
                        document.getElementById('power-factor').textContent = pf;
                    } else {
                        document.getElementById('power-factor').textContent = '-';
                    }
                } else {
                    document.getElementById('power-factor').textContent = '-';
                }

            } catch (error) {
                console.error('Error fetching status:', error);
            }
        }

        // Initial load
        updateStatus();

        // Update every 1 second for real-time feel
        setInterval(updateStatus, 1000);

        async function rebootWallbox() {
            if (!confirm('Are you sure you want to reboot the wallbox? This will interrupt any active charging session.')) {
                return;
            }

            try {
                const response = await fetch('/api/reboot', { method: 'POST' });
                const data = await response.json();

                if (data.status === 'success') {
                    alert('✅ Reset command sent to wallbox successfully!\\n\\nThe wallbox will perform a hard reset and reboot. Wait for BootNotification to confirm the reboot.');
                } else {
                    alert('❌ Error: ' + data.message);
                }
            } catch (error) {
                alert('❌ Failed to send reset command: ' + error);
                console.error('Error sending reset command:', error);
            }
        }

        async function stopTransaction() {
            if (!confirm('Stop the stuck transaction? This will force the transaction to close.')) {
                return;
            }

            try {
                const response = await fetch('/api/stop-transaction', { method: 'POST' });
                const data = await response.json();

                if (data.status === 'success') {
                    alert('✅ ' + data.message);
                } else {
                    alert('❌ Error: ' + data.message);
                }
            } catch (error) {
                alert('❌ Failed to stop transaction: ' + error);
                console.error('Error stopping transaction:', error);
            }
        }

        async function getConfiguration() {
            if (!confirm('Send GetConfiguration command to wallbox? This will query all configuration parameters.')) {
                return;
            }

            try {
                const response = await fetch('/api/get-configuration', { method: 'POST' });
                const data = await response.json();

                if (data.status === 'success') {
                    alert('✅ GetConfiguration command sent!\\n\\nThe wallbox will respond with all configuration parameters. Check the Configuration Parameters section below.');
                } else {
                    alert('❌ Error: ' + data.message);
                }
            } catch (error) {
                alert('❌ Failed to send GetConfiguration: ' + error);
                console.error('Error sending GetConfiguration:', error);
            }
        }
    </script>
</body>
</html>
        """
        return web.Response(text=html, content_type='text/html')

    async def start_web_server(self):
        """Start the HTTP web interface server"""
        app = web.Application()
        app.router.add_get('/', self.handle_web_index)
        app.router.add_get('/messages', self.handle_messages_api)
        app.router.add_get('/wallboxes', self.handle_wallboxes_api)
        app.router.add_post('/clear', self.handle_clear_api)
        app.router.add_get('/status', self.handle_status_page)
        app.router.add_get('/api/status', self.handle_status_api)
        app.router.add_post('/api/reboot', self.handle_reboot_api)
        app.router.add_post('/api/stop-transaction', self.handle_stop_transaction_api)
        app.router.add_post('/api/get-configuration', self.handle_get_configuration_api)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.listen_host, self.web_port)
        await site.start()

        logger.info(f"Web interface started on http://{self.listen_host}:{self.web_port}")
        return runner

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
    parser.add_argument("--web-port", type=int, default=8889, help="Web interface port (default: 8889)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    proxy = WebSocketProxy(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        target_host=args.target_host,
        target_port=args.target_port,
        web_port=args.web_port
    )

    async def run_proxy():
        # Start both WebSocket proxy and web interface
        ws_server = await proxy.start_server()
        web_runner = await proxy.start_web_server()

        # Start background task loop
        asyncio.create_task(proxy._background_tasks())
        logger.info("Started background tasks")

        # Don't send startup boot notifications - let wallboxes connect naturally
        # asyncio.create_task(proxy._send_startup_boot_notifications())

        try:
            # Keep running until interrupted
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("Shutting down servers...")
            ws_server.close()
            await ws_server.wait_closed()
            await web_runner.cleanup()

    try:
        asyncio.run(run_proxy())
    except KeyboardInterrupt:
        logger.info("Proxy server stopped")

if __name__ == "__main__":
    main()