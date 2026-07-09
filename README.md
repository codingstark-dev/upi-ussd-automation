# UPI USSD Automation Skill

This repository contains an AI agent skill for secure UPI/USSD payment automation on Android devices using ADB, scrcpy, and uiautomator2.

It is designed for terminal-based agent workflows where natural-language payment requests are parsed, validated, and executed with strict safety guardrails.

---

## 🛠️ Installation

### Option A: Install Python dependencies
```bash
pip install -r scripts/requirements.txt
python -m uiautomator2 init
```

### Option B: Verify Android connectivity first
```bash
adb devices
scrcpy --version
```

Then update runtime guardrails in `scripts/config.json` before any execution.

---

## 🔒 Security & Risk Controls

1. **Human-in-the-loop finalization:** automation must stop before UPI PIN/biometric approval.
2. **Fail-closed execution:** unknown UI states, disconnects, and validation mismatches abort the transaction.
3. **Runtime guardrails:** per-transaction limits, daily caps, app allowlists, and optional payee allowlists are enforced from config.
4. **Audit trail support:** screenshots and execution state are captured to support traceability.

---

## 🚀 Core Capabilities

1. **Intent Parsing:** convert natural-language requests into structured payment intents.
2. **Pre-flight Validation:** validate amount, app, time window, and policy limits before execution.
3. **UPI Deep Link Execution:** launch `upi://pay` intents for reliable app handoff.
4. **USSD Initiation (`*99#`):** trigger interactive USSD flows through Android dial intents.
5. **App UI Automation:** optionally drive supported UPI apps via uiautomator2 when needed.

---

## 📂 Repository Layout

- `SKILL.md` — skill manifest, architecture, workflow, and security model.
- `scripts/upi_agent.py` — core Python implementation.
- `scripts/config.json` — policy and runtime configuration.
- `scripts/README.md` — script-level setup details.

---

For full execution and architecture details, see `SKILL.md`.
