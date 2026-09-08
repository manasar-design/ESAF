import argparse
import base64
import sys
from aes_Cipher import AESGCMCipher

SECRET_KEY = "JSgFznpEFHQec+lDTOTgpXDdtorGzuyc"   # your actual key
AES_REQUIRED = True


def encode_data(data):
    if not AES_REQUIRED:
        return base64.b64encode(data.encode()).decode()

    aes = AESGCMCipher(SECRET_KEY.encode())
    return aes.encrypt(data)


def decode_data(data):
    if not AES_REQUIRED:
        return base64.b64decode(data.encode()).decode()

    aes = AESGCMCipher(SECRET_KEY.encode())
    return aes.decrypt(data)
    

def read_large_input(prompt):
    """
    Read data that may be too large or contain newlines for a single
    input() call. Supports either a file path or a multi-line paste
    terminated by EOF (Ctrl-D on Linux/macOS, Ctrl-Z+Enter on Windows).
    """
    source = input(f"{prompt}\n  [F]ile path or [P]aste text? ").strip().lower()

    if source in ("f", "file"):
        path = input("Path to file: ").strip()
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    print("Paste/type the data, then press Ctrl-D (Ctrl-Z+Enter on Windows) when done:")
    return sys.stdin.read()


def write_large_output(result):
    """
    Print short results directly; for large results, offer to save to a
    file instead of dumping a huge blob to the terminal.
    """
    if len(result) <= 2000:
        print(result)
        return

    print(f"Result is {len(result)} characters.")
    dest = input("Save to file (leave blank to print to terminal): ").strip()
    if dest:
        with open(dest, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"Saved to {dest}")
    else:
        print(result)


def run_cli():
    """
    Non-interactive entry point, for environments without a real
    keyboard stdin (e.g. running via `!` in Claude Code chat) or for
    scripting. Falls back to the interactive prompts when no
    subcommand is given and a keyboard is actually attached.
    """
    parser = argparse.ArgumentParser(description="AES-GCM encode/decode helper.")
    sub = parser.add_subparsers(dest="mode")

    for name in ("encode", "decode"):
        p = sub.add_parser(name)
        group = p.add_mutually_exclusive_group(required=True)
        group.add_argument("--text", help="Data to process, given directly.")
        group.add_argument("--file", help="Path to a file containing the data.")
        p.add_argument("--out", help="Path to write the result to (default: stdout).")

    args = parser.parse_args()

    if args.mode is None:
        if not sys.stdin.isatty():
            parser.print_help()
            sys.exit(1)
        choice = int(input("Select 1 for Encoder.\nSelect 2 for Decoder.\n"))
        if choice == 1:
            input_str = read_large_input("String to encode:")
            write_large_output(encode_data(input_str))
        else:
            input_str = read_large_input("String to decode:")
            write_large_output(decode_data(input_str))
        return

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            data = f.read()
    else:
        data = args.text

    result = encode_data(data) if args.mode == "encode" else decode_data(data)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(result)
        print(f"Saved to {args.out}")
    else:
        print(result)


if __name__ == "__main__":
    run_cli()