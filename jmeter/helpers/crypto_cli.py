#!/usr/bin/env python3
"""
CLI bridge for JMeter to call encode_data / decode_data from main.py

Usage:
    python crypto_cli.py encode '{"key": "value"}'
    python crypto_cli.py decode 'encrypted_string_here'
"""
import sys
import os

# ============================================================
# PATH SETUP
# main.py is at: playwright-python/tests/main.py
# ============================================================
PROJECT_ROOT = "/home/lenovo/Desktop/Manasa/playwright-python/tests"

# Add to Python path
sys.path.insert(0, PROJECT_ROOT)

# Verify main.py exists
main_path = os.path.join(PROJECT_ROOT, "main.py")
if not os.path.exists(main_path):
    print(
        f"ERROR: main.py not found at {main_path}",
        file=sys.stderr
    )
    sys.exit(1)

# ============================================================
# IMPORT encode_data / decode_data from tests/main.py
# ============================================================
try:
    from main import encode_data, decode_data
except ImportError as e:
    print(f"ERROR: Could not import from main.py: {e}", file=sys.stderr)
    sys.exit(1)


# ============================================================
# CLI HANDLER
# ============================================================
def main():
    if len(sys.argv) < 3:
        print(
            "Usage: crypto_cli.py <encode|decode> <data>",
            file=sys.stderr
        )
        sys.exit(1)

    action = sys.argv[1].strip()
    data   = sys.argv[2].strip()

    try:
        if action == "encode":
            result = encode_data(data)
        elif action == "decode":
            result = decode_data(data)
        else:
            print(
                f"ERROR: Unknown action '{action}'. Use 'encode' or 'decode'",
                file=sys.stderr
            )
            sys.exit(1)

        # Print ONLY result to stdout (JMeter reads this)
        sys.stdout.write(str(result))
        sys.stdout.flush()

    except Exception as e:
        print(f"ERROR during {action}: {str(e)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()