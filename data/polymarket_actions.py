"""Polymarket Wallet Actions — Aşama 2.

Bu modül cüzdan yazma/aksiyon operasyonlarını içerir. Aşama 1 (read-only)
``polymarket_portfolio.py`` ile beraber kullanılır. Heddas direktifi
(2026-04-29): rate limit yok, double confirm yok, auto-delete yok.

Aksiyonlar:
  A1 — approve_allowance(): pUSD Exchange contract approve via SDK
  A2 — deposit_info(): Polygon adresi + QR generation hint
  A3 — withdraw_url(): Polymarket UI deeplink (direct on-chain withdraw
       Gnosis Safe execTransaction gerektiriyor — Aşama 3 backlog)
  A4 — wallet_import_steps(): .env edit talimatı
  A5 — export_private_key(): .env'den POLYGON_PRIVATE_KEY oku & döndür
       (no rate limit, no confirm, no auto-delete — Heddas direktifi)

Polymarket docs uyumlu:
  - https://docs.polymarket.com/trading/clients/l2#updatebalanceallowance
  - https://docs.polymarket.com/concepts/pusd
  - https://docs.polymarket.com/builders/fees#balance-checks
"""
from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse
from typing import Optional

logger = logging.getLogger("polypaper.data.polymarket_actions")

# Polygon network constants (docs/resources/contracts.mdx)
POLYGON_CHAIN_ID = 137
PUSD_CONTRACT = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
POLYGONSCAN_BASE = "https://polygonscan.com"

# Polymarket UI URLs
POLYMARKET_BASE = "https://polymarket.com"
POLYMARKET_PORTFOLIO_URL = f"{POLYMARKET_BASE}/portfolio"
POLYMARKET_WITHDRAW_URL = f"{POLYMARKET_BASE}/portfolio?withdraw=true"
POLYMARKET_DEPOSIT_URL = f"{POLYMARKET_BASE}/portfolio?deposit=true"

CLOB_HOST = os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com")


def _proxy_address() -> str:
    return os.getenv("POLYGON_WALLET", "").strip()


def _build_clob_client():
    """Build authenticated CLOB client (same pattern as polymarket_portfolio)."""
    try:
        from py_clob_client.client import ClobClient
        pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
        wallet = os.getenv("POLYGON_WALLET", "").strip()
        if not pk or not wallet:
            return None
        sig_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))
        client = ClobClient(
            CLOB_HOST,
            key=pk, chain_id=137,
            signature_type=sig_type,
            funder=wallet,
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        return client
    except (ImportError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"actions clob_client build: {type(e).__name__}: {e}")
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"actions clob_client unexpected: {type(e).__name__}: {e}")
        return None


# ════════════════════════════════════════════════════════════════════════
# A1 — Allowance Approve
# ════════════════════════════════════════════════════════════════════════
async def approve_allowance() -> tuple[bool, str]:
    """pUSD Exchange contract için allowance approve.

    py-clob-client 0.34.6: ``update_balance_allowance(BalanceAllowanceParams)``
    On-chain transaction (gas öder, Polygon network). Rabby PK ile imzalanır,
    Gnosis Safe Proxy execTransaction olarak gönderilir.

    Returns: (success_bool, detail_message)
    """
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
    except ImportError as e:
        return False, f"py-clob-client SDK not installed: {e}"

    client = _build_clob_client()
    if client is None:
        return False, "CLOB client oluşturulamadı (POLYGON_PRIVATE_KEY/WALLET eksik?)"

    try:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        loop = asyncio.get_running_loop()
        # update_balance_allowance: signed tx submission, returns tx hash or status
        result = await loop.run_in_executor(
            None, lambda: client.update_balance_allowance(params)
        )
        # Response shape varies; capture for diagnostic
        if isinstance(result, dict):
            tx_hash = result.get("transactionHash") or result.get("hash") or ""
            if tx_hash:
                msg = f"✅ Allowance approve gönderildi. TX: {tx_hash[:10]}..."
                logger.info(f"approve_allowance: tx={tx_hash}")
                return True, msg
        return True, f"✅ Allowance approve gönderildi. Detay: {str(result)[:100]}"
    except AttributeError as e:
        # SDK method name might differ across versions
        return False, f"SDK update_balance_allowance method bulunamadı: {e}"
    except (ValueError, TypeError, KeyError) as e:
        return False, f"approve fail ({type(e).__name__}): {e}"
    except Exception as e:  # noqa: BLE001
        logger.exception(f"approve_allowance unexpected: {e}")
        return False, f"approve unexpected ({type(e).__name__}): {str(e)[:200]}"


# ════════════════════════════════════════════════════════════════════════
# A2 — Deposit Info (QR + adres)
# ════════════════════════════════════════════════════════════════════════
def deposit_info() -> dict:
    """Deposit yapılacak Polygon adresi + Polymarket UI link + QR URL.

    Polymarket docs: deposit Polygon network'te USDC olarak gönderilir,
    pUSD'ye otomatik convert edilir. Address = Polymarket Gnosis Safe Proxy
    (POLYGON_WALLET .env değeri).

    QR data: standart EIP-3770 / web3 wallet URI ile uyumlu.
    """
    addr = _proxy_address()
    if not addr:
        return {
            "address": "",
            "error": "POLYGON_WALLET env var empty",
        }

    # EIP-681 URI for QR code (web3 wallets understand this)
    eip681_uri = f"ethereum:{addr}@{POLYGON_CHAIN_ID}"

    # QR generator service (Telegram bot inline image)
    # Using qrserver.com — public, no auth, GET-only
    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=400x400&data={urllib.parse.quote(eip681_uri)}"
    )

    return {
        "address": addr,
        "chain": "Polygon (chainId 137)",
        "tokens": ["USDC.e (Polygon Bridged USDC)", "auto-converts to pUSD"],
        "min_deposit": "$3.00 USDC (Polymarket minimum)",
        "polymarket_ui": POLYMARKET_DEPOSIT_URL,
        "polygonscan": f"{POLYGONSCAN_BASE}/address/{addr}",
        "eip681_uri": eip681_uri,
        "qr_image_url": qr_url,
    }


# ════════════════════════════════════════════════════════════════════════
# A3 — Withdraw via Polymarket UI
# ════════════════════════════════════════════════════════════════════════
def withdraw_info(amount: Optional[float] = None) -> dict:
    """Polymarket UI üzerinden withdraw — deeplink + pre-check rehber.

    Direct on-chain withdraw (Gnosis Safe execTransaction) Aşama 3 backlog.
    Şu an en güvenli yol Polymarket UI'dan tek tık.
    """
    addr = _proxy_address()

    return {
        "ui_url": POLYMARKET_WITHDRAW_URL,
        "polygonscan": f"{POLYGONSCAN_BASE}/address/{addr}" if addr else "",
        "method": "Polymarket UI (Gnosis Safe execTransaction)",
        "fee": "Polymarket gas fee öder (sponsor) — kullanıcı 0 fee",
        "min_withdraw": "$1.00 pUSD (Polymarket minimum)",
        "amount_requested": amount,
        "note": (
            "Direct on-chain withdraw bot'tan: Polymarket Gnosis Safe Proxy "
            "execTransaction Aşama 3'te eklenecek. Şu an UI link daha güvenli."
        ),
    }


# ════════════════════════════════════════════════════════════════════════
# A4 — Wallet Import Steps
# ════════════════════════════════════════════════════════════════════════
def wallet_import_steps() -> dict:
    """Yeni Polymarket cüzdanı bot'a tanıtma talimatı."""
    return {
        "step_1": (
            "Yeni Rabby/MetaMask wallet oluştur (private key dışa al)."
        ),
        "step_2": (
            "Bu wallet ile polymarket.com'a login → CREATE2 ile yeni "
            "Gnosis Safe Proxy oluşur. Profile/Wallet sayfasında "
            "'Deposit Address' = yeni proxy adresi."
        ),
        "step_3": (
            "Bot'u durdur (Ctrl+C). `.env` dosyasında değerleri güncelle:\n"
            "  POLYGON_PRIVATE_KEY=0x<yeni rabby private key>\n"
            "  POLYGON_WALLET=0x<yeni Polymarket Deposit Address>\n"
            "  CLOB_SIGNATURE_TYPE=2  # GNOSIS_SAFE\n"
            "Diğer satırları (TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY) bırak."
        ),
        "step_4": (
            "Bot'u yeniden başlat. /portfolio komutu yeni cüzdanın "
            "verilerini gösterir. Eski cüzdan referansları silinir."
        ),
        "warning": (
            "⚠ Bot tek aktif cüzdanı destekler. Multi-wallet için ayrı bir "
            "switcher feature gerek (backlog)."
        ),
    }


# ════════════════════════════════════════════════════════════════════════
# A5 — Private Key Export
# ════════════════════════════════════════════════════════════════════════
def export_private_key() -> dict:
    """`.env`'den POLYGON_PRIVATE_KEY oku ve döndür.

    Heddas 2026-04-29 direktifi: rate limit yok, double confirm yok,
    auto-delete yok. Bot dış dünyaya kapalı varsayılıyor — full PK görünür.

    Returns dict with pk + risk warnings + derived address (sanity check).
    """
    pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
    wallet = os.getenv("POLYGON_WALLET", "").strip()

    if not pk:
        return {
            "private_key": "",
            "error": "POLYGON_PRIVATE_KEY env var empty",
        }

    # Derive EOA address from PK (sanity check vs Rabby's actual address)
    derived_eoa = ""
    derive_error = ""
    try:
        from eth_account import Account
        acct = Account.from_key(pk)
        derived_eoa = acct.address
    except (ValueError, TypeError, ImportError) as e:
        derive_error = f"eth_account derive: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        derive_error = f"derive unexpected: {type(e).__name__}: {e}"

    return {
        "private_key": pk,                # Full PK, 64 hex with 0x prefix
        "polymarket_proxy": wallet,       # Gnosis Safe Proxy (funder)
        "derived_eoa": derived_eoa,       # Should match Rabby's EOA address
        "derive_error": derive_error,
        "risk_warnings": [
            "Bu private key Polymarket Proxy'nin OWNER'ı. Eline geçen herkes "
            "Polymarket bakiyene erişebilir.",
            "Telegram mesaj geçmişine kaydolur — bot'un mesaj log'u veya "
            "Telegram cloud backup'ında görünür.",
            "Screenshot alıp kimseyle paylaşma — özellikle 'PolyPaper Bot "
            "support' diyenlerle.",
            "Eğer PK sızdı şüphesi varsa: yeni Rabby wallet oluştur, "
            "Polymarket'a yeni login, fonları yeni proxy'e taşı, .env güncelle.",
        ],
    }
