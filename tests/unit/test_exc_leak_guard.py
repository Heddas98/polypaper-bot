"""T11.6-B regression -- check_exc_leak.py guard.

Verifies:
  1. Single-line `reply_text(..., esc(str(e))...)` caught
  2. Single-line slice variant `esc(str(e)[:N])` caught
  3. Multi-line reply_text with esc(str(e)) caught
  4. send_message variant caught
  5. logger.* contexts NOT flagged (server-side log OK)
  6. noqa: T11.6-OK suppresses
  7. Files outside telegram_bot/ skipped
  8. CLI --all on current handlers exits 0 (post-T11.6-B annotations)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_exc_leak.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_exc_leak as guard  # noqa: E402


def test_script_exists():
    assert SCRIPT.exists()


def test_single_line_reply_text_caught(tmp_path: Path):
    f = tmp_path / "h.py"
    f.write_text(
        "async def x(update, e):\n"
        "    await update.reply_text(f'X: {esc(str(e))}', parse_mode='HTML')\n"
    )
    viols = guard._check_file(f)
    assert len(viols) == 1, f"Expected 1, got {len(viols)}"


def test_slice_variant_caught(tmp_path: Path):
    """esc(str(e)[:N]) slice variant must also be caught."""
    f = tmp_path / "h.py"
    f.write_text(
        "async def x(update, e):\n"
        "    await update.reply_text(f'X: {esc(str(e)[:200])}', parse_mode='HTML')\n"
    )
    viols = guard._check_file(f)
    assert len(viols) == 1


def test_send_message_variant_caught(tmp_path: Path):
    f = tmp_path / "h.py"
    f.write_text(
        "async def x(ctx, e):\n"
        "    await ctx.bot.send_message(chat_id=1, text=f'{esc(str(e))}')\n"
    )
    viols = guard._check_file(f)
    assert len(viols) == 1


def test_logger_calls_not_flagged(tmp_path: Path):
    """Server-side logger.* calls should NOT trigger guard."""
    f = tmp_path / "h.py"
    f.write_text(
        "def x(e):\n"
        "    logger.error(f'fail: {esc(str(e))}')\n"
        "    logger.warning(f'fail: {esc(str(e))}')\n"
        "    logger.exception(f'fail: {esc(str(e))}')\n"
    )
    assert guard._check_file(f) == []


def test_noqa_suppresses(tmp_path: Path):
    f = tmp_path / "h.py"
    f.write_text(
        "async def x(update, e):\n"
        "    await update.reply_text(f'X: {esc(str(e))}',  # noqa: T11.6-OK\n"
        "        parse_mode='HTML')\n"
    )
    assert guard._check_file(f) == []


def test_multi_line_reply_text_caught(tmp_path: Path):
    f = tmp_path / "h.py"
    f.write_text(
        "async def x(update, e):\n"
        "    await update.reply_text(\n"
        "        f'X: {esc(str(e))}',\n"
        "        parse_mode='HTML')\n"
    )
    viols = guard._check_file(f)
    assert len(viols) >= 1, "Multi-line pattern must be caught"


def test_multi_line_with_noqa_suppressed(tmp_path: Path):
    f = tmp_path / "h.py"
    f.write_text(
        "async def x(update, e):\n"
        "    await update.reply_text(  # noqa: T11.6-OK\n"
        "        f'X: {esc(str(e))}',\n"
        "        parse_mode='HTML')\n"
    )
    assert guard._check_file(f) == []


def test_in_scope_classification():
    assert guard._is_in_scope("telegram_bot/handlers/foo.py")
    assert guard._is_in_scope("telegram_bot/bot.py")
    assert not guard._is_in_scope("core/engine.py")
    assert not guard._is_in_scope("tests/unit/test_x.py")


def test_cli_all_clean_on_current_handlers():
    """All current handlers should pass --all (post-T11.6-B annotations)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--all"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"--all should exit 0; got {result.returncode}.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
