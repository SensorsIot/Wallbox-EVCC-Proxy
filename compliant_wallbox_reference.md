# Compliant Wallbox OCPP 1.6 Reference Behavior

**Test Date:** 2025-11-15 17:08:59 - 17:10:09 UTC
**Duration:** ~70 seconds
**Wallboxes Tested:**
- Actec (AcTec SmartCharger, V1.0.0)
- AE104ABG00029B (ELECQ AE104, EPRO001_V1.2.0(7-1761213148))

**EVCC Server:** 192.168.0.202:8887

---

## Connection Sequence (Compliant Behavior)

### 1. Initial Connection and Boot Sequence

Both wallboxes exhibited identical, fully compliant OCPP 1.6 behavior:

**Actec - Connection at 17:08:59.641:**
1. BootNotification sent immediately upon connection
2. StatusNotification sent (connector 1, Available, NoError)
3. EVCC sent ChangeAvailability (Operative) → Accepted
4. EVCC acknowledged StatusNotification and BootNotification (Accepted, interval: 60s)

**AE104ABG00029B - Connection at 17:08:59.908:**
1. BootNotification sent immediately upon connection
2. StatusNotification sent (connector 1, Available, NoError)
3. EVCC sent ChangeAvailability (Operative) → Accepted
4. EVCC acknowledged (Accepted, interval: 60s)

---

## Configuration Discovery Phase

EVCC performed identical configuration queries on both wallboxes:

### GetConfiguration Response
Both wallboxes returned 30 configuration keys:

**Core Configuration:**
- AuthorizeRemoteTxRequests: true
- ClockAlignedDataInterval: 900
- ConnectionTimeOut: 90
- ConnectorPhaseRotation: NotApplicable
- GetConfigurationMaxKeys: 44 (readonly)
- HeartbeatInterval: 60
- LocalAuthorizeOffline: false
- LocalPreAuthorize: false
- MeterValuesAlignedData: Energy.Active.Import.Register
- MeterValuesSampledData: Energy.Active.Import.Register,Current.Import,Power.Active.Import,SoC,Voltage
- MeterValueSampleInterval: 60
- NumberOfConnectors: 1 (readonly)
- ResetRetries: 1
- StopTransactionOnEVSideDisconnect: true
- StopTransactionOnInvalidId: true
- StopTxnAlignedData: Energy.Active.Import.Register
- StopTxnSampledData: Current.Import
- SupportedFeatureProfiles: Core,FirmwareManagement,LocalAuthListManagement,Reservation,SmartCharging,RemoteTrigger (readonly)
- TransactionMessageAttempts: 3
- TransactionMessageRetryInterval: 10
- UnlockConnectorOnEVSideDisconnect: true
- WebSocketPingInterval: 0

**Local Auth List:**
- LocalAuthListEnabled: true
- LocalAuthListMaxLength: 20 (readonly)
- SendLocalListMaxLength: 20 (readonly)

**Smart Charging:**
- ChargeProfileMaxStackLevel: 8 (readonly)
- ChargingScheduleAllowedChargingRateUnit: Current,Power (readonly)
- ChargingScheduleMaxPeriods: 5 (readonly)
- ConnectorSwitch3to1PhaseSupported: false (readonly)
- MaxChargingProfilesInstalled: 8 (readonly)

---

## TriggerMessage Acceptance (Key Compliance Test)

**Both wallboxes accepted and responded to TriggerMessage for BootNotification:**

**Actec (line 9-12):**
```
evcc->Actec: TriggerMessage(BootNotification)
Actec->evcc: BootNotification (chargePointModel: "AcTec SmartCharger", ...)
Actec->evcc: CallResult - status: Accepted
evcc->Actec: BootNotification accepted
```

**AE104ABG00029B (line 49-52):**
```
evcc->AE104ABG00029B: TriggerMessage(BootNotification)
AE104ABG00029B->evcc: BootNotification (chargePointModel: "AE104", ...)
AE104ABG00029B->evcc: CallResult - status: Accepted
evcc->AE104ABG00029B: BootNotification accepted
```

**Result:** ✅ Fully compliant - both wallboxes immediately responded to TriggerMessage requests

---

## Configuration Changes Applied by EVCC

Both wallboxes accepted all configuration changes (status: Accepted):

1. **MeterValuesSampledData** - Tested individual values:
   - Power.Active.Import
   - Energy.Active.Import.Register
   - Current.Import
   - Voltage
   - Current.Offered
   - Power.Offered
   - SoC

2. **Final combined value:**
   - MeterValuesSampledData: "Power.Active.Import,Energy.Active.Import.Register,Current.Import,Voltage,Current.Offered,Power.Offered,SoC"

3. **MeterValueSampleInterval:** Changed from 60s → 10s

4. **WebSocketPingInterval:** Changed from 0s → 30s

---

## Periodic Message Behavior

### MeterValues (10 second interval after configuration)

Both wallboxes sent MeterValues every 10 seconds with identical structure:

**3-Phase Voltage (230V per phase):**
- L1: 230.0V
- L2: 230.0V
- L3: 230.0V

**Current Import (idle state):**
- L1: 0.0A
- L2: 0.0A
- L3: 0.0A

**Power Active Import (idle state):**
- L1: 0W
- L2: 0W
- L3: 0W

**Energy Register:**
- Energy.Active.Import.Register: 0 Wh

### Heartbeat (60 second interval)

Both wallboxes sent Heartbeat exactly 60 seconds after BootNotification acceptance:

**Actec:** 17:09:59.654 (exactly 60.009s after initial boot response)
**AE104ABG00029B:** 17:09:59.917 (exactly 60.009s after initial boot response)

EVCC responded with current time synchronization.

---

## EVCC Monitoring Commands

EVCC sent periodic GetCompositeSchedule requests (~every 10-11 seconds):

**Example sequence:**
- 17:08:59.978 → Actec
- 17:08:59.979 → AE104ABG00029B
- 17:09:01.196 → Actec
- 17:09:11.381 → AE104ABG00029B
- 17:09:21.367 → Actec
- 17:09:31.392 → AE104ABG00029B
- etc.

Both wallboxes appear to have responded (responses not shown in filtered log).

---

## Message Timeline Summary

**Total messages logged:** 127 lines

**Breakdown by wallbox:**
- **Actec:**
  - 1 BootNotification (initial)
  - 1 BootNotification (triggered)
  - 1 StatusNotification
  - 1 Heartbeat
  - 11 MeterValues (2 triggered, 9 periodic)

- **AE104ABG00029B:**
  - 1 BootNotification (initial)
  - 1 BootNotification (triggered)
  - 1 StatusNotification
  - 1 Heartbeat
  - 10 MeterValues (2 triggered, 8 periodic)

**EVCC commands per wallbox:**
- 1 ChangeAvailability
- 1 GetConfiguration
- 1 TriggerMessage (BootNotification)
- 7 ChangeConfiguration (MeterValuesSampledData variations)
- 1 ChangeConfiguration (MeterValueSampleInterval)
- 1 ChangeConfiguration (WebSocketPingInterval)
- 2 TriggerMessage (MeterValues)
- ~5-6 GetCompositeSchedule requests

---

## Key Compliance Points Verified

✅ **Immediate BootNotification:** Both wallboxes sent BootNotification immediately upon connection
✅ **TriggerMessage Acceptance:** Both wallboxes accepted and responded to TriggerMessage for BootNotification
✅ **Configuration Compliance:** All ChangeConfiguration commands accepted
✅ **Periodic Heartbeat:** Sent exactly at configured 60s interval
✅ **Periodic MeterValues:** Sent at configured 10s interval
✅ **3-Phase Data:** Proper multi-phase voltage/current/power reporting
✅ **Message Format:** All OCPP 1.6 messages properly formatted

---

## Expected vs Non-Compliant Behavior

This reference establishes the baseline for **compliant OCPP 1.6 wallbox behavior**.

**Non-compliant behaviors to test against this baseline:**
1. ⚠️ Wallbox skips BootNotification on startup
2. ⚠️ Wallbox rejects TriggerMessage for BootNotification
3. ⚠️ Wallbox doesn't respond to configuration changes
4. ⚠️ Wallbox sends incorrect message formats
5. ⚠️ Wallbox doesn't maintain periodic message timing

**EVCC's Expected Response to Compliant Wallboxes:**
- Accept BootNotification immediately
- Request full configuration via GetConfiguration
- Optimize MeterValuesSampledData for required metrics
- Set appropriate sampling intervals
- Establish periodic monitoring via GetCompositeSchedule
- Maintain stable connection

---

## Test Environment

**Simulator Configuration:**
```bash
./wallbox_simulator.py --station-id Actec --model "AcTec SmartCharger" --vendor "AcTec" --serial "Actec" --firmware "V1.0.0"

./wallbox_simulator.py --station-id AE104ABG00029B --model "AE104" --vendor "ELECQ" --serial "AE104ABG00029B" --firmware "EPRO001_V1.2.0(7-1761213148)"
```

**Log File:** `/home/wallbox/wallbox_simulator_ocpp.log`

---

## Conclusion

Both simulated wallboxes demonstrated **full OCPP 1.6 compliance** during this reference test. The behavior establishes the expected baseline for:

1. Connection initialization
2. Configuration discovery and acceptance
3. TriggerMessage responsiveness
4. Periodic message timing
5. MeterValue data structure
6. Heartbeat timing

This reference should be compared against non-compliant scenarios to understand EVCC's tolerance and recovery mechanisms when wallboxes exhibit protocol violations.
