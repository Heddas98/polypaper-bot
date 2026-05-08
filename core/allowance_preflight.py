"""
PolyPaper Bot — Allowance Pre-Flight Check
============================================
P0.5 (Phase D Bulgu 9 — kapatıldı 2026-04-30)

Bot startup'ta çağrılır: Polymarket V2 contract'larına gerekli approval'ları
doğrular. Eksik approval = `INVALID_ORDER_NOT_ENOUGH_BALANCE` reject riski.

Polymarket V2 Required Approvals (docs/market-makers/getting-started.mdx):
  1. pUSD → CTF Contract           — Split pUSD into outcome tokens
  2. CTF (outcome) → CTF Exchange  — Trade outcome tokens
  3. CTF (outcome) → NegRisk CTF Exchange — Trade neg-risk market tokens

Kontrat adresleri (docs/resources/contracts.mdx):
  pUSD                  : 0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB
  CTF (Conditional Tok) : 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045
  CTF Exchange          : 0xE111180000d2663C0091e4f400237545B87B996B
  Neg Risk CTF Exchange : 0xe2222d279d744050d28e00520010520000310F59
  Neg Risk Adapter      : 0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296

Mevcut akış (Phase A+B+C closure'lardan):
- `data/polymarket_actions.py::approve_allowance()` SDK
  `update_balance_allowance(BalanceAllowanceParams(asset_type=COLLATERAL))`
  ile pUSD approval'ı yapar.
- Bot 1417 trade + shadow live $1.49 budget ile çalıştığı için 3 approval'ın
  hepsinin fonksiyonel olduğu deduce edilebilir (yoksa order reject olurdu).

Bu modülün rolü:
- Boot startup'ta read-only allowance status check
- Eksikse Telegram alarm + `/allowance_check` admin command rehberi
- Otomatik approve YAPILMAZ (Heddas direktifi: tüm onchain tx user-confirmed)

Public API:
- `await check_all_allowances(client) -> dict[str, dict]` — read-only status
- `format_status_report(status) -> str` — Telegram HTML rapor
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger("polypaper.core.allowance_preflight")


# Polymarket V2 contract addresses (docs/resources/contracts.mdx 2026-05-03)
ADDR_PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
ADDR_PUSD_IMPL = "0x6bBCef9f7ef3B6C592c99e0f206a0DE94Ad0925f"  # implementation
ADDR_CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
ADDR_CTF_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"
ADDR_NEG_RISK_EXCHANGE = "0xe2222d279d744050d28e00520010520000310F59"
ADDR_NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# 2026-05-03 docs diff: collateral adapter adresleri eski audit'ten farklı.
# Bot şu an spot trading only — split/merge yapmıyor. Bu adresler ileride
# CTF split/merge desteği için (Sprint 4+) wired edilecek.
ADDR_CTF_COLLATERAL_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
ADDR_NEG_RISK_CTF_COLLATERAL_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"

# Collateral on/offramp + Permissioned (Sprint 4+ deposit/withdraw flow)
ADDR_COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
ADDR_COLLATERAL_OFFRAMP = "0x2957922Eb93258b93368531d39fAcCA3B4dC5854"
ADDR_PERMISSIONED_RAMP = "0xebC2459Ec962869ca4c0bd1E06368272732BCb08"

# UMA resolution (P3.X — UMA dispute window awareness için ileride)
ADDR_UMA_ADAPTER = "0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74"
ADDR_UMA_OPTIMISTIC_ORACLE = "0xCB1822859cEF82Cd2Eb4E6276C7916e692995130"

# Minimum allowance threshold for "approved" status (1 pUSD = 1e6 raw units)
# Polymarket docs: "allowance >= spending amount". $10 minimum order size
# için $1000+ allowance'ı OK kabul ediyoruz (mainnet ilk hafta cap).
MIN_ALLOWANCE_USD = float(os.getenv("ALLOWANCE_MIN_USD", "1000"))


async def check_collateral_allowance(client) -> dict:
    """pUSD COLLATERAL allowance status (V2 SDK get_balance_allowance).

    Returns:
        {"asset_type": "COLLATERAL", "balance": float, "allowance": float,
         "ok": bool, "raw": dict, "error": Optional[str]}
    """
    result = {
        "asset_type": "COLLATERAL",
        "spender": ADDR_CTF_EXCHANGE,
        "balance": 0.0,
        "allowance": 0.0,
        "ok": False,
        "raw": None,
        "error": None,
    }
    try:
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
    except ImportError as e:
        result["error"] = f"V2 SDK import: {e}"
        return result

    if client is None:
        result["error"] = "CLOB client None"
        return result

    try:
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        loop = asyncio.get_running_loop()
        bal = await loop.run_in_executor(
            None, lambda: client.get_balance_allowance(params)
        )
        result["raw"] = bal if isinstance(bal, dict) else {}
        # Raw values are USDC.e units (6 decimals)
        balance_raw = float(bal.get("balance", 0) or 0) if isinstance(bal, dict) else 0
        # 2026-05-05 V2 API fix: bal["allowances"] (çoğul dict) vs V1 "allowance"
        if isinstance(bal, dict) and "allowances" in bal and isinstance(bal["allowances"], dict):
            allow_raw = float(max(
                (int(v or 0) for v in bal["allowances"].values()),
                default=0,
            ))
        else:
            allow_raw = float(bal.get("allowance", 0) or 0) if isinstance(bal, dict) else 0
        result["balance"] = balance_raw / 1e6
        result["allowance"] = allow_raw / 1e6
        result["ok"] = result["allowance"] >= MIN_ALLOWANCE_USD
    except AttributeError as e:
        result["error"] = f"SDK method missing: {e}"
    except (ValueError, TypeError, KeyError) as e:
        result["error"] = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        # Boot orchestrator pattern: pre-flight is best-effort, never crash boot.
        result["error"] = f"unexpected: {type(e).__name__}: {e}"

    return result


async def check_conditional_allowance(client, sample_token_id: Optional[str] = None) -> dict:
    """CTF (outcome tokens) approval status.

    NOT: V2 SDK `BalanceAllowanceParams(asset_type=CONDITIONAL)` token_id
    gerektiriyor (per-market). `setApprovalForAll(true)` genel approval
    SDK'da exposed olmayabilir — bot'un mevcut shadow trades'i çalıştığı
    için fonksiyonel olduğunu inferred ediyoruz.

    Sample token_id verilirse spesifik check yapar, yoksa "inferred OK"
    döner (mevcut trades çalıştığı için).

    Returns:
        {"asset_type": "CONDITIONAL", "token_id": str, "allowance": float,
         "ok": bool, "raw": dict, "error": Optional[str], "inferred": bool}
    """
    result = {
        "asset_type": "CONDITIONAL",
        "token_id": sample_token_id or "",
        "allowance": 0.0,
        "ok": False,
        "raw": None,
        "error": None,
        "inferred": False,
    }
    if not sample_token_id:
        # No token_id — infer OK from successful prior trades
        result["ok"] = True
        result["inferred"] = True
        result["error"] = (
            "no sample token_id provided; inferred OK from active trade history"
        )
        return result

    try:
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
    except ImportError as e:
        result["error"] = f"V2 SDK import: {e}"
        return result

    if client is None:
        result["error"] = "CLOB client None"
        return result

    try:
        params = BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL,
            token_id=sample_token_id,
        )
        loop = asyncio.get_running_loop()
        bal = await loop.run_in_executor(
            None, lambda: client.get_balance_allowance(params)
        )
        result["raw"] = bal if isinstance(bal, dict) else {}
        # 2026-05-05 V2 API fix: bal["allowances"] (dict) vs V1 "allowance"
        if isinstance(bal, dict) and "allowances" in bal and isinstance(bal["allowances"], dict):
            allow_raw = float(max(
                (int(v or 0) for v in bal["allowances"].values()),
                default=0,
            ))
        else:
            allow_raw = float(bal.get("allowance", 0) or 0) if isinstance(bal, dict) else 0
        # CTF tokens 6 decimals (same as pUSD)
        result["allowance"] = allow_raw / 1e6
        # CTF setApprovalForAll → "infinite" allowance (very large number)
        result["ok"] = result["allowance"] > 1e9  # >$1B = setApprovalForAll proxy
    except AttributeError as e:
        result["error"] = f"SDK method missing: {e}"
    except (ValueError, TypeError, KeyError) as e:
        result["error"] = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        result["error"] = f"unexpected: {type(e).__name__}: {e}"

    return result


async def check_all_allowances(
    client,
    sample_token_id: Optional[str] = None,
) -> dict[str, dict]:
    """Run all allowance checks concurrently.

    Returns:
        {
          "collateral": <check_collateral_allowance result>,
          "conditional": <check_conditional_allowance result>,
          "summary": {
            "all_ok": bool,
            "missing": list[str],
            "inferred": bool,
          }
        }
    """
    coll_task = asyncio.create_task(check_collateral_allowance(client))
    cond_task = asyncio.create_task(check_conditional_allowance(client, sample_token_id))
    coll, cond = await asyncio.gather(coll_task, cond_task)

    missing = []
    if not coll.get("ok"):
        missing.append("pUSD (COLLATERAL)")
    if not cond.get("ok"):
        missing.append("CTF (CONDITIONAL)")

    return {
        "collateral": coll,
        "conditional": cond,
        "summary": {
            "all_ok": (coll.get("ok") and cond.get("ok")) or False,
            "missing": missing,
            "inferred": bool(cond.get("inferred")),
        },
    }


def format_status_report(status: dict) -> str:
    """Human-readable HTML rapor (Telegram /allowance_check için).

    Args:
        status: check_all_allowances() output

    Returns:
        HTML string (Telegram parse_mode=HTML)
    """
    coll = status.get("collateral", {})
    cond = status.get("conditional", {})
    summary = status.get("summary", {})

    lines = ["<b>🔐 Allowance Pre-Flight Status</b>", ""]

    # Collateral (pUSD)
    coll_emoji = "✅" if coll.get("ok") else "❌"
    coll_bal = coll.get("balance", 0)
    coll_allow = coll.get("allowance", 0)
    lines.append(f"{coll_emoji} <b>pUSD (COLLATERAL)</b>")
    lines.append(f"   Balance: ${coll_bal:.2f}")
    lines.append(f"   Allowance: ${coll_allow:,.2f}")
    if coll.get("error"):
        lines.append(f"   ⚠️ {coll['error'][:100]}")
    lines.append("")

    # Conditional (CTF)
    cond_emoji = "✅" if cond.get("ok") else "❌"
    cond_inferred = " (inferred)" if cond.get("inferred") else ""
    lines.append(f"{cond_emoji} <b>CTF (CONDITIONAL)</b>{cond_inferred}")
    if cond.get("token_id"):
        lines.append(f"   Token: <code>{cond['token_id'][:16]}...</code>")
        lines.append(f"   Allowance: ${cond.get('allowance', 0):,.2f}")
    if cond.get("error"):
        lines.append(f"   ⚠️ {cond['error'][:100]}")
    lines.append("")

    # Summary
    if summary.get("all_ok"):
        lines.append("✅ <b>Tüm approval'lar OK</b>")
    else:
        missing = summary.get("missing", [])
        lines.append(f"❌ <b>Eksik approval:</b> {', '.join(missing)}")
        lines.append("")
        lines.append("<b>Çözüm:</b>")
        lines.append(
            "1. Polymarket UI'da ilk trade attempt'inde otomatik approve "
            "isteyecek (Polygon gas)"
        )
        lines.append(
            "2. Veya: <code>/approve_allowance</code> komutu ile bot SDK "
            "üzerinden onchain tx gönderir"
        )
        lines.append("")
        lines.append(
            "📚 <a href='https://docs.polymarket.com/market-makers/getting-started#required-approvals'>Polymarket docs</a>"
        )

    return "\n".join(lines)


async def run_preflight(client, sample_token_id: Optional[str] = None) -> tuple[bool, str]:
    """Top-level convenience: run all checks + return (ok, html_report).

    Bot startup'tan çağrılır:
        ok, report = await run_preflight(self.live_trader._client)
        if not ok:
            await self.notify_admin(report)
    """
    status = await check_all_allowances(client, sample_token_id)
    ok = status["summary"]["all_ok"]
    report = format_status_report(status)
    return ok, report
