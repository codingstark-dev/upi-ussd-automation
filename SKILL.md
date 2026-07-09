---
name: upi-ussd-automation
description: >
  Secure UPI and USSD payment automation skill for Android remote control with
  ADB/scrcpy, intent validation, human confirmation, and auditable execution.
tags:
  - upi
  - ussd
  - automation
  - adb
  - scrcpy
  - android
---

# Skill: UPI Payment Automation via USSD & Remote Device Control

## Overview

This skill enables an AI agent to perform UPI payments by remotely controlling
an Android device. It supports three execution strategies:

1. **UPI Deep Link (`upi://pay`)**: Official NPCI standard. Most reliable.
   Opens the UPI app chooser with payee and amount pre-filled.
2. **USSD (`*99#`)**: Universal, works without internet. Opens the interactive
   carrier menu for manual navigation.
3. **UPI App Automation**: Uses `uiautomator2` to drive specific apps.
   Fragile across app versions.

The agent parses natural language, validates the request against strict security
policies, and either executes or aborts based on configurable guardrails.

> **WARNING**: Automating financial transactions carries significant risk. This
> skill requires explicit human confirmation for every transaction and must
> never store UPI PINs or passwords.

---

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌─────────────┐
│   User      │────▶│  AI Agent    │────▶│  Command    │────▶│   scrcpy    │
│  Request    │     │  (Parser)    │     │  Validator  │     │   + ADB     │
└─────────────┘     └──────────────┘     └─────────────┘     └──────┬──────┘
                                                                     │
                                                            ┌────────▼────────┐
                                                            │  Android Device │
                                                            │  (USSD/UPI App) │
                                                            └─────────────────┘
```

**Key Design Principles:**
- **Fail-Closed**: Any ambiguity, disconnection, or validation failure aborts the transaction.
- **Human-in-the-Loop**: The agent halts at the UPI PIN / Biometric screen and alerts the user.
- **Audit Everything**: Screenshots, logs, and device state are recorded for every step.

---

## Prerequisites

### Hardware
- Android device with UPI-enabled SIM / banking app installed.
- Host machine (Linux / macOS / Windows) with USB or WiFi ADB access.
- USB cable or ADB over WiFi configured and trusted.

### Software
- `scrcpy` >= v2.0 (for screen mirroring + manual control)
- `adb` (Android Debug Bridge)
- Python 3.10+ with dependencies listed in `scripts/requirements.txt`

### Android Setup
1. Enable **Developer Options** → **USB Debugging**.
2. Enable **Stay awake** while charging.
3. Install your banking / UPI app and complete KYC.
4. Set the default SIM for USSD if using a dual-SIM device.
5. (Optional) Enable **Pointer location** in Dev Options for coordinate debugging.

---

## Installation

```bash
# 1. Clone or create the skill directory
# 2. Install Python dependencies
pip install -r scripts/requirements.txt

# 3. Initialize uiautomator2 on the Android device
python -m uiautomator2 init

# 4. Verify ADB connection
adb devices
```

---

## Command Schema

The agent MUST extract and validate these fields before execution:

| Field | Format | Example | Validation |
|-------|--------|---------|------------|
| `payee` | 10-digit mobile or UPI ID | `9876543210` or `user@upi` | Regex or UPI ID format |
| `amount` | Decimal (2 places) | `250.00` | Range: `1.00` - `100000.00` |
| `app` | Enum | `*99#`, `phonepe`, `gpay` | Whitelist only |
| `remarks` | String (optional) | `Lunch` | Max 50 chars, sanitized |

### Execution Format Templates

**UPI Deep Link (Recommended for UPI IDs):**
```
upi://pay?pa=PAYEE_VPA&pn=Name&am=AMOUNT&cu=INR&tr=TXN_ID&tn=Note
```

**USSD Menu (For mobile numbers without internet):**
```bash
# Use ACTION_DIAL (not CALL) and encode # as %23
adb shell am start -a android.intent.action.DIAL -d "tel:*99%23"
```

> **Note**: Pre-composed USSD strings like `*99*1*1*NUMBER*AMOUNT#` are
> treated as phone calls on modern Android. Always open the menu first,
> then navigate interactively.

---

## Execution Workflow

### Phase 1: Intent Parsing
Extract `payee`, `amount`, and `app` preference from natural language.

**Examples:**
- `"Pay 9876543210 rupees 250 for dinner"`
- `"Send 500 to Rahul via PhonePe"`

### Phase 2: Security Validation (CRITICAL)
Multi-layer validation before execution:
1. Amount within daily limit (configurable).
2. Payee in allowed contacts list (whitelist mode).
3. Time-based restrictions (e.g., no payments 11 PM - 6 AM).
4. Explicit user confirmation for amounts above a threshold.

### Phase 3: Device Preparation
```bash
# Connect to device
adb connect DEVICE_IP:5555  # or USB

# Start scrcpy for visual monitoring (optional but recommended)
scrcpy --serial $DEVICE_SERIAL \
       --window-borderless \
       --max-size 1024 \
       --stay-awake &
```

### Phase 4: Execution Strategies

#### Strategy A: UPI Deep Link (Most Reliable)
Uses the official NPCI `upi://pay` intent when the payee has a UPI ID.
Opens the system's UPI app chooser with details pre-filled.

```bash
adb shell am start -a android.intent.action.VIEW \
  -d "upi://pay?pa=user@upi&am=250.00&cu=INR"
```

#### Strategy B: USSD *99# Menu (Interactive)
Opens the carrier's USSD menu. The user navigates manually.

```bash
# CRITICAL: Use ACTION_DIAL + %23, not ACTION_CALL + #
adb shell am start -a android.intent.action.DIAL -d "tel:*99%23"
```

#### Strategy C: UPI App Automation (Heuristic)
Uses `uiautomator2` to drive a specific app. Fragile across versions.

```python
import uiautomator2 as u2
d = u2.connect(device_serial)
d.app_start("com.phonepe.app")
d(text="Send Money").click()
# ... navigate, enter details, stop before PIN
```

### Phase 5: Human Confirmation & Finalization
The automation **must stop** at the PIN / Biometric screen.
- Capture screenshot.
- Notify user: *"Transaction ready. Please enter PIN on device or confirm in scrcpy window."*
- Optionally, wait for post-transaction screen and capture UTR via OCR.

---

## Security Model (NON-NEGOTIABLE)

### 1. Authentication Layers
- **Device Lock**: Biometric / PIN on the device itself.
- **Agent Password**: A local secret required to invoke the skill.
- **Transaction Signing**: TOTP or hardware key for high-value amounts.

### 2. Execution Guardrails
```yaml
max_daily_limit: 10000
max_per_transaction: 5000
allowed_hours: "00:00-23:59"
require_confirmation_above: 1000
whitelist_only: false  # Enable true for production
```

### 3. Audit Trail
Every action MUST log:
- ISO 8601 Timestamp
- Parsed intent
- Validation results
- Encrypted screenshots
- Device ID and IP

### 4. Failure Modes
- **scrcpy disconnects mid-transaction** → ABORT
- **USSD dialog timeout** → ABORT
- **Amount mismatch detected** → ABORT
- **Unknown screen state** → ABORT and alert user
- **User does not confirm within timeout** → ABORT

---

## Python Implementation

See `scripts/upi_agent.py` for the full reference implementation.

### Quick Start
```python
from upi_agent import UPISkill

skill = UPISkill(
    device_serial="YOUR_SERIAL",
    config_path="config.json"
)

# Parse a request
intent = skill.parse("Pay 9876543210 250 rupees for lunch")

# Validate
if skill.validate(intent):
    # Execute (stops before PIN)
    result = skill.execute(intent)
    print(result)
```

---

## scrcpy Integration Commands

```bash
# Visual monitoring during automation
scrcpy \
  --serial $DEVICE_ID \
  --max-fps 15 \
  --window-width 400 \
  --window-height 800 \
  --record /tmp/scrcpy_session.mp4

# Raw ADB interaction (used by the Python script)
adb shell input tap x y
adb shell input text "string"
adb shell input keyevent 66   # ENTER
adb shell input keyevent 3    # HOME
```

---

## Risk Matrix

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Unauthorized payment | Medium | Critical | Whitelist + explicit confirmation |
| Network timeout mid-txn | High | Medium | Timeout handling + retry limit |
| Screen state mismatch | Medium | High | State machine + element checks |
| ADB over network exploit | Low | Critical | WiFi ADB only via VPN / localhost |
| SIM swap / device theft | Low | Critical | Device lock + remote wipe capability |

---

## Limitations & Disclaimers

1. **Banking Compliance**: Automating UPI may violate your bank's terms of service. Verify compliance.
2. **Security**: This skill requires elevated device access. Never expose ADB over public networks.
3. **Reliability**: USSD menus vary by carrier. UI layouts differ by device resolution and app version.
4. **Liability**: The author assumes no liability for financial losses. Test thoroughly with ₹1 transactions.
5. **Regulatory**: Commercial use of UPI automation requires NPCI approval.

---

## Future Enhancements

- [x] Integration with official NPCI UPI Deep Links (`upi://pay?pa=...`).
- [ ] ML-based screen state classification for adaptive UI navigation.
- [ ] Hardware Security Module (HSM) integration for transaction signing.
- [ ] Multi-factor biometric approval hooks before execution.
- [ ] Automatic USSD menu navigation via DTMF tone injection.

---

## References

- NPCI UPI *99# Service: https://www.npci.org.in/what-we-do/upi/product-overview
- scrcpy Documentation: https://github.com/Genymobile/scrcpy
- uiautomator2 Python Library: https://github.com/openatx/uiautomator2
