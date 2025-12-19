# CORRECTED: Critical Wallbox Compliance Issues Found

## ⚠️ **MAJOR SAFETY CONCERNS IDENTIFIED**

**You were absolutely right to question my analysis.** The data reveals **serious OCPP compliance failures** by the wallbox.

## 🚨 **Critical Issue #1: Wallbox Ignores 11A Increase Command**

### Command vs Reality:

**Exact evcc SetChargingProfile Command (15:17:01):**
```json
[2,"1278809386","SetChargingProfile",{
  "connectorId":1,
  "csChargingProfiles":{
    "chargingProfileId":0,
    "stackLevel":0,
    "chargingProfilePurpose":"TxDefaultProfile",
    "chargingProfileKind":"Absolute",
    "chargingSchedule":{
      "startSchedule":"2025-10-01T13:16:01Z",
      "chargingRateUnit":"A",
      "chargingSchedulePeriod":[{"startPeriod":0,"limit":11}]
    }
  }
}]
```

**Actual Meter Readings After Command:**
```
15:17:02: Current: 6.53A, 6.30A, 6.43A  (NOT 11A!)
15:17:11: Current: 6.57A, 6.33A, 6.48A  (NOT 11A!)
15:17:22: Current: 6.50A, 6.26A, 6.43A  (NOT 11A!)
15:17:32: Current: 6.52A, 6.28A, 6.43A  (NOT 11A!)
15:17:41: Current: 6.61A, 6.39A, 6.51A  (NOT 11A!)
```

**❌ COMPLIANCE FAILURE**: Wallbox accepted command but **did not execute** - remained at ~6.5A instead of 11A.

## 🚨 **Critical Issue #2: Wallbox Ignores 0A Stop Command**

### Even More Serious Safety Issue:

**Exact evcc SetChargingProfile STOP Command (15:19:09):**
```json
[2,"2289839452","SetChargingProfile",{
  "connectorId":1,
  "csChargingProfiles":{
    "chargingProfileId":0,
    "stackLevel":0,
    "chargingProfilePurpose":"TxDefaultProfile",
    "chargingProfileKind":"Absolute",
    "chargingSchedule":{
      "startSchedule":"2025-10-01T13:18:09Z",
      "chargingRateUnit":"A",
      "chargingSchedulePeriod":[{"startPeriod":0,"limit":0}]
    }
  }
}]
```

**evcc Response (15:19:11):**
```
charge power: 0W  // evcc THINKS charging stopped
```

**ACTUAL Meter Readings After STOP Command:**
```
15:19:12: Current: 6.53A, 6.28A, 6.43A + Power: 1460W, 1410W, 1410W (STILL CHARGING!)
15:19:22: Current: 6.52A, 6.30A, 6.45A + Power: 1460W, 1410W, 1410W (STILL CHARGING!)
15:19:32: Current: 6.55A, 6.30A, 6.46A + Power: 1460W, 1410W, 1410W (STILL CHARGING!)
15:19:42: Current: 6.54A, 6.31A, 6.46A + Power: 1460W, 1410W, 1410W (STILL CHARGING!)
15:19:52: Current: 6.52A, 6.30A, 6.48A + Power: 1460W, 1410W, 1410W (STILL CHARGING!)
```

**❌ MAJOR SAFETY FAILURE**: Wallbox continued charging for 43+ seconds after receiving STOP command while evcc believed charging had stopped.

## 📊 **What Actually Worked**

### 6A Commands (15:12:42):
**Exact evcc SetChargingProfile Command:**
```json
[2,"985694111","SetChargingProfile",{
  "connectorId":1,
  "csChargingProfiles":{
    "chargingProfileId":0,
    "stackLevel":0,
    "chargingProfilePurpose":"TxDefaultProfile",
    "chargingProfileKind":"Absolute",
    "chargingSchedule":{
      "startSchedule":"2025-10-01T13:11:42Z",
      "chargingRateUnit":"A",
      "chargingSchedulePeriod":[{"startPeriod":0,"limit":6}]
    }
  }
}]
```

**Meter Response:**
```
~6.5A actual delivery (close to commanded 6A)
```

✅ **This worked** - wallbox delivered close to commanded current.

## 🔍 **Root Cause Analysis**

### Pattern of Failures:
1. **6A commands**: ✅ Work (~6.5A delivered)
2. **7A commands**: ❌ Failed (ignored, stayed at 6.5A)
3. **11A commands**: ❌ Failed (ignored, stayed at 6.5A)
4. **0A commands**: ❌ Failed (ignored stop command)

## 🚨 **Critical Issues**
1. **Uncontrolled Charging**: evcc reports 0W but wallbox continues at 4.3kW
2. **Command Ignored**: Higher current commands (7A, 11A) not executed
3. **Stop Command Failed**: 0A command ignored for 43+ seconds

## 📋 **Complete Command Timeline with Exact JSON**

### Chronological Command Sequence:

**1. First 6A Command (15:12:42):**
```json
[2,"985694111","SetChargingProfile",{"connectorId":1,"csChargingProfiles":{"chargingProfileId":0,"stackLevel":0,"chargingProfilePurpose":"TxDefaultProfile","chargingProfileKind":"Absolute","chargingSchedule":{"startSchedule":"2025-10-01T13:11:42Z","chargingRateUnit":"A","chargingSchedulePeriod":[{"startPeriod":0,"limit":6}]}}}]
```

**MeterValues Response (15:13:01):**
```json
"Current.Import": "6.52 A" (L1), "6.29 A" (L2), "6.44 A" (L3)
"Power.Active.Import": "1470 W" (L1), "1410 W" (L2), "1410 W" (L3)
```
*Result: ✅ Worked (~6.5A delivered)*

**2. Repeated 6A Commands (15:12:44, 15:12:51, 15:13:01, 15:13:31):**
```json
[2,"2830474359","SetChargingProfile",{"connectorId":1,"csChargingProfiles":{"chargingProfileId":0,"stackLevel":0,"chargingProfilePurpose":"TxDefaultProfile","chargingProfileKind":"Absolute","chargingSchedule":{"startSchedule":"2025-10-01T13:11:44Z","chargingRateUnit":"A","chargingSchedulePeriod":[{"startPeriod":0,"limit":6}]}}}]
```
*Result: ✅ Maintained 6A charging*

**3. 7A Increase Command (15:15:28):**
```json
[2,"834255345","SetChargingProfile",{"connectorId":1,"csChargingProfiles":{"chargingProfileId":0,"stackLevel":0,"chargingProfilePurpose":"TxDefaultProfile","chargingProfileKind":"Absolute","chargingSchedule":{"startSchedule":"2025-10-01T13:14:28Z","chargingRateUnit":"A","chargingSchedulePeriod":[{"startPeriod":0,"limit":7}]}}}]
```

**MeterValues Response (15:15:31):**
```json
"Current.Import": "6.59 A" (L1), "6.36 A" (L2), "6.53 A" (L3)
"Power.Active.Import": "1480 W" (L1), "1430 W" (L2), "1430 W" (L3)
```
*Result: ❌ FAILED (ignored, stayed at ~6.5A, no increase from 7A command)*

**4. 11A Increase Command (15:17:01):**
```json
[2,"1278809386","SetChargingProfile",{"connectorId":1,"csChargingProfiles":{"chargingProfileId":0,"stackLevel":0,"chargingProfilePurpose":"TxDefaultProfile","chargingProfileKind":"Absolute","chargingSchedule":{"startSchedule":"2025-10-01T13:16:01Z","chargingRateUnit":"A","chargingSchedulePeriod":[{"startPeriod":0,"limit":11}]}}}]
```

**MeterValues Response (15:17:11):**
```json
"Current.Import": "6.57 A" (L1), "6.33 A" (L2), "6.48 A" (L3)
"Power.Active.Import": "1460 W" (L1), "1410 W" (L2), "1410 W" (L3)
```
*Result: ❌ FAILED (ignored 11A command, stayed at ~6.5A)*

**5. STOP Command (15:19:09):**
```json
[2,"2289839452","SetChargingProfile",{"connectorId":1,"csChargingProfiles":{"chargingProfileId":0,"stackLevel":0,"chargingProfilePurpose":"TxDefaultProfile","chargingProfileKind":"Absolute","chargingSchedule":{"startSchedule":"2025-10-01T13:18:09Z","chargingRateUnit":"A","chargingSchedulePeriod":[{"startPeriod":0,"limit":0}]}}}]
```

**MeterValues Response (15:19:22):**
```json
"Current.Import": "6.52 A" (L1), "6.30 A" (L2), "6.45 A" (L3)
"Power.Active.Import": "1460 W" (L1), "1410 W" (L2), "1410 W" (L3)
```
*Result: ❌ CRITICAL FAILURE (ignored 0A STOP command, continued charging at 6.5A)*

## 📋 **Evidence Summary**

### Commands That Failed:
- **7A command (15:15:28)**: Ignored, stayed at 6.5A
- **11A command (15:17:01)**: Ignored, stayed at 6.5A
- **0A stop command (15:19:09)**: Ignored, continued charging 43+ seconds

### Commands That Worked:
- **6A commands**: Delivered ~6.5A

## 📋 **Facts Summary**

**Commands that work:** 6A
**Commands that fail:** 7A, 11A, 0A (stop)
**Critical issue:** Wallbox ignores stop commands while evcc reports charging stopped