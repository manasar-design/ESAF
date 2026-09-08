#!/usr/bin/env python3
import csv
import string
import os

EMAIL_DOMAIN = "esaf-dev.esthenos.com"
TOTAL_USERS  = 20

OUTPUT_FILE  = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "data",
    "users.csv"
)

def generate_users():
    rows    = []
    letters = string.ascii_lowercase

    for i in range(TOTAL_USERS):
        char       = letters[i % 26]
        suffix     = char * 3
        first_name = f"raj{suffix}"
        last_name  = f"admin{suffix}"
        email      = f"{first_name}@{EMAIL_DOMAIN}"
        phone      = f"87655171{str(i + 1).zfill(2)}"
        emp_id     = str(101 + i)
        rows.append([first_name, last_name, email, phone, emp_id])

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    with open(OUTPUT_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "first_name", "last_name", "email",
            "phone", "esaf_employee_id"
        ])
        writer.writerows(rows)

    print(f"✅ Generated {TOTAL_USERS} users → {OUTPUT_FILE}")

if __name__ == "__main__":
    generate_users()
