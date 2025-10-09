# AcTEC EV-AC22K Wallbox - OCPP 1.6 Compliance Issues Report

# Dynamic Behavior

**Device Information:**
- Model: EV-AC22K
- Vendor: AcTEC
- Firmware Version: V1.17.9
- Serial Number: Actec
- Protocol: OCPP 1.6

**Report Date:** October 9, 2025
**Testing Environment:** Production deployment with EVCC backend
**Proxy Version:** Wallbox EVCC Proxy with comprehensive message logging and corrective actions

---

## Executive Summary

This report documents multiple OCPP 1.6 compliance violations and firmware bugs discovered in the AcTEC EV-AC22K wallbox during integration with EVCC charging management system. The issues range from protocol violations to incorrect metering data, false transaction stop reasons, and discrepancies between wallbox display and OCPP reporting.

**Severity Levels:**
- 🔴 **Critical**: Protocol violations requiring proxy workarounds
- 🟡 **High**: Incorrect data reporting affecting system integration
- 🟠 **Medium**: Behavioral issues causing transaction failures
- 🔵 **Low**: Minor protocol deviations

---

## 1. Malformed WebSocket URL 🔴 Critical

### Issue
Wallbox initiates WebSocket connection with double slash in URL path.

### Evidence
```
ws://<proxy_host>:<proxy_port>//<charge_point_id>
```

### Standard URL Format
```
ws://<proxy_host>:<proxy_port>/<charge_point_id>
```

### Impact
- Strict WebSocket servers reject malformed URLs
- Complete communication failure with compliant systems

### Proxy Workaround
Proxy cleans malformed URL paths before forwarding.

**Code Reference:** `ocpp_proxy.py` (`clean_url_path` method)

---

## 2. Invalid Timestamp Format 🔴 Critical

### Issue
Wallbox reports invalid null timestamps in OCPP messages.

### Evidence
```json
"timestamp": "0000-00-00T00:00:00.000Z"
```

### OCPP 1.6 Specification
ISO 8601 format timestamps are required. The value `0000-00-00` is not a valid ISO 8601 date.

### Impact
- Backend systems cannot parse invalid timestamps
- Transaction records become unreliable
- Compliance violation with OCPP 1.6 timestamp requirements

### Proxy Workaround
Proxy detects and replaces invalid timestamps with current UTC time.

**Code Reference:** `ocpp_proxy.py:409-421` (`fix_timestamp` method)

---

## 3. IdTag Length Violation 🔴 Critical

### Issue
Wallbox accepts and uses IdTag values exceeding OCPP 1.6 maximum length.

### OCPP 1.6 Specification
**Section 3.1.1:** IdToken/IdTag maximum length is **20 characters**.

### Observed Behavior
Wallbox uses longer IdTag values in transaction messages without truncation or validation.

### Impact
- Backend systems with strict validation reject messages
- Transaction start/stop failures
- Authorization mismatches

### Proxy Workaround
Proxy truncates IdTag fields to 20 characters or converts to hash format.

**Code Reference:** `ocpp_proxy.py:560-571` (`_fix_idtag_length` method)

---

## 4. Power Metering Error - 10x Underreporting 🟡 High

### Issue
Wallbox reports active power values **10 times lower** than actual consumption.

### Evidence
- Vehicle drawing ~6A at 230V (expected: ~4140W)
- Wallbox reports: ~410W in MeterValues
- Actual measured consumption: ~4100W

### OCPP 1.6 Specification
MeterValues must accurately report electrical measurements in specified units.

### Impact
- Incorrect energy billing calculations
- Load management systems receive false data
- Power monitoring dashboards show 10% of actual consumption

### Proxy Workaround
Proxy multiplies all `Power.Active.Import` values by 10 in MeterValues messages.

**Code Reference:** `ocpp_proxy.py:573-599` (`_multiply_watts_by_10` method)

---

## 5. Voltage Reporting Inconsistency - Display vs OCPP 🟡 High

### Issue
Wallbox displays correct voltage on physical screen but reports **0.0V** in OCPP MeterValues during active transactions.

### Evidence

**Transaction State Analysis:**

| Transaction State | Wallbox Display | OCPP MeterValues |
|------------------|-----------------|------------------|
| `transactionId: 0` (idle, after boot) | ~230V | ✅ 231.1V, 231.3V, 233.8V |
| `transactionId: >0` (Error: 00) | ~230V | ❌ 0.0V, 0.0V, 0.0V |

**Example - Transaction ID 1759926730 (active transaction):**
```json
// Wallbox physical display: Shows ~230V on all phases
// OCPP MeterValues at 22:02:56:
{
  "value": "0.0", "measurand": "Voltage", "phase": "L1", "unit": "V"
},
{
  "value": "0.0", "measurand": "Voltage", "phase": "L2", "unit": "V"
},
{
  "value": "0.0", "measurand": "Voltage", "phase": "L3", "unit": "V"
}
```

**Example - Idle state at 22:01:22 (before transaction start):**
```json
// Same wallbox, moments earlier with transactionId: 0:
{
  "value": "231.1", "measurand": "Voltage", "phase": "L1", "unit": "V"
},
{
  "value": "231.3", "measurand": "Voltage", "phase": "L2", "unit": "V"
},
{
  "value": "233.8", "measurand": "Voltage", "phase": "L3", "unit": "V"
}
```

### OCPP 1.6 Specification
MeterValues should accurately report electrical measurements. Voltage readings must reflect actual measured values.

### Pattern Analysis
The wallbox firmware **stops reporting voltage in OCPP** once a transaction becomes active, despite:
- Voltage continues to be measured (wallbox display shows correct values)
- Voltage is critical safety and monitoring data
- OCPP requires accurate metering during transactions

---

## 6. False Stop and false Reason Reporting - Display vs OCPP 🔴 Critical

### Issue
Wallbox **displays "error:00" on screen** when stopping transactions, but reports `"reason": "Remote"` in OCPP StopTransaction messages. Error: 00 is not documented in Installation Manual

### Evidence

**Multiple transactions showing this pattern:**

```
Transaction 1759926730 (22:01:30 - RFID):
  - StartTransaction at 22:01:30
  - No RemoteStopTransaction command sent by EVCC
  - No current flowing (0.00A all phases)
  - Transaction stops after 1.8 seconds
  - Wallbox display: Shows "error:00"
  - OCPP message: "reason": "Remote"
```

**OCPP StopTransaction message:**
```json
[2, "127", "StopTransaction", {
  "idTag": "50600020100021",
  "meterStop": 0,
  "timestamp": "2025-10-09T20:01:23.000Z",
  "transactionId": 1759926730,
  "reason": "Remote"  // FALSE - no remote command was sent
}]
```

**What actually happened:**
1. No RemoteStopTransaction command in logs
2. No current flowing (timeout due to no vehicle charging)
3. Wallbox **displays "error:00"** acknowledging error condition
4. Wallbox **reports "Remote"** in OCPP (completely false)
5. EV all the time connected

### OCPP 1.6 Specification
**Section 4.11 - StopTransaction:**
Stop reasons should accurately reflect the actual cause:
- `Remote`: Stopped by RemoteStopTransaction command
- `EVDisconnected`: Vehicle disconnected
- `Other`: Timeout or other local conditions
- `PowerLoss`: Power failure
- `EmergencyStop`: Emergency button pressed

### Critical Finding
The wallbox firmware **knows** it's an error condition:
- **Evidence**: Displays "error:00" on physical screen
- **But reports**: `"reason": "Remote"` in OCPP protocol
- **Reality**: No remote stop command was ever sent

This proves the wallbox has error detection logic but fails to correctly map error codes to OCPP stop reasons.

### Impact
- Misleading transaction records ("Remote" when it was actually a timeout/error)
- Impossible to diagnose charging failures (all errors reported as "Remote")
- User confusion about why charging stopped
- Backend analytics show false "remote stop" statistics
- Troubleshooting requires physical access to wallbox display to see real error codes

---

## 7. GetCompositeSchedule Behavior During vs After Transaction 🟠 Medium

### Issue
Wallbox stops transactions when receiving certain commands immediately after StartTransaction.

### Evidence
**Pattern observed in multiple transactions:**
- Transaction starts (StartTransaction sent)
- Backend sends GetCompositeSchedule or TriggerMessage within 2-5 seconds
- Same transaction works if delayed for a few seconds after StartTransaction

### Expected Behavior
OCPP commands should be processable at any time during an active transaction without causing transaction termination.

---

**Report Generated:** October 9, 2025
**Proxy Version:** Wallbox EVCC Proxy v1.0
**Document Version:** 2.0
