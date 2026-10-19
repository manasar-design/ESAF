# flow_bm.py
"""
BM hierarchy (WEB): web login, access features, dashboard statistics, then
applications and customers (page 1).
"""
import flow_common as fc

NAME = "BM"
USERS_FILE = "BM_credentials.txt"

ACCESS_FEATURE_URL = f"{fc.WEB_API}/organisation/get_user_access_feature"
DASHBOARD_URL      = f"{fc.WEB_API}/organisation/dashboard?stats_key=statistics"
APPLICATIONS_URL = f"{fc.WEB_API}/organisation/applications?page=1"
CUSTOMERS_URL    = f"{fc.WEB_API}/organisation/customers?page=1"


def run(request, user: dict, login_only: bool = False) -> dict:
    email = user.get("email") or fc.user_label(user)
    status, auth, ms = fc.web_login(request, email, fc.RESET_PASSWORD)
    r = {"login": fc.fmt(status, ms), "ok": auth is not None, "steps": {}, "error": ""}
    if not auth or login_only:
        return r
    st, _, ms = fc.web_get(request, ACCESS_FEATURE_URL, auth)
    r["steps"]["access_feature"] = fc.fmt(st, ms)
    st, _, ms = fc.web_get(request, DASHBOARD_URL, auth)
    r["steps"]["dashboard"] = fc.fmt(st, ms)
    st, _, ms = fc.web_get(request, APPLICATIONS_URL, auth)
    r["steps"]["applications"] = fc.fmt(st, ms)
    st, _, ms = fc.web_get(request, CUSTOMERS_URL, auth)
    r["steps"]["customers"] = fc.fmt(st, ms)
    return r
