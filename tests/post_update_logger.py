# post_update_logger.py
"""
Standalone module to log employee update data to CSV.
Call `log_update_to_csv(create_data, update_payload, update_response)`
immediately after the PUT API call in employee_creation.py.
"""

import csv
import json
import os

# ──────────────────────────────────────────────────────────────
# DYNAMIC PATH SETUP — Saves CSV to jmeter/results/ directory
# ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "results"))
CSV_UPDATE_DETAILS = os.path.join(RESULTS_DIR, "employee_update_details.csv")

# ──────────────────────────────────────────────────────────────
# HEADERS we want in the CSV
# ──────────────────────────────────────────────────────────────
UPDATE_CSV_HEADER = [
    "first_name",
    "last_name",
    "email",
    "credit_officer_id",
    "employee_id",
    "esaf_employee_id",
    "role",
]


def _write_row(file: str, header: list, row: dict):
    """Append one row; write header only when file is new."""
    # Ensure the jmeter/results directory exists
    os.makedirs(os.path.dirname(file), exist_ok=True)
    
    write_header = not os.path.exists(file)
    with open(file, "a", newline="", encoding="utf-8") as f: # Uses "a" to append
        writer = csv.DictWriter(f, fieldnames=header)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def log_update_to_csv(
    create_data: dict,
    update_payload: dict,
    update_response: dict | None = None,
):
    """
    Extract required fields from three possible sources and write to CSV.
    """
    # ── Flatten update_response so nested "data" block is reachable ──
    resp_data: dict = {}
    if isinstance(update_response, dict):
        resp_data = (
            update_response.get("data")
            or update_response.get("result")
            or update_response
        )

    def pick(field: str):
        """Return first non-None value across response → payload → create."""
        for source in (resp_data, update_payload, create_data):
            if isinstance(source, dict):
                val = source.get(field)
                if val is not None:
                    return val
        return ""

    row = {
        "first_name"       : pick("first_name"),
        "last_name"        : pick("last_name"),
        "email"            : pick("email"),
        "credit_officer_id": pick("credit_officer_id"),
        "employee_id"      : pick("employee_id"),
        "esaf_employee_id" : pick("esaf_employee_id"),
        "role"             : pick("role"),
    }

    _write_row(CSV_UPDATE_DETAILS, UPDATE_CSV_HEADER, row)

    print(
        f"\n  📝 Update details saved to '{CSV_UPDATE_DETAILS}': "
        f"{row['first_name']} {row['last_name']} | "
        f"emp_id={row['employee_id']} | "
        f"esaf_id={row['esaf_employee_id']}"
    )