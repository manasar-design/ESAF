# appium_flow.py
"""
Drive the ESAF mobile app with Appium and capture the flow visually.

    ../venv/bin/python appium_flow.py               # auto-login, then record screens
    ../venv/bin/python appium_flow.py --manual      # you tap through the app, script records
    ../venv/bin/python appium_flow.py --no-install  # app already installed, keep its data
    ../venv/bin/python appium_flow.py --apk ~/Downloads/new.apk            # fresh install of a new build
    ../venv/bin/python appium_flow.py --apk ~/Downloads/new.apk --upgrade  # update in place, keep login

Batch mode — log in every FO from a file, no screenshots/video/report (only a
screenshot when a login fails) and a pass/fail CSV:

    ../venv/bin/python appium_flow.py --users ../DATA/emails_DEV_FO.txt
    ../venv/bin/python appium_flow.py --users ../DATA/emails_DEV_FO.txt --limit 10 --offset 5 --no-install

After each successful login (both modes) the collection APIs from test_collection.py
run in order: dashboard/center -> menu -> collections/centers -> v2/collections.
Add --skip-apis for a UI-only run.

Each step saves a screenshot + the on-screen elements (resource-id / text) and the
run ends with results/appium_<timestamp>/flow.html (step-by-step flow) and
screen.mp4 (screen recording).

Needs: Appium server running (`appium`), uiautomator2 driver, and a device /
emulator visible in `adb devices`.
"""
import argparse
import base64
import contextlib
import csv
import html
import io
import json
import os
import re
import subprocess
import time
from datetime import datetime

import test_collection as tc   # collection GET sequence + API login (BASE_URL lives there)
from playwright.sync_api import sync_playwright
from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ============================================================
# CONFIGURATION
# ============================================================
APPIUM_URL = "http://127.0.0.1:4723"
# APK_PATH   = os.path.expanduser("~/Downloads/ESAF-2439-debug.apk")             # DEV  -> esaf-dev-api.esthenos.com
APK_PATH = os.path.expanduser("~/Downloads/Esaf-Uat-debug (2).apk")           # UAT debug 3.2.1 -> guat-api.esafbank.com
# APK_PATH = os.path.expanduser("~/Downloads/Esaf-Uat-debug (1).apk")         # UAT debug 3.1.9
# APK_PATH = os.path.expanduser("~/Downloads/esaf-uat-debug-3.0.3.apk")      # UAT debug 3.0.3
# APK_PATH = os.path.expanduser("~/Downloads/Esaf(v3.2.0)-Uat-release.apk")  # UAT release: blocked on emulators
# APK_PATH = os.path.expanduser("~/Downloads/Esaf-Sit-debug.apk")              # SIT debug 3.1.7 -> gravity-sit-api.esafbank.com
APP_PACKAGE  = "gravity.esaf.bank.debug"   # DEV + UAT debug builds; SIT release uses a different id
APP_ACTIVITY = "com.esthenos.presentation.ui.splash.SplashActivity"
DEVICE_NAME = "Android"
UDID        = None    # set to the `adb devices` id if more than one device is attached
SPECIAL_APP_OPS = ["MANAGE_EXTERNAL_STORAGE", "REQUEST_INSTALL_PACKAGES"]
ADB         = os.path.join(os.environ.get("ANDROID_HOME", os.path.expanduser("~/Android/Sdk")), "platform-tools", "adb")

# EMAIL  = "manasafo"   # SIT FO
EMAIL    = "manasafo"   # UAT FO (single-user mode); the app takes the bare username, no domain
# EMAIL  = "ajayfo"     # DEV FO
PASSWORD = "Esaf@123"
OTP      = "123456"

WAIT_SECONDS = 30
RECORD_VIDEO = True

# Login screen locators (LoginActivity, captured from a recorded run)
EMAIL_ID        = f"{APP_PACKAGE}:id/email"
PASSWORD_ID     = f"{APP_PACKAGE}:id/password"
LOGIN_BUTTON_ID = f"{APP_PACKAGE}:id/sign_in_login"
LOGIN_ACTIVITY  = "LoginActivity"
HOME_ACTIVITY   = "HomeActivity"
TOUR_ACTIVITY   = "TourActivity"
SUBMIT_BUTTON_TEXT = r"(?i)^\s*(submit|verify|continue|proceed|next|ok)\s*$"
TOUR_BUTTON_TEXT   = r"(?i)^\s*(continue|skip|next|get started)\s*$"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_USERS_FILE = os.path.abspath(os.path.join(BASE_DIR, "..", "DATA", "emails_UAT_FO.txt"))
RUN_DIR  = os.path.abspath(os.path.join(BASE_DIR, "..", "results", f"appium_{datetime.now():%Y%m%d_%H%M%S}"))


# ============================================================
# DRIVER
# ============================================================
def installed_version() -> str:
    match = re.search(r"versionName=(\S+)", adb("shell", "dumpsys", "package", APP_PACKAGE))
    return match.group(1) if match else "not installed"


def prepare_app(mode: str):
    """Install / upgrade (optional) and grant every permission BEFORE the app is
    launched, otherwise it opens the Settings permission screens first.

    mode: "fresh"   -> uninstall + install APK_PATH (app data wiped)
          "upgrade" -> install APK_PATH over the current app (login kept)
          "keep"    -> use whatever is installed
    """
    adb("shell", "am", "force-stop", APP_PACKAGE)
    adb("shell", "am", "force-stop", "com.android.settings")
    if mode in ("fresh", "upgrade"):
        if not os.path.exists(APK_PATH):
            raise RuntimeError(f"❌ APK not found: {APK_PATH}")
        print(f"📦 {'Fresh install' if mode == 'fresh' else 'Upgrade'}: {os.path.basename(APK_PATH)}  (was {installed_version()})")
        if mode == "fresh":
            adb("uninstall", APP_PACKAGE)
        # -d allows installing an older build over a newer one
        result = adb("install", "-r", "-d", "-g", APK_PATH, timeout=300)
        if "Success" not in result:
            raise RuntimeError(f"❌ APK install failed: {result.strip()}")
    elif APP_PACKAGE not in adb("shell", "pm", "list", "packages", APP_PACKAGE):
        raise RuntimeError(f"❌ {APP_PACKAGE} not installed — run without --no-install")
    print(f"📱 App version on device: {installed_version()}")

    # "All files access" and "Install unknown apps" are special permissions that
    # install -g / autoGrantPermissions don't cover
    for op in SPECIAL_APP_OPS:
        adb("shell", "appops", "set", APP_PACKAGE, op, "allow")


def create_driver(mode: str):
    prepare_app(mode)

    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = DEVICE_NAME
    if UDID:
        options.udid = UDID
    options.app_package = APP_PACKAGE
    options.app_activity = APP_ACTIVITY
    options.no_reset = True                 # install/reset is handled by prepare_app()
    options.auto_grant_permissions = True
    options.new_command_timeout = 600
    options.set_capability("appium:adbExecTimeout", 60000)
    # Splash hands off to Tour/Login/Home almost instantly — accept any app screen as "started"
    options.set_capability("appium:appWaitActivity", "com.esthenos.presentation.ui.*")
    options.set_capability("appium:appWaitDuration", 40000)

    print(f"🔌 Connecting to Appium at {APPIUM_URL} ...")
    driver = webdriver.Remote(APPIUM_URL, options=options)
    time.sleep(3)
    if driver.current_package != APP_PACKAGE:
        raise RuntimeError(f"❌ Expected the ESAF app in front, got {driver.current_package}/{driver.current_activity}")
    print(f"✅ App started: {driver.current_package} / {driver.current_activity}")
    return driver


def adb(*args, timeout=60) -> str:
    cmd = [ADB] + (["-s", UDID] if UDID else []) + list(args)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return res.stdout + res.stderr


# ============================================================
# FLOW RECORDER
# ============================================================
class FlowRecorder:

    def __init__(self, driver, visual: bool = True):
        self.driver = driver
        self.visual = visual   # False = batch mode: just log step names, no screenshots / page dumps
        self.steps  = []
        if visual:
            os.makedirs(RUN_DIR, exist_ok=True)

    def capture(self, name: str, note: str = ""):
        idx = len(self.steps) + 1
        if not self.visual:
            self.steps.append({"step": idx, "name": name, "note": note})
            return self.steps[-1]
        shot = f"step_{idx:02d}.png"
        try:
            self.driver.save_screenshot(os.path.join(RUN_DIR, shot))
        except WebDriverException as e:
            print(f"  ⚠️  Screenshot failed: {e}")
            shot = None

        step = {
            "step"      : idx,
            "name"      : name,
            "note"      : note,
            "time"      : datetime.now().strftime("%H:%M:%S"),
            "activity"  : self._safe(lambda: self.driver.current_activity),
            "screenshot": shot,
            "elements"  : self.visible_elements(),
        }
        self.steps.append(step)
        print(f"📸 Step {idx}: {name}  [{step['activity']}]  ({len(step['elements'])} elements)")
        return step

    def visible_elements(self) -> list:
        """Interesting on-screen elements, so locators can be picked for the next run."""
        try:
            source = self.driver.page_source
        except WebDriverException:
            return []
        elements = []
        for node in re.finditer(r"<([\w.]+) [^>]*?>", source):
            tag = node.group(0)
            attr = lambda k: (re.search(rf'\b{k}="([^"]*)"', tag) or [None, ""])[1]
            rid, text, desc = attr("resource-id"), attr("text"), attr("content-desc")
            clickable = attr("clickable") == "true"
            if not (rid or text or desc) or not (clickable or text or "EditText" in tag):
                continue
            elements.append({
                "class": node.group(1).split(".")[-1],
                "id": rid, "text": html.unescape(text), "desc": html.unescape(desc),
                "clickable": clickable, "password": attr("password") == "true",
            })
        return elements

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception:
            return ""

    def write_report(self):
        with open(os.path.join(RUN_DIR, "flow.json"), "w") as f:
            json.dump(self.steps, f, indent=2)

        cards = []
        for s in self.steps:
            rows = "".join(
                f"<tr><td>{html.escape(e['class'])}</td><td><code>{html.escape(e['id'])}</code></td>"
                f"<td>{html.escape(e['text'] or e['desc'])}</td><td>{'👆' if e['clickable'] else ''}{'🔒' if e['password'] else ''}</td></tr>"
                for e in s["elements"]
            )
            img = f'<img src="{s["screenshot"]}" alt="step {s["step"]}">' if s["screenshot"] else "<div class='noimg'>no screenshot</div>"
            cards.append(f"""
            <div class="step">
              <div class="num">{s['step']}</div>
              {img}
              <h3>{html.escape(s['name'])}</h3>
              <p class="meta">{s['time']} · <code>{html.escape(s['activity'] or '')}</code></p>
              <p>{html.escape(s['note'])}</p>
              <details><summary>{len(s['elements'])} elements</summary>
                <table><tr><th>class</th><th>resource-id</th><th>text</th><th></th></tr>{rows}</table>
              </details>
            </div>""")

        video = '<video src="screen.mp4" controls></video>' if os.path.exists(os.path.join(RUN_DIR, "screen.mp4")) else ""
        page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>App Flow Report</title>
<style>
:root {{ --bg:#f6f7f9; --panel:#fff; --border:#e2e5ea; --text:#1d2330; --muted:#6b7380; --accent:#2f6fde; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#12151b; --panel:#1a1e26; --border:#2a303b; --text:#e3e7ee; --muted:#8b93a1; --accent:#5b8ff0; }} }}
body {{ margin:0; padding:16px; background:var(--bg); color:var(--text); font:14px/1.45 system-ui,sans-serif; }}
.flow {{ display:flex; gap:28px; overflow-x:auto; padding:8px 4px 16px; align-items:flex-start; }}
.step {{ position:relative; flex:0 0 260px; background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:10px; }}
.step:not(:last-child)::after {{ content:"→"; position:absolute; right:-22px; top:200px; color:var(--muted); font-size:20px; }}
.step img {{ width:100%; border-radius:8px; border:1px solid var(--border); }}
.num {{ position:absolute; top:-10px; left:-10px; background:var(--accent); color:#fff; border-radius:999px; width:26px; height:26px; display:grid; place-items:center; font-weight:600; }}
h3 {{ margin:8px 0 2px; font-size:15px; }} .meta {{ color:var(--muted); font-size:12px; margin:0; }}
table {{ width:100%; border-collapse:collapse; font-size:11px; }} td, th {{ border-top:1px solid var(--border); padding:3px; text-align:left; word-break:break-all; }}
video {{ max-width:360px; border-radius:8px; margin-top:12px; }} .noimg {{ height:400px; display:grid; place-items:center; color:var(--muted); }}
</style></head><body>
<h1>📱 ESAF App Flow</h1>
<p class="meta">{len(self.steps)} steps · {datetime.now():%Y-%m-%d %H:%M} · {html.escape(APK_PATH)}</p>
<div class="flow">{''.join(cards)}</div>
{video}
</body></html>"""
        path = os.path.join(RUN_DIR, "flow.html")
        with open(path, "w") as f:
            f.write(page)
        print(f"\n📊 Flow report: {path}")


# ============================================================
# ACTIONS
# ============================================================
def wait_for(driver, by, value, timeout=WAIT_SECONDS):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, value)))


def edit_texts(driver):
    return driver.find_elements(AppiumBy.CLASS_NAME, "android.widget.EditText")


def tap_button(driver, pattern: str) -> bool:
    # Compose UIs put the label on a non-clickable TextView inside the clickable
    # parent, so match on visible text and tap the label itself.
    for el in driver.find_elements(AppiumBy.XPATH, "//*[@text!='' or @content-desc!='']"):
        try:
            label = el.text or el.get_attribute("content-desc") or ""
            if re.match(pattern, label):
                el.click()
                return True
        except WebDriverException:
            continue
    return False


def pass_tour(driver, recorder: FlowRecorder):
    """Tap through the intro tour (shown on a fresh install) until the login form appears."""
    for i in range(6):
        if TOUR_ACTIVITY not in (driver.current_activity or "") or edit_texts(driver):
            return
        if i == 0:
            recorder.capture("Intro tour")
        if not tap_button(driver, TOUR_BUTTON_TEXT):
            return
        time.sleep(2)


def login(driver, recorder: FlowRecorder, username: str = EMAIL, password_text: str = PASSWORD):
    pass_tour(driver, recorder)
    # With --no-install the previous session may still be logged in — wait for
    # either the login form or the home screen, whichever comes first.
    wait_until(lambda: driver.find_elements(AppiumBy.ID, EMAIL_ID) or HOME_ACTIVITY in (driver.current_activity or ""))
    if driver.current_package != APP_PACKAGE:
        # Release builds close themselves on an emulator / with developer options on
        raise RuntimeError(f"App closed itself (now on {driver.current_package}) — release builds block "
                           "emulators and USB debugging; use a debug build of the APK")
    if not driver.find_elements(AppiumBy.ID, EMAIL_ID):
        wait_until(lambda: len(driver.find_elements(AppiumBy.XPATH, "//*[@text!='']")) > 5)
        recorder.capture("Already logged in", "Skipped login — app kept its session")
        return False
    recorder.capture("Login screen")

    email = driver.find_element(AppiumBy.ID, EMAIL_ID)
    email.clear()
    email.send_keys(username)
    password = driver.find_element(AppiumBy.ID, PASSWORD_ID)
    password.clear()
    password.send_keys(password_text)
    if driver.is_keyboard_shown():
        driver.hide_keyboard()
    recorder.capture("Credentials entered", username)

    driver.find_element(AppiumBy.ID, LOGIN_BUTTON_ID).click()
    left_login = wait_until(lambda: LOGIN_ACTIVITY not in (driver.current_activity or "") or "otp" in driver.page_source.lower())
    if left_login and recorder.visual:
        # Home is drawn after the activity opens — wait for its content so the screenshot isn't blank
        wait_until(lambda: len(driver.find_elements(AppiumBy.XPATH, "//*[@text!='']")) > 5)
        time.sleep(2)
    recorder.capture("After login tap", "" if left_login else "Still on login screen after waiting — check for an error message")

    enter_otp_if_asked(driver, recorder)
    return True


def wait_until(condition, timeout=WAIT_SECONDS, poll=1.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if condition():
                return True
        except WebDriverException:
            pass
        time.sleep(poll)
    return False


def enter_otp_if_asked(driver, recorder: FlowRecorder):
    source = driver.page_source.lower()
    if "otp" not in source:
        return
    fields = edit_texts(driver)
    if not fields:
        return
    if len(fields) >= len(OTP):           # one box per digit
        for box, digit in zip(fields, OTP):
            box.send_keys(digit)
    else:
        fields[0].send_keys(OTP)
    recorder.capture("OTP entered", OTP)
    if tap_button(driver, SUBMIT_BUTTON_TEXT):
        time.sleep(3)
        recorder.capture("After OTP submit")


def manual_recording(recorder: FlowRecorder):
    print("\n🎬 Manual mode — use the app on the device.")
    print("   Press Enter to capture a step (type a name first if you like), 'q' + Enter to finish.\n")
    while True:
        try:
            name = input(f"step {len(recorder.steps) + 1} > ").strip()
        except EOFError:
            break
        if name.lower() == "q":
            break
        recorder.capture(name or f"Screen {len(recorder.steps) + 1}")


# ============================================================
# COLLECTION APIS (after the UI login)
# ============================================================
API_COLUMNS = ["dashboard", "menu", "centers", "collections", "total_centers", "total_customers", "center_id", "payments"]
# The app accepts the bare username, but /api/v1/token/sourcing needs the full email
# API_EMAIL_DOMAIN = "esaf-dev.esthenos.com" # DEV,
API_EMAIL_DOMAIN = "guat.esafbank.com"   # UAT; gravity-sit.esafbank.com for SIT, esaf-dev.esthenos.com for DEV
# API_EMAIL_DOMAIN = "gravity-sit.esafbank.com" # SIT


def run_collection_apis(username: str, quiet: bool) -> dict:
    """API-login as the same FO, then every collection API: dashboard -> menu -> centers
    -> v2 collections (tc.run_collection_sequence) -> customers of every center ->
    make-payments (dry run unless tc.SUBMIT_PAYMENTS). quiet=True hides the dumps."""
    api_user = username if "@" in username else f"{username}@{API_EMAIL_DOMAIN}"
    out = io.StringIO() if quiet else None
    with (contextlib.redirect_stdout(out) if quiet else contextlib.nullcontext()):
        with sync_playwright() as p:
            request = p.request.new_context()
            try:
                headers = tc.perform_login(request, api_user, PASSWORD)
                if not headers:
                    return {"error": "api login failed"}
                seq = tc.run_collection_sequence(request, headers)
                if seq["center_id"] and seq["dues"]:
                    paid = tc.make_payments(request, headers, api_user, seq["center_id"], seq["dues"])
                    seq["payments"] = "dry-run" if not tc.SUBMIT_PAYMENTS else "ok" if paid else "failed"
            finally:
                request.dispose()
    return {k: seq.get(k) for k in API_COLUMNS}


def api_summary(result: dict) -> str:
    if result.get("error"):
        return result["error"]
    return "  ".join(f"{k}={result.get(k)}" for k in API_COLUMNS)


# ============================================================
# BATCH MODE (many FOs, no visuals)
# ============================================================
def load_users(path: str, offset: int, limit: int) -> list:
    """One login per line/row: .txt/.csv with an `email` column, or a .json list of
    {"username": ...}. Domains are stripped — the app login takes the bare username."""
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            names = [u.get("username") or u.get("email", "") for u in json.load(f)]
    else:
        with open(path, encoding="utf-8", newline="") as f:
            names = [row.get("email", "") for row in csv.DictReader(f)]
    users = [n.strip().split("@")[0] for n in names if n and n.strip()]
    return users[offset:offset + limit] if limit else users[offset:]


def reset_app():
    """Wipe the app's data between users (logs the previous FO out) — much faster
    than reinstalling. pm clear also revokes permissions, so grant them again."""
    adb("shell", "pm", "clear", APP_PACKAGE)
    dump = adb("shell", "dumpsys", "package", APP_PACKAGE)
    runtime = dump.split("runtime permissions:")[-1] if "runtime permissions:" in dump else ""
    for perm in re.findall(r"(android\.permission\.[A-Z_]+): granted=false", runtime):
        adb("shell", "pm", "grant", APP_PACKAGE, perm)
    for op in SPECIAL_APP_OPS:
        adb("shell", "appops", "set", APP_PACKAGE, op, "allow")


def run_batch(driver, users: list, call_apis: bool = True):
    batch_dir = RUN_DIR.replace("appium_", "appium_batch_")
    os.makedirs(batch_dir, exist_ok=True)
    csv_path = os.path.join(batch_dir, "results.csv")
    fields = ["#", "username", "status", "seconds", "screen", "error", "screenshot"] + API_COLUMNS + ["api_error"]
    with open(csv_path, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()

    # Long runs: stop the device screen from sleeping/locking between users
    adb("shell", "svc", "power", "stayon", "true")
    print(f"\n👥 Batch: {len(users)} users — results in {csv_path}\n")
    passed = 0
    for i, username in enumerate(users, 1):
        row = {"#": i, "username": username, "status": "FAIL", "error": "", "screenshot": ""}

        # Collection APIs run BEFORE the app login: one session per user (3.1.x),
        # so the app's login comes last and stays valid. API problems get their
        # own columns and never change the UI PASS/FAIL status.
        api_line = ""
        if call_apis:
            try:
                api = run_collection_apis(username, quiet=True)
            except Exception as e:
                api = {"error": f"api error: {str(e).strip().splitlines()[0][:150] if str(e).strip() else type(e).__name__}"}
            row.update({k: api.get(k) for k in API_COLUMNS})
            row["api_error"] = api.get("error", "")
            api_line = f"  | {api_summary(api)}"

        start = time.time()
        try:
            driver.terminate_app(APP_PACKAGE)
            reset_app()
            driver.activate_app(APP_PACKAGE)
            login(driver, FlowRecorder(driver, visual=False), username, PASSWORD)
            if wait_until(lambda: HOME_ACTIVITY in (driver.current_activity or ""), timeout=10):
                row["status"] = "PASS"
                passed += 1
            else:
                row["error"] = "Did not reach home screen"
        except (RuntimeError, WebDriverException) as e:
            row["error"] = (getattr(e, "msg", None) or str(e)).strip().splitlines()[0][:200] if str(e).strip() else "Timed out"
        row["seconds"] = round(time.time() - start, 1)
        row["screen"] = FlowRecorder._safe(lambda: driver.current_activity.split(".")[-1])

        if row["status"] == "FAIL":
            shot = f"fail_{i:03d}_{username}.png"
            try:
                driver.save_screenshot(os.path.join(batch_dir, shot))
                row["screenshot"] = shot
            except WebDriverException:
                pass

        with open(csv_path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=fields).writerow(row)
        mark = "✅" if row["status"] == "PASS" else "❌"
        print(f"{mark} {i:>3}/{len(users)}  {username:<20} {row['seconds']:>5}s  {row['error']}{api_line}")

    print(f"\n📊 {passed}/{len(users)} passed, {len(users) - passed} failed — {csv_path}")


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Record the ESAF app flow with Appium")
    parser.add_argument("--manual", action="store_true", help="skip auto-login; capture screens on Enter")
    parser.add_argument("--no-install", action="store_true", help="use the installed app and keep its data")
    parser.add_argument("--apk", help="APK to install instead of APK_PATH (e.g. a new build)")
    parser.add_argument("--upgrade", action="store_true", help="install the APK over the current app, keeping its login/data")
    parser.add_argument("--users", nargs="?", const=DEFAULT_USERS_FILE,
                        help="batch mode: log in every user in this file, no visuals (default: DATA/emails_DEV_FO.txt)")
    parser.add_argument("--limit", type=int, default=0, help="batch mode: max users to run (0 = all)")
    parser.add_argument("--offset", type=int, default=0, help="batch mode: skip this many users from the top")
    parser.add_argument("--skip-apis", action="store_true",
                        help="don't call the collection APIs (dashboard, menu, centers, collections, customers, make-payments)")
    args = parser.parse_args()

    global APK_PATH
    if args.apk:
        APK_PATH = os.path.abspath(os.path.expanduser(args.apk))
    mode = "keep" if args.no_install else "upgrade" if args.upgrade else "fresh"

    if args.users:
        users = load_users(args.users, args.offset, args.limit)
        if not users:
            raise SystemExit(f"❌ No users found in {args.users}")
        driver = create_driver(mode)
        try:
            run_batch(driver, users, call_apis=not args.skip_apis)
        finally:
            driver.quit()
        return

    driver = create_driver(mode)
    recorder = FlowRecorder(driver)
    if RECORD_VIDEO:
        driver.start_recording_screen(timeLimit="1800")

    try:
        if args.manual:
            recorder.capture("App launched")
        else:
            # One session per user (3.1.x): the APIs log in first, then the app,
            # so the app's session is the newest one and stays valid.
            if not args.skip_apis:
                print(f"\n🌐 Collection APIs for {EMAIL}: dashboard → menu → centers → collections → customers → make-payments")
                try:
                    api = run_collection_apis(EMAIL, quiet=False)
                except Exception as e:
                    api = {"error": f"api error: {e}"}
                print(f"\n📋 {api_summary(api)}")
                recorder.capture("Collection APIs", api_summary(api))
            logged_in_now = login(driver, recorder)
            # With --no-install the app may have kept an old session, which the API
            # login just invalidated — if the app drops to the login screen, log in again.
            if not args.skip_apis and not logged_in_now and \
                    wait_until(lambda: driver.find_elements(AppiumBy.ID, EMAIL_ID), timeout=10):
                print("🔁 App's old session was replaced by the API login — logging in again")
                login(driver, recorder)
            print("\n✅ Login flow done — continue in the app and capture more steps.")
        manual_recording(recorder)
    except (RuntimeError, WebDriverException) as e:
        reason = getattr(e, "msg", None) or str(e).splitlines()[0] if str(e).strip() else "Timed out waiting for the screen"
        print(f"\n❌ {reason}")
        recorder.capture("Failure screen", reason)
    finally:
        if RECORD_VIDEO:
            try:
                video = driver.stop_recording_screen()
                with open(os.path.join(RUN_DIR, "screen.mp4"), "wb") as f:
                    f.write(base64.b64decode(video))
            except WebDriverException as e:
                print(f"  ⚠️  Could not save recording: {e}")
        recorder.write_report()
        driver.quit()


if __name__ == "__main__":
    main()
