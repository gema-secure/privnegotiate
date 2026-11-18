"""Generate portable local configuration with unique URL-safe credentials."""
import argparse
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    password = secrets.token_hex(24)
    values = {
        "INVITE_TOKEN_SECRET": secrets.token_hex(32),
        "MYSQL_PASSWORD": password,
        "MYSQL_ROOT_PASSWORD": secrets.token_hex(32),
        "DATABASE_URL": f"mysql+pymysql://privnegotiate:{password}@127.0.0.1:3306/privnegotiate?charset=utf8mb4",
    }
    template = (ROOT / ".env.example").read_text(encoding="utf-8")
    lines = []
    for line in template.splitlines():
        key = line.split("=", 1)[0]
        lines.append(f"{key}={values[key]}" if key in values else line)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Never silently replace a working deployment's credentials.
    with args.output.open("x", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines) + "\n")
    print("Configuration generated; keep it private and preserve it across restarts.")


if __name__ == "__main__":
    main()
