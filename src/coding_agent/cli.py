"""Command-line interface for the Stage 0 project skeleton."""


def run_cli() -> None:
    """Display the Stage 0 prompt and accept one task."""
    print("Mini Coding Agent")
    print("Stage 0 - Project initialized")
    print()
    print("Type your task:")

    try:
        input("> ")
    except (EOFError, KeyboardInterrupt):
        print()
        print("Exiting Mini Coding Agent.")
        return

    print("Agent functionality is not implemented yet.")


def main() -> None:
    """Run the command-line interface."""
    run_cli()
