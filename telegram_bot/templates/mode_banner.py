"""Top-level Bot Mode Banner — Aşama 3.B.

Heddas direktifi (2026-04-29): "paper veya real diye seçilecek. paper ayrı
bir dünya real ayrı bi dünya gidecek."

Mode source of truth: ``LIVE_ENABLED`` env var (runtime mutable via
engine.live.toggle() veya /envt).

Mode mapping:
  LIVE_ENABLED=true  → REAL (💰 gerçek pUSD trade)
  LIVE_ENABLED=false → PAPER (📋 simülasyon)

Banner her major Telegram handler'ının başına eklenir, böylece kullanıcı
hangi modda olduğunu **anında** görür. Tutarlılık prensibi: mode bilgisi
TEK kaynaktan (LIVE_ENABLED) her yere yayılır.
"""

from __future__ import annotations

import os
from typing import Optional


def get_current_mode() -> str:
    """Returns 'paper' or 'real' based on LIVE_ENABLED env."""
    return "real" if os.getenv("LIVE_ENABLED", "false").lower() == "true" else "paper"


def is_paper_mode() -> bool:
    return get_current_mode() == "paper"


def is_real_mode() -> bool:
    return get_current_mode() == "real"


def format_banner(mode: Optional[str] = None, compact: bool = False) -> str:
    """Format mode banner HTML for Telegram messages.

    Args:
        mode: Override (for preview). Default = current LIVE_ENABLED state.
        compact: Single-line vs multi-line. Default multi (with separator).

    Returns:
        HTML string ending with \\n\\n (caller can prepend to message).
    """
    m = mode or get_current_mode()
    if m == "real":
        if compact:
            return "💰 <b>REAL MODE</b> | "
        return (
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "💰 <b>REAL MODE</b> · Gerçek pUSD\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
    if compact:
        return "📋 <b>PAPER MODE</b> | "
    return (
        "━━━━━━━━━━━━━━━━━━━━━\n" "📋 <b>PAPER MODE</b> · Simülasyon\n" "━━━━━━━━━━━━━━━━━━━━━\n\n"
    )


def format_mode_status_text() -> str:
    """Long-form mode status — for /mode command response."""
    if is_real_mode():
        return (
            "💰 <b>REAL MODE — Gerçek pUSD Trade</b>\n\n"
            "Bot Polymarket Proxy cüzdanından <b>gerçek</b> pUSD ile order "
            "place ediyor. Trade'ler Polygon network'te onchain.\n\n"
            "<b>Aktif kontroller:</b>\n"
            "• <code>LIVE_BUDGET</code> — toplam harcama tavanı\n"
            "• <code>LIVE_MAX_TRADE</code> — trade başı limit\n"
            "• <code>LIVE_STRATEGIES</code> — sadece whitelist çalışır\n"
            "• <code>LIVE_MAX_DAILY_LOSS</code> — günlük zarar kesimi\n\n"
            "<b>İlgili komutlar:</b>\n"
            "• /portfolio — Polymarket gerçek bakiye + pozisyonlar\n"
            "• /live — bot trader paneli (toggle)\n"
            "• /risk — risk durumu + günlük PnL\n\n"
            "<i>PAPER mode'a geçmek için aşağıdaki butona bas.</i>"
        )
    return (
        "📋 <b>PAPER MODE — Simülasyon</b>\n\n"
        "Bot virtual paper wallet ile çalışıyor. Hiçbir gerçek para "
        "hareketi yok. Strateji geliştirme + backtest + forward test için "
        "ideal.\n\n"
        "<b>Aktif:</b>\n"
        "• Tüm 15+ strateji (whitelist filtresi yok)\n"
        "• Virtual $10,000 başlangıç bakiyesi\n"
        "• Polymarket REAL fiyat akışı + gerçek market data\n"
        "• Trade'ler <code>executions</code> table'a yazılır\n\n"
        "<b>İlgili komutlar:</b>\n"
        "• /strategies — paper strateji yönetimi\n"
        "• /stats — paper performance\n"
        "• /backtest_v2 — geçmiş veri replay\n\n"
        "<i>REAL mode'a geçmek için: önce Polymarket'a deposit ($3+), "
        "sonra aşağıdaki butona bas.</i>"
    )
