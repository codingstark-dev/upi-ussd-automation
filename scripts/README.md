# Scripts module

This directory contains the runnable Python implementation for UPI/USSD automation.

## Files

- `upi_agent.py`: Core automation skill logic.
- `config.json`: Guardrails and runtime configuration.
- `requirements.txt`: Python dependencies.

## Setup

```bash
pip install -r requirements.txt
python -m uiautomator2 init
adb devices
```

## Run

Use `upi_agent.py` from your Python runtime and supply a valid device serial and config file path.