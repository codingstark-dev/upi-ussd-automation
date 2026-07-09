# UPI USSD Automation

Automate UPI payment flows on an Android device through ADB/scrcpy with strict safety checks, human confirmation before PIN entry, and audit logging.

## What this repository contains

- `SKILL.md`: Skill definition and operational/security guidance.
- `scripts/upi_agent.py`: Python implementation for parsing, validating, and executing payment intents.
- `scripts/config.json`: Runtime limits and guardrail configuration.
- `scripts/README.md`: Script-focused setup and usage.

## Quick start

1. Install dependencies:
   ```bash
   pip install -r scripts/requirements.txt
   ```
2. Connect your Android device with ADB:
   ```bash
   adb devices
   ```
3. Configure limits and allowed apps in: `scripts/config.json`
4. Run automation from the Python module in `scripts/`.

## Security note

This project handles financial transaction automation. Keep `agent_password` configured, avoid exposing ADB publicly, and always require explicit user confirmation before payment finalization.
