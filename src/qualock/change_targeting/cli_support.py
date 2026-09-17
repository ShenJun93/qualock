from typer import _click
from typer._click.exceptions import UsageError
from typer.core import TyperCommand


class _InputErrorExit3TyperCommand(TyperCommand):
    def parse_args(self, ctx: _click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except UsageError as exc:
            exc.exit_code = 3
            raise
