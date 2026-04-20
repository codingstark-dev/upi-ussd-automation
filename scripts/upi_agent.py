"""
UPI Payment Automation Skill
============================
Securely automates UPI payments on an Android device via ADB / scrcpy.
Supports USSD (*99#) and UPI app automation via uiautomator2.

SECURITY WARNING:
- Never store UPI PINs or passwords in this script.
- Automation intentionally stops before the final PIN/Biometric step.
- Always test with ₹1 transactions before real use.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any, Optional

# Optional dependency for app automation
try:
    import uiautomator2 as u2
except ImportError:
    u2 = None  # type: ignore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "max_daily_limit": 10_000.0,
    "max_per_transaction": 5_000.0,
    "allowed_hours": {"start": "00:00", "end": "23:59"},
    "require_confirmation_above": 1_000.0,
    "whitelist_only": True,
    "allowed_payees": [],
    "allowed_apps": ["*99#", "com.phonepe.app", "com.google.android.apps.nbu.paisa.user", "net.one97.paytm"],
    "screenshot_dir": "./screenshots",
    "device_serial": None,
    "agent_password": None,
    "abort_on_disconnect": True,
    "ussd_timeout": 30,
    "ui_timeout": 10,
}

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class PaymentIntent:
    payee: str
    amount: float
    app: str = "*99#"
    remarks: Optional[str] = None
    raw_input: str = ""

    def __post_init__(self):
        # Allow zero amount for quick USSD dial (e.g. raw_input == "*99#")
        if self.amount < 0:
            raise ValueError("Amount cannot be negative")
        if self.amount == 0 and not (self.raw_input == "*99#" and self.payee == ""):
            raise ValueError("Amount must be positive")
        if self.app not in DEFAULT_CONFIG["allowed_apps"]:
            # Allow generic fallback for known names
            app_map = {
                "phonepe": "com.phonepe.app",
                "gpay": "com.google.android.apps.nbu.paisa.user",
                "paytm": "net.one97.paytm",
                "googlepay": "com.google.android.apps.nbu.paisa.user",
            }
            mapped = app_map.get(self.app.lower())
            if mapped and mapped in DEFAULT_CONFIG["allowed_apps"]:
                self.app = mapped
            elif self.app.lower() not in {"*99#", "generic"}:
                raise ValueError(f"Unsupported payment app: {self.app}")


@dataclass
class TransactionResult:
    success: bool
    message: str
    intent: PaymentIntent
    screenshots: list[str] = field(default_factory=list)
    ussd_code: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


class PaymentError(Exception):
    """Raised when a payment validation or execution step fails."""
    pass


# ---------------------------------------------------------------------------
# Logger Setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("upi_skill")


# ---------------------------------------------------------------------------
# Core Skill
# ---------------------------------------------------------------------------

class UPISkill:
    """
    Main skill class for parsing, validating, and executing UPI payments
    on a remotely connected Android device.
    """

    # Mapping common names to ADB package names
    APP_PACKAGES = {
        "phonepe": "com.phonepe.app",
        "gpay": "com.google.android.apps.nbu.paisa.user",
        "paytm": "net.one97.paytm",
        "googlepay": "com.google.android.apps.nbu.paisa.user",
        "*99#": "*99#",
        "generic": "*99#",
    }

    def __init__(self, device_serial: Optional[str] = None, config_path: Optional[str] = None):
        self.device_serial = device_serial or self._discover_device()
        self.config = self._load_config(config_path)
        self.daily_spent: float = 0.0
        self.screenshot_dir = Path(self.config["screenshot_dir"])
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        # Initialize uiautomator2 if available
        self._ui = None
        if u2 is not None:
            try:
                self._ui = u2.connect(self.device_serial)
                logger.info("uiautomator2 connected to %s", self.device_serial)
            except Exception as exc:
                logger.warning("uiautomator2 connection failed: %s", exc)
        else:
            logger.warning("uiautomator2 not installed; app automation unavailable")

    # ------------------------------------------------------------------
    # Config & Helpers
    # ------------------------------------------------------------------

    def _load_config(self, path: Optional[str]) -> dict:
        cfg = DEFAULT_CONFIG.copy()

        # Auto-detect config.json in script directory if no path given
        if path is None:
            script_dir = Path(__file__).parent.resolve()
            auto_path = script_dir / "config.json"
            if auto_path.is_file():
                path = str(auto_path)

        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                user_cfg = json.load(fh)
            # Deep merge so nested dicts (like allowed_hours) aren't accidentally clobbered
            for key, value in user_cfg.items():
                if isinstance(value, dict) and key in cfg and isinstance(cfg[key], dict):
                    cfg[key].update(value)
                else:
                    cfg[key] = value

        # Ensure required nested keys exist (fallback to defaults if missing)
        for key, default_val in DEFAULT_CONFIG.items():
            if key not in cfg:
                cfg[key] = default_val
            elif isinstance(default_val, dict) and isinstance(cfg.get(key), dict):
                for sub_key, sub_default in default_val.items():
                    cfg[key].setdefault(sub_key, sub_default)

        # Override device serial if passed explicitly
        if self.device_serial:
            cfg["device_serial"] = self.device_serial
        return cfg

    @staticmethod
    def _discover_device() -> str:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, check=True
        )
        lines = [line.strip() for line in result.stdout.splitlines() if "\tdevice" in line]
        if not lines:
            raise PaymentError("No ADB device found. Connect a device and enable USB debugging.")
        if len(lines) > 1:
            raise PaymentError(
                f"Multiple devices found: {lines}. Specify device_serial explicitly."
            )
        return lines[0].split("\t")[0]

    def _adb(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["adb", "-s", self.device_serial, *args]
        logger.debug("ADB: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, check=check)

    def _screenshot(self, name: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{name}.png"
        local_path = self.screenshot_dir / filename
        remote_path = f"/sdcard/{filename}"
        self._adb("shell", "screencap", "-p", remote_path)
        self._adb("pull", remote_path, str(local_path))
        self._adb("shell", "rm", remote_path)
        logger.info("Screenshot saved: %s", local_path)
        return str(local_path)

    # ------------------------------------------------------------------
    # Phase 1: Parsing
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> PaymentIntent:
        """
        Extract payee, amount, and app preference from natural language.
        Supports mobile numbers and basic UPI IDs.

        Also supports structured shortcuts:
          pay <payee> <amount> [app]
          send <amount> to <payee> [app]
          *99#                    (quick dial)
        """
        text_stripped = text.strip()
        text_lower = text_stripped.lower()

        # --- Quick USSD dial shortcut ---
        if text_stripped == "*99#":
            return PaymentIntent(
                payee="",
                amount=0.0,
                app="*99#",
                remarks="Quick USSD dial",
                raw_input=text_stripped,
            )

        # --- Structured shortcuts ---
        # pay <payee> <amount> [via/app]
        pay_shortcut = re.match(
            r"^pay\s+(\S+)\s+(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:via\s+(\w+))?",
            text_lower,
        )
        if pay_shortcut:
            payee = pay_shortcut.group(1)
            amount = float(pay_shortcut.group(2).replace(",", ""))
            app = pay_shortcut.group(3) or "*99#"
            return PaymentIntent(
                payee=payee, amount=amount, app=app,
                remarks=text_stripped, raw_input=text_stripped,
            )

        # send <amount> to <payee> [via/app]
        send_shortcut = re.match(
            r"^send\s+(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s+(?:rs\.?|rupees?|₹)?\s*to\s+(\S+)\s*(?:via\s+(\w+))?",
            text_lower,
        )
        if not send_shortcut:
            # try without currency word: send 250 to 9876543210
            send_shortcut = re.match(
                r"^send\s+(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s+to\s+(\S+)\s*(?:via\s+(\w+))?",
                text_lower,
            )
        if send_shortcut:
            amount = float(send_shortcut.group(1).replace(",", ""))
            payee = send_shortcut.group(2)
            app = send_shortcut.group(3) or "*99#"
            return PaymentIntent(
                payee=payee, amount=amount, app=app,
                remarks=text_stripped, raw_input=text_stripped,
            )

        # --- Natural language parsing ---
        # Extract amount
        amount_patterns = [
            r"(?:rs\.?|rupees?|₹|inr)\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)",
            r"\b(?:pay|send)\s+(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:rs\.?|rupees?|₹)?",
            r"(\d+(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:rs\.?|rupees?|₹)",
        ]
        amounts: list[str] = []
        for pat in amount_patterns:
            amounts.extend(re.findall(pat, text_lower))
        if not amounts:
            raise PaymentError(
                "Could not parse amount from input.\n"
                "Try one of these formats:\n"
                "  pay 9876543210 250\n"
                "  send 500 to rahul via phonepe\n"
                "  Pay 9876543210 rupees 250 for lunch\n"
                "  *99#"
            )
        amount = float(amounts[0].replace(",", ""))

        # Extract payee (10-digit mobile number or UPI ID)
        mobiles = re.findall(r"\b([6-9]\d{9})\b", text)
        upi_ids = re.findall(r"[\w.-]+@[\w.-]+", text)
        payee = mobiles[0] if mobiles else (upi_ids[0] if upi_ids else "")
        if not payee:
            raise PaymentError(
                "Could not parse payee (mobile number or UPI ID) from input.\n"
                "Try one of these formats:\n"
                "  pay 9876543210 250\n"
                "  send 500 to rahul@upi\n"
                "  Pay 9876543210 rupees 250 for lunch"
            )

        # Detect app preference
        app = "*99#"
        app_keywords = {
            "phonepe": "phonepe",
            "gpay": "gpay",
            "googlepay": "gpay",
            "paytm": "paytm",
            "upi": "*99#",
        }
        for keyword, app_key in app_keywords.items():
            if keyword in text_lower:
                app = app_key
                break

        # Remarks (everything else is hard; keep raw input)
        remarks = text_stripped

        return PaymentIntent(
            payee=payee,
            amount=amount,
            app=app,
            remarks=remarks,
            raw_input=text_stripped,
        )

    # ------------------------------------------------------------------
    # Phase 2: Validation
    # ------------------------------------------------------------------

    def validate(self, intent: PaymentIntent, password: Optional[str] = None) -> bool:
        """
        Multi-layer validation. Returns True if valid, raises PaymentError otherwise.
        """
        # Agent password check
        cfg_pw = self.config.get("agent_password")
        if cfg_pw and password != cfg_pw:
            raise PaymentError("Invalid agent password.")

        # Amount checks
        if intent.amount > self.config["max_per_transaction"]:
            raise PaymentError(
                f"Amount ₹{intent.amount} exceeds per-transaction limit of "
                f"₹{self.config['max_per_transaction']}."
            )
        if (self.daily_spent + intent.amount) > self.config["max_daily_limit"]:
            raise PaymentError(
                f"This transaction would exceed your daily limit of "
                f"₹{self.config['max_daily_limit']}."
            )

        # Allowed hours
        if not self._is_within_allowed_hours():
            start, end = self.config["allowed_hours"]["start"], self.config["allowed_hours"]["end"]
            raise PaymentError(f"Payments are only allowed between {start} and {end}.")

        # Whitelist check
        if self.config["whitelist_only"]:
            allowed = {str(a).strip() for a in self.config.get("allowed_payees", [])}
            if allowed and intent.payee not in allowed:
                raise PaymentError(
                    f"Payee {intent.payee} is not in the allowed list. "
                    f"Add them to config to enable payments."
                )

        # App check
        if intent.app not in self.config["allowed_apps"]:
            raise PaymentError(f"App '{intent.app}' is not in the allowed apps list.")

        logger.info("Validation passed for %s", intent)
        return True

    def _is_within_allowed_hours(self) -> bool:
        now = datetime.now().time()
        start = dt_time.fromisoformat(self.config["allowed_hours"]["start"])
        end = dt_time.fromisoformat(self.config["allowed_hours"]["end"])
        if start < end:
            return start <= now <= end
        else:  # crosses midnight
            return now >= start or now <= end

    def confirm(self, intent: PaymentIntent) -> bool:
        """
        Require explicit user confirmation for high-value transactions.
        In a headless/agent context, this should call out to the user
        (e.g., voice prompt, chat confirmation) and return True/False.
        """
        threshold = self.config["require_confirmation_above"]
        if intent.amount >= threshold:
            # Placeholder: replace with actual user-interaction logic
            print(
                f"\n[CONFIRMATION REQUIRED] Pay ₹{intent.amount} to {intent.payee} via {intent.app}?"
            )
            answer = input("Type 'yes' to proceed: ").strip().lower()
            if answer != "yes":
                logger.warning("User declined confirmation for %s", intent)
                return False
        return True

    # ------------------------------------------------------------------
    # Phase 3 & 4: Execution
    # ------------------------------------------------------------------

    def execute(self, intent: PaymentIntent) -> TransactionResult:
        """
        Main execution entrypoint. Dispatches to the best strategy:
          - UPI Deep Link (if payee is a UPI ID like user@upi)
          - USSD *99# (if app is *99# or payee is mobile number)
          - App automation (if specific app requested)
        """
        screenshots: list[str] = []

        try:
            # Strategy C: UPI Deep Link (most reliable for UPI IDs)
            if "@" in intent.payee:
                result = self._execute_upi_deep_link(intent)
            elif intent.app == "*99#":
                result = self._execute_ussd(intent)
            else:
                result = self._execute_app(intent)
            screenshots.extend(result.screenshots)
            return TransactionResult(
                success=result.success,
                message=result.message,
                intent=intent,
                screenshots=screenshots,
                ussd_code=result.ussd_code,
                meta=result.meta,
            )
        except Exception as exc:
            # Capture failure screenshot for forensics
            try:
                screenshots.append(self._screenshot("failure"))
            except Exception:
                pass
            raise PaymentError(f"Execution failed: {exc}") from exc

    def _execute_ussd(self, intent: PaymentIntent) -> TransactionResult:
        """
        Strategy A: Open the *99# USSD menu via ADB.

        IMPORTANT: On modern Android/carriers, pre-composed USSD strings
        like *99*1*1*NUMBER*AMOUNT# are treated as phone calls and fail.
        We use ACTION_DIAL with URL-encoded %23 to reliably open the
        interactive USSD menu. The user must then navigate the menus manually.
        """
        # Use ACTION_DIAL (not CALL) and encode # as %23
        ussd = "tel:*99%23"
        logger.info("Opening USSD menu: %s", ussd)

        self._adb(
            "shell", "am", "start", "-a",
            "android.intent.action.DIAL", "-d", ussd
        )
        time.sleep(3)

        screenshots = [self._screenshot("ussd_menu_opened")]

        if intent.payee and intent.amount > 0:
            msg = (
                f"USSD *99# menu opened.\n"
                f"You requested: Pay ₹{intent.amount} to {intent.payee}\n"
                f"The interactive menu is now on your device. "
                f"Navigate it manually (e.g., 1 → Send Money → 1 → Mobile → "
                f"enter {intent.payee} → enter {intent.amount} → confirm). "
                f"Automation stops here for security."
            )
        else:
            msg = (
                "USSD *99# menu opened. "
                "Navigate the interactive menu manually on your device."
            )

        return TransactionResult(
            success=True,
            message=msg,
            intent=intent,
            screenshots=screenshots,
            ussd_code=ussd,
        )

    def _execute_upi_deep_link(self, intent: PaymentIntent) -> TransactionResult:
        """
        Strategy C: Use the official NPCI UPI Deep Link intent.
        Opens the system's UPI app chooser with payee + amount pre-filled.
        This is the MOST RELIABLE method when the payee has a UPI ID.
        """
        # Build the UPI URI
        import urllib.parse
        payee_vpa = intent.payee
        params = {
            "pa": payee_vpa,
            "pn": "Payee",  # Generic; ideally we'd map VPA to a name
            "am": str(intent.amount),
            "cu": "INR",
            "tr": f"UPISKILL{int(time.time())}",
            "tn": intent.remarks or "UPI Payment",
        }
        query = urllib.parse.urlencode(params)
        upi_uri = f"upi://pay?{query}"

        logger.info("Launching UPI deep link: %s", upi_uri)

        self._adb(
            "shell", "am", "start", "-a",
            "android.intent.action.VIEW", "-d", upi_uri
        )
        time.sleep(3)

        screenshots = [self._screenshot("upi_deep_link_opened")]

        return TransactionResult(
            success=True,
            message=(
                f"UPI app chooser opened with payee {payee_vpa} "
                f"and amount ₹{intent.amount} pre-filled. "
                "Select your preferred UPI app and complete the payment. "
                "Automation stops before PIN entry for security."
            ),
            intent=intent,
            screenshots=screenshots,
            meta={"upi_uri": upi_uri},
        )

    def _execute_app(self, intent: PaymentIntent) -> TransactionResult:
        """
        Strategy B: Automate a UPI app using uiautomator2.
        Navigates Send Money → Enter Number → Enter Amount → Click Pay.
        **Stops before the PIN screen**.
        """
        if self._ui is None:
            raise PaymentError("uiautomator2 is not available. Install it and run 'python -m uiautomator2 init'.")

        pkg = self.APP_PACKAGES.get(intent.app, intent.app)
        logger.info("Launching app package: %s", pkg)

        self._ui.app_start(pkg)
        time.sleep(3)  # Wait for launch
        screenshots = [self._screenshot("app_launched")]

        # --- Generic Heuristic Navigation ---
        # These selectors are approximate and will vary by app version.
        # They serve as a starting point; adjust based on your device.

        # 1. Try to click "Send Money" or similar
        send_money_labels = ["Send Money", "Send", "Pay", "Transfer", "Pay Now"]
        clicked = False
        for label in send_money_labels:
            if self._ui(text=label).exists(timeout=5):
                self._ui(text=label).click()
                clicked = True
                logger.info("Clicked '%s'", label)
                break
        if not clicked:
            screenshots.append(self._screenshot("send_money_not_found"))
            raise PaymentError("Could not find 'Send Money' button. Check UI layout.")

        time.sleep(2)
        screenshots.append(self._screenshot("after_send_money_click"))

        # 2. Enter Payee
        # Try to find a text field near labels like "Mobile" or "UPI ID"
        payee_field = None
        for hint in ["Mobile number", "UPI ID", "Enter mobile", "Enter number", "To"]:
            if self._ui(textContains=hint).exists(timeout=3):
                # Often the EditText is adjacent or the same element; attempt focus
                try:
                    self._ui(textContains=hint).click()
                    payee_field = True
                    break
                except Exception:
                    continue

        if not payee_field:
            # Fallback: find any focused EditText
            if self._ui(className="android.widget.EditText").exists(timeout=3):
                self._ui(className="android.widget.EditText").click()
                payee_field = True

        if not payee_field:
            screenshots.append(self._screenshot("payee_field_not_found"))
            raise PaymentError("Could not locate payee input field.")

        self._ui.send_keys(intent.payee)
        time.sleep(1)
        screenshots.append(self._screenshot("payee_entered"))

        # 3. Enter Amount
        # Some apps have two EditTexts; we try the second one or search by hint
        amount_field = None
        for hint in ["Amount", "Enter amount", "₹"]:
            elems = self._ui(textContains=hint)
            if elems.exists(timeout=3):
                try:
                    elems.click()
                    amount_field = True
                    break
                except Exception:
                    continue

        if not amount_field:
            # Fallback to all EditTexts and pick the one that isn't payee
            edits = self._ui(className="android.widget.EditText")
            if edits.count >= 2:
                edits[1].click()
                amount_field = True
            elif edits.count == 1:
                edits.click()
                amount_field = True

        if not amount_field:
            screenshots.append(self._screenshot("amount_field_not_found"))
            raise PaymentError("Could not locate amount input field.")

        self._ui.send_keys(str(intent.amount))
        time.sleep(1)
        screenshots.append(self._screenshot("amount_entered"))

        # 4. Click Pay / Continue
        pay_labels = ["Pay", "Continue", "Proceed", "Next", "Send"]
        clicked_pay = False
        for label in pay_labels:
            if self._ui(text=label).exists(timeout=3):
                self._ui(text=label).click()
                clicked_pay = True
                logger.info("Clicked '%s'", label)
                break

        if not clicked_pay:
            screenshots.append(self._screenshot("pay_button_not_found"))
            raise PaymentError("Could not find final 'Pay' button.")

        time.sleep(2)
        screenshots.append(self._screenshot("before_pin"))

        # 5. HALT before PIN
        return TransactionResult(
            success=True,
            message=(
                "Payment flow automated up to the PIN/Biometric screen. "
                "Please complete authentication on the device or via scrcpy. "
                "DO NOT share your PIN with the agent."
            ),
            intent=intent,
            screenshots=screenshots,
        )

    # ------------------------------------------------------------------
    # Post-Transaction
    # ------------------------------------------------------------------

    def wait_for_completion(self, timeout: int = 60) -> Optional[str]:
        """
        Placeholder for monitoring the post-transaction screen.
        Could use OCR to extract UTR / reference number.
        """
        logger.info("Waiting %ss for transaction completion...", timeout)
        time.sleep(timeout)
        path = self._screenshot("post_txn")
        return path

    def update_daily_spent(self, amount: float):
        """Persist daily spent amount to a local JSON file."""
        self.daily_spent += amount
        state_path = Path("upi_skill_state.json")
        state = {"daily_spent": self.daily_spent, "date": datetime.now().isoformat()}
        state_path.write_text(json.dumps(state, indent=2))
        logger.info("Updated daily spent: ₹%s", self.daily_spent)


# ---------------------------------------------------------------------------
# CLI / Demo Entrypoint
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("UPI Payment Automation Skill")
    print("=" * 60)
    print("\nAccepted formats:")
    print("  pay 9876543210 250          (USSD menu - navigate manually)")
    print("  pay user@upi 250             (UPI Deep Link - most reliable)")
    print("  send 500 to rahul via phonepe (App automation)")
    print("  Pay 9876543210 rupees 250    (Natural language)")
    print("  *99#                         (Quick USSD dial)")
    print("  quit                         (Exit)")
    print("-" * 60)

    # Load skill
    skill = UPISkill()

    # Developer mode warning
    if not skill.config.get("whitelist_only", True):
        print("⚠️  DEVELOPER MODE: Whitelist disabled. All payees allowed.")
    if not skill.config.get("agent_password"):
        print("⚠️  DEVELOPER MODE: No agent password set.")
    print("-" * 60)

    # Example interactive loop
    while True:
        try:
            user_input = input("\nEnter payment request (or 'quit'): ").strip()
            if user_input.lower() in {"quit", "exit", "q"}:
                break
            if not user_input:
                continue

            # 1. Parse
            intent = UPISkill.parse(user_input)

            # Quick USSD dial bypasses validation/ledger
            if intent.raw_input == "*99#":
                print("\n[Quick Dial] Opening *99# USSD menu...")
                result = skill.execute(intent)
                print(f"\n[Result] {result.message}")
                continue

            print(f"\n[Parsed] Pay ₹{intent.amount} to {intent.payee} via {intent.app}")

            # 2. Validate
            # In real usage, supply agent password if configured
            skill.validate(intent, password=None)

            # 3. Confirm
            if not skill.confirm(intent):
                print("[Aborted] User declined confirmation.")
                continue

            # 4. Execute
            result = skill.execute(intent)
            print(f"\n[Result] {result.message}")
            if result.screenshots:
                print(f"[Screenshots] {len(result.screenshots)} captured:")
                for s in result.screenshots:
                    print(f"  - {s}")

            # 5. Update ledger (only if you manually confirm success)
            skill.update_daily_spent(intent.amount)

        except PaymentError as exc:
            logger.error("PaymentError: %s", exc)
            print(f"\n[Error] {exc}")
        except Exception as exc:
            logger.exception("Unexpected error")
            print(f"\n[Critical Error] {exc}")


if __name__ == "__main__":
    main()
