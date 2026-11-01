"""Entry point for `python -m emporio`."""


def main() -> None:
    from emporio.cli import app

    app()


if __name__ == "__main__":
    main()
