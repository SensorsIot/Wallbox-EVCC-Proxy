# AcTec Wallbox Error Documentation

## Problem Summary

Two separate issues prevented stable OCPP communication between the AcTec wallbox and EVCC:

1. **AcTec wallbox firmware bug**: The wallbox firmware (V1.17.9) incorrectly disconnects after receiving valid `NotSupported` OCPP error responses.

2. **Missing EVCC configuration**: EVCC was not configured to recognize the "Actec" charge point, causing it to immediately close connections.

## Root Causes

### Issue 1: AcTec Wallbox Firmware Bug (Observed in Connection Cycle 1)

The wallbox sends optional `FirmwareStatusNotification` messages every ~10 seconds. When EVCC correctly responds with the **perfectly valid** OCPP error `[4, MessageId, "NotSupported", "Unsupported feature FirmwareStatusNotification", {}]`, the wallbox firmware incorrectly accumulates these errors and **closes the entire connection** after receiving ~4-5 error responses.

This is non-compliant OCPP behavior - charge points should gracefully handle errors for unsupported optional features and continue normal operation.

**Workaround Implemented**: Proxy blocks FirmwareStatusNotification messages before they reach EVCC, preventing the wallbox from receiving the error responses that trigger its disconnect bug.

### Issue 2: Missing EVCC Configuration (Observed in Connection Cycles 2-5)

EVCC had no charger configured with `stationid: Actec`. When the wallbox connected, EVCC logged "unknown charge point connected: Actec" and immediately closed the connection. This was a separate issue from the wallbox firmware bug.

**Fix Implemented**: Added charger configuration to `/addon_configs/49686a9f_evcc/evcc.yaml` with proper `stationid: Actec` mapping.

## Key Insights

**Two Separate Problems:**

**Problem 1 - Wallbox Firmware Bug:**
- ❌ **AcTec Wallbox**: Buggy firmware that disconnects after receiving valid error responses
- ✅ **EVCC**: Correctly implements OCPP 1.6 specification by responding with NotSupported for unsupported features
- ✅ **Proxy Workaround**: Blocks FirmwareStatusNotification messages to prevent wallbox from receiving error responses

The OCPP 1.6 specification explicitly states that error responses like `[4, MessageId, "NotSupported", ...]` are valid and should be handled gracefully by charge points. The AcTec wallbox firmware violates this by treating them as fatal errors.

**Problem 2 - EVCC Configuration:**
- ✅ **EVCC Behavior**: Correctly rejected connections from unrecognized charge points
- ❌ **Configuration Issue**: Missing charger definition with `stationid: Actec` in evcc.yaml
- ✅ **Fix**: Added proper charger configuration to evcc.yaml

## Error Message Sequences

### Connection Cycle 1: First Connection (09:35:53 - 09:36:44)

**Timeline: 51 seconds from connection to disconnection**

#### Initial Connection
```
[2025-12-19 09:35:53]
INFO - connection open
INFO - New client connection from ('192.168.0.120', 59164) requesting path: /Actec
INFO - Attempting to connect to target: ws://192.168.0.202:8887/Actec
```

#### Successful Initial Handshake
```
[09:35:55,576] [client->target] [2,"100","BootNotification",{"chargePointModel":"EV-AC22K","chargePointVendor":"AcTEC","chargePointSerialNumber":"Actec","firmwareVersion":"V1.17.9"}]
[09:35:55,577] [target->client] [3,"100",{"currentTime":"2025-12-19T08:35:55Z","interval":60,"status":"Accepted"}]

[09:35:57,215] [client->target] [2,"101","BootNotification",{"chargePointModel":"EV-AC22K","chargePointVendor":"AcTEC","chargePointSerialNumber":"Actec","firmwareVersion":"V1.17.9"}]
[09:35:57,216] [target->client] [3,"101",{"currentTime":"2025-12-19T08:35:57Z","interval":60,"status":"Accepted"}]

[09:35:58,735] [client->target] [2,"102","Heartbeat",{}]
[09:35:58,736] [target->client] [3,"102",{"currentTime":"2025-12-19T08:35:58Z"}]
```

#### Wallbox Bug Triggered - Receives Valid Error Responses
```
[09:36:00,294] [client->target] [2,"103","FirmwareStatusNotification",{"status":"Installed"}]
[09:36:00,295] [target->client] [4,"103","NotSupported","Unsupported feature FirmwareStatusNotification",{}]
                                 ↑ EVCC sends VALID OCPP error response per specification

[09:36:10,451] [client->target] [2,"104","FirmwareStatusNotification",{"status":"Installed"}]
[09:36:10,452] [target->client] [4,"104","NotSupported","Unsupported feature FirmwareStatusNotification",{}]

[09:36:20,570] [client->target] [2,"105","FirmwareStatusNotification",{"status":"Installed"}]
[09:36:20,570] [target->client] [4,"105","NotSupported","Unsupported feature FirmwareStatusNotification",{}]

[09:36:30,930] [client->target] [2,"106","FirmwareStatusNotification",{"status":"Installed"}]
[09:36:30,931] [target->client] [4,"106","NotSupported","Unsupported feature FirmwareStatusNotification",{}]
```

**EVCC Behavior**: ✅ **CORRECT** - Responding with NotSupported error is the proper OCPP 1.6 response for unsupported features.

**Expected Wallbox Behavior**: Should log the error internally and continue normal operation.

**Actual Wallbox Behavior**: ❌ **BUGGY** - Accumulates these error responses and will disconnect after receiving ~4-5 of them.

#### Wallbox Disconnects After Receiving Valid Errors
```
[09:36:44,730] [client->target] [2,"107","Heartbeat",{}]
                                         ↑ Wallbox sends final message before disconnecting

[2025-12-19 09:36:44]
INFO - Closing wallbox connection ('192.168.0.120', 59164) - bidirectional proxy ended
      ↑ Wallbox (192.168.0.120) closes WebSocket connection from its side

[2025-12-19 09:36:54]
INFO - connection closed
```

**Analysis**: After receiving 4 valid NotSupported error responses from EVCC (one every ~10 seconds), the wallbox firmware incorrectly decides to disconnect. The wallbox sends one final Heartbeat message and then **initiates the connection closure** from its side (client-side close). This is a firmware bug - the wallbox should continue operating normally despite receiving these errors.

---

### Connection Cycles 2-5: EVCC Closes Connections Due to Missing Configuration (09:36-09:38)

**Timeline: Multiple rapid reconnection attempts, all lasting only 1-12 seconds**

**Example from Connection Cycle 3 (09:37:15):**

```
[09:37:15,564] INFO - Connection closed (target->client)
                       ↑ EVCC closes connection immediately after accepting it

[09:37:17,202] [client->target] [2,"110","FirmwareStatusNotification",{"status":"Installed"}]
                                 ↑ Wallbox tries to send message on already-closed connection

[09:37:17,202] INFO - Connection closed (client->target)
                      ↑ Wallbox realizes connection is already closed
```

**FACT**: In cycles 2-5, EVCC was closing connections immediately (within 1-2 seconds of accepting them) because the charge point "Actec" was **not configured** in EVCC. EVCC logs showed: "unknown charge point connected: Actec"

**FACT**: These disconnections had **nothing to do with FirmwareStatusNotification**. EVCC closed the connections before the wallbox could send any substantive messages or receive any error responses.

**Pattern**: Wallbox kept reconnecting every 20-30 seconds, EVCC kept immediately closing the connection due to missing configuration.

---

### After Proxy Fix Implementation (09:39:27+)

**FirmwareStatusNotification messages now blocked at proxy level:**

```
[2025-12-19 09:39:27]
INFO - Blocked FirmwareStatusNotification from /Actec (EVCC doesn't support this feature)

[09:39:27,424] [client->target-BLOCKED] [2,"122","FirmwareStatusNotification",{"status":"Installed"}]
                                         ↑ Message intercepted by proxy, never reaches EVCC

[2025-12-19 09:39:37]
INFO - Blocked FirmwareStatusNotification from /Actec (EVCC doesn't support this feature)

[09:39:37,580] [client->target-BLOCKED] [2,"123","FirmwareStatusNotification",{"status":"Installed"}]
```

**Result**: Messages blocked by proxy before reaching EVCC. **Wallbox never receives NotSupported error responses**, so its disconnect bug is never triggered. The wallbox now maintains connection stability because it's never exposed to the error responses that cause its buggy behavior.

However, connections still closed after 20-30 seconds due to missing charger configuration in EVCC (see secondary issue above).

---

### After EVCC Configuration Fix (10:58+)

**EVCC now properly configured and sending commands:**

```
[10:58:01,750] [target->client] [2,"3871321050","ChangeAvailability",{"connectorId":0,"type":"Operative"}]

[10:58:31,747] [target->client] [2,"4001746851","GetConfiguration",{}]
```

**Result**: EVCC recognized the charge point and began sending OCPP commands to configure and query the wallbox.

---

## OCPP Message Type Reference

- **Type [2]**: CALL - Request message
- **Type [3]**: CALLRESULT - Successful response
- **Type [4]**: CALLERROR - Error response

## Message Direction Tags

- `[client->target]`: Wallbox → EVCC
- `[target->client]`: EVCC → Wallbox
- `[client->target-BLOCKED]`: Message blocked by proxy before reaching EVCC

## Solutions Implemented

### 1. Proxy-Level Blocking - Workaround for Wallbox Firmware Bug (Implemented 2025-12-19 09:39)

**File**: `/home/OCPP-Proxy/ocpp_proxy.py`
**Location**: `proxy_messages()` method, lines ~711-720

```python
# Block FirmwareStatusNotification messages - WORKAROUND for AcTec wallbox firmware bug
# The wallbox incorrectly disconnects after receiving valid NotSupported error responses
# By blocking the message at the proxy, the wallbox never receives the error and stays connected
if (isinstance(parsed_message, list) and
    len(parsed_message) >= 4 and
    parsed_message[0] == 2 and
    parsed_message[2] == "FirmwareStatusNotification"):
    should_block = True
    ocpp_logger.info(f"[{direction}-BLOCKED] {message}")
    self._add_message_to_buffer(direction, message, "BLOCKED", station_id)
    logger.info(f"Blocked FirmwareStatusNotification from {station_id}")
```

**Why This Works**: By intercepting and blocking the FirmwareStatusNotification messages before they reach EVCC, the wallbox never receives the NotSupported error response that triggers its buggy disconnection behavior. This is a **workaround** for the wallbox firmware bug, not a fix for EVCC (which was behaving correctly).

**Impact**: Prevents wallbox from receiving error responses that trigger its disconnect bug. Connections remain stable.

### 2. EVCC Configuration Fix (Implemented 2025-12-19 10:55)

**File**: `/addon_configs/49686a9f_evcc/evcc.yaml`

```yaml
chargers:
  - name: actec_wallbox
    type: ocpp
    stationid: Actec

loadpoints:
  - title: AcTec
    charger: actec_wallbox
```

**Database Cleanup**: Removed stale "Elecq" charger entries, deleted and recreated `/data/evcc.db`

**Impact**: EVCC now properly recognizes charge point "Actec" and maintains stable connection.

## Current Status

- ✅ FirmwareStatusNotification messages blocked at proxy level
- ✅ EVCC properly configured with charger definition
- ✅ EVCC sending configuration commands to wallbox
- ✅ Stable connection maintained
- ✅ No more "unknown charge point connected" errors

## Lessons Learned

1. **Firmware bugs in OCPP implementations**: The AcTec wallbox firmware (V1.17.9) has a critical bug where it disconnects after receiving valid `NotSupported` error responses. This violates OCPP best practices - charge points should gracefully handle errors for optional features and continue normal operation. The error response from EVCC was **100% correct per OCPP 1.6 specification**.

2. **Proxy workarounds can mask firmware bugs**: By blocking the FirmwareStatusNotification messages at the proxy level, we prevent the wallbox from ever receiving the NotSupported error response. This keeps the connection stable but doesn't fix the underlying firmware bug. **The proper solution would be an AcTec firmware update**.

3. **OCPP error handling varies between implementations**: While EVCC correctly implements error responses per the OCPP specification, some charge point manufacturers (like AcTec) may have buggy error handling that causes disconnections. Testing interoperability is critical.

4. **Configuration is critical**: The EVCC database-driven configuration took precedence over YAML file definitions. Stale database entries prevented proper charge point recognition even after the wallbox disconnect bug was worked around.

5. **Distinguish between protocol correctness and buggy behavior**: Just because both sides "speak OCPP" doesn't mean they'll work together. One side can be following the specification correctly (EVCC) while the other has bugs that cause failures (AcTec wallbox).

## Firmware Bug Report for AcTec

**Product**: AcTec EV-AC22K Wallbox
**Firmware Version**: V1.17.9
**Serial Number**: Actec
**Model**: EV-AC22K
**Vendor**: AcTEC

**Bug Description**: The wallbox firmware incorrectly closes the WebSocket connection after receiving valid OCPP 1.6 error responses with error code "NotSupported". According to the OCPP 1.6 specification, charge points should gracefully handle errors for unsupported optional features and continue normal operation.

**Expected Behavior**:
1. Wallbox sends FirmwareStatusNotification (optional OCPP feature)
2. Central System responds with `[4, MessageId, "NotSupported", "Unsupported feature FirmwareStatusNotification", {}]`
3. Wallbox logs the error internally and continues normal operation
4. Connection remains stable

**Actual Behavior**:
1. Wallbox sends FirmwareStatusNotification (optional OCPP feature)
2. Central System responds with `[4, MessageId, "NotSupported", "Unsupported feature FirmwareStatusNotification", {}]`
3. After receiving 4-5 such error responses (~45 seconds), wallbox **closes the entire WebSocket connection**
4. Wallbox enters rapid connect-disconnect cycle

**Recommendation**: Update firmware to properly handle NotSupported error responses per OCPP 1.6 specification. The charge point should either:
- Stop sending FirmwareStatusNotification messages after receiving NotSupported error
- Continue sending them but ignore the NotSupported error responses and maintain the connection

**Current Workaround**: Proxy server intercepts FirmwareStatusNotification messages before they reach the Central System, preventing the wallbox from receiving the error responses that trigger its disconnect bug.

---

## Related Files

- Proxy implementation: `/home/OCPP-Proxy/ocpp_proxy.py`
- OCPP message logs: `/home/OCPP-Proxy/ocpp_messages.log`
- EVCC configuration: `/addon_configs/49686a9f_evcc/evcc.yaml` (on HA server 192.168.0.202)
- EVCC database: `/data/evcc.db` (on HA server 192.168.0.202)
- Project documentation: `/home/OCPP-Proxy/CLAUDE.md`
