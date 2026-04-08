#!/usr/bin/env python3
import os
import shlex
import sys


ALLOWED_COMMANDS = {
    "/usr/local/sbin/xray-add-client": {
        "flags": {
            "--email": True,
            "--uuid": True,
            "--name": True,
            "--flow": True,
        }
    },
    "/usr/local/sbin/xray-remove-client": {
        "flags": {
            "--uuid": True,
            "--email": True,
        }
    },
    "/usr/local/sbin/xray-build-vless-link": {
        "flags": {
            "--uuid": True,
            "--name": True,
            "--json": False,
        }
    },
    "/usr/local/sbin/xray-list-clients": {
        "flags": {
            "--json": False,
        }
    },
}

ALIASES = {
    os.path.basename(command): command for command in ALLOWED_COMMANDS
}


def fail(message: str, exit_code: int = 126) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def normalize_command(token: str) -> str:
    return ALIASES.get(token, token)


def validate_argv(argv: list[str]) -> None:
    command = normalize_command(argv[0])
    spec = ALLOWED_COMMANDS.get(command)
    if spec is None:
        fail(f"Command is not allowed: {argv[0]}")

    flags = spec["flags"]
    index = 1
    while index < len(argv):
        token = argv[index]
        if token not in flags:
            fail(f"Argument is not allowed for {command}: {token}")
        expects_value = flags[token]
        if expects_value:
            index += 1
            if index >= len(argv):
                fail(f"Missing value for {token}")
            if "\x00" in argv[index]:
                fail(f"Invalid value for {token}")
        index += 1


def main() -> None:
    original_command = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()
    if not original_command:
        fail("Interactive SSH is disabled for this account.", 127)

    try:
        argv = shlex.split(original_command)
    except ValueError as error:
        fail(f"Could not parse SSH_ORIGINAL_COMMAND: {error}")

    if not argv:
        fail("Empty SSH_ORIGINAL_COMMAND.", 127)

    validate_argv(argv)

    command = normalize_command(argv[0])
    os.execv(
        "/usr/bin/sudo",
        ["/usr/bin/sudo", "--non-interactive", "--", command, *argv[1:]],
    )


if __name__ == "__main__":
    main()
