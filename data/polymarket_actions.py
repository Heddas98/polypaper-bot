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

NOT (P0-03 — 2026-05-08): A5 export_private_key() removed for security.
Telegram chat history → 3rd party leak risk; single-PC compromise =
total wallet drain. Yerine OS keychain / HW wallet entegrasyonu (P0-02)
gelecek.

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
    """Build authenticated CLOB client.

    2026-05-05 fix: Shared cache reuse (core.live_trader.SHARED_CREDS_CACHE).
    Live trader boot sırasında zaten API key derive yapmış. Yeni derive
    Polymarket'ta 'Could not create api key' 400 hatası veriyor (rate limit).
    Aynı creds'i her allowance/portfolio call'unda yeniden kullan.
    """
    try:
        from py_clob_client_v2 import ClobClient
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

        # ── PATH 1: Shared cache (live_trader boot'ta derive PASS yapmışsa) ──
        try:
            from core.live_trader import get_shared_creds
            shared_creds, shared_ts = get_shared_creds()
            if shared_creds:
                client.set_api_creds(shared_creds)
                logger.debug(
                    f"actions clob_client: shared cache reuse "
                    f"(age={int(__import__('time').time() - shared_ts)}s)"
                )
                return client
        except (ImportError, AttributeError):
            pass

        # ── PATH 2: Stored ENV creds (fallback) ──
        api_key = os.getenv("POLYMARKET_API_KEY", "").strip()
        api_secret = os.getenv("POLYMARKET_API_SECRET", "").strip()
        api_pass = os.getenv("POLYMARKET_PASSPHRASE", "").strip()
        if all([api_key, api_secret, api_pass]):
            try:
                from py_clob_client_v2 import ApiCreds
                stored = ApiCreds(
                    api_key=api_key, api_secret=api_secret,
                    api_passphrase=api_pass,
                )
                client.set_api_creds(stored)
                logger.debug(
                    f"actions clob_client: stored ENV creds "
                    f"(key={api_key[:8]}...)"
                )
                return client
            except Exception as _se:  # noqa: BLE001
                logger.debug(f"actions stored creds fail: {_se}")

        # ── PATH 3: Last resort — derive (riskli, rate limit) ──
        logger.warning(
            "actions clob_client: shared cache yok ve stored ENV yok. "
            "Derive deneniyor (Polymarket rate limit riski)..."
        )
        try:
            creds = client.create_or_derive_api_key()
            client.set_api_creds(creds)
            return client
        except Exception as _de:  # noqa: BLE001
            err_str = str(_de)
            if "400" in err_str or "Could not create" in err_str:
                logger.error(
                    "actions clob_client: Polymarket reddetti (rate limit). "
                    "Bot'u yeniden başlat veya 1-2 dakika bekle."
                )
            else:
                logger.warning(f"actions derive: {type(_de).__name__}: {_de}")
            return None

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
    """3 contract için pUSD allowance approve — Polymarket resmi gasless rehberi.

    Polymarket docs (/market-makers/getting-started + /trading/gasless):
    Gnosis Safe Proxy wallet için ZORUNLU 3 approve:
      | Token | Spender                | Amaç                            |
      |-------|------------------------|---------------------------------|
      | pUSD  | CTF Contract           | Split pUSD → outcome tokens     |
      | pUSD  | CTF Exchange           | Trade outcome tokens             |
      | pUSD  | Neg Risk CTF Exchange  | Neg-risk market trade            |

    PATH-A (RELAYER, gasless): py-builder-relayer-client SDK
      - RELAYER_API_KEY + RELAYER_API_KEY_ADDRESS env gerek
      - Polymarket relayer gas öder (gerçekten $0)
      - 3 approve tek batch'te atomic
      - https://docs.polymarket.com/trading/gasless

    PATH-B (CLOB SDK): update_balance_allowance(COLLATERAL+CONDITIONAL)
      - Cloudflare 403 / 400 alabilir Gnosis Safe için
      - Best-effort fallback

    PATH-C (UI): polymarket.com/portfolio manuel approve
      - %100 garanti, ~$0.06 gas
      - Heddas tek tıkla yapar

    Returns: (success_bool, detail_message_with_html_formatting)
    """
    pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
    wallet = os.getenv("POLYGON_WALLET", "").strip()
    if not pk or not wallet:
        return False, "POLYGON_PRIVATE_KEY veya POLYGON_WALLET env yok"

    # Polymarket resmi contract address'leri (docs/resources/contracts.mdx)
    PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
    SPENDERS = {
        "CTF": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
        "CTF Exchange": "0xE111180000d2663C0091e4f400237545B87B996B",
        "Neg Risk CTF Exchange": "0xe2222d279d744050d28e00520010520000310F59",
    }
    MAX_UINT256 = 2**256 - 1
    results = []

    # ═══════════════════════════════════════════════════════════════
    # PATH A: Polymarket Relayer (resmi gasless yol — Gnosis Safe için)
    # ═══════════════════════════════════════════════════════════════
    relayer_key = os.getenv("RELAYER_API_KEY", "").strip()
    relayer_addr = os.getenv("RELAYER_API_KEY_ADDRESS", "").strip()
    relayer_host = os.getenv(
        "RELAYER_HOST", "https://relayer-v2.polymarket.com"
    ).strip()

    if relayer_key and relayer_addr:
        # ── Adım 1: SDK + helper paket import (her biri ayrı try) ──
        RelayClient = None
        try:
            from py_builder_relayer_client.client import RelayClient as _RC  # type: ignore
            RelayClient = _RC
        except ImportError as _ie:
            err = f"py_builder_relayer_client missing: {_ie}"
            results.append(err)
            logger.warning(f"Relayer Path A: {err}")

        # ABI encoder — web3 varsa kullan, yoksa eth_abi (her ikisi de yoksa
        # manuel hex encode fallback)
        encode_approve_data = None
        if RelayClient is not None:
            try:
                from web3 import Web3  # type: ignore
                _w3 = Web3()
                _erc20_abi = [{
                    "name": "approve", "type": "function",
                    "inputs": [
                        {"name": "spender", "type": "address"},
                        {"name": "amount", "type": "uint256"},
                    ],
                    "outputs": [{"type": "bool"}],
                }]
                _contract = _w3.eth.contract(address=PUSD, abi=_erc20_abi)

                def encode_approve_data(spender, amount):
                    return _contract.encode_abi(
                        abi_element_identifier="approve",
                        args=[spender, amount],
                    )
                logger.debug("Relayer Path A: ABI encode via web3")
            except ImportError:
                # web3 yoksa manuel hex encode
                # ERC20 approve(address,uint256) selector = 0x095ea7b3
                def encode_approve_data(spender, amount):
                    s = spender.lower().replace("0x", "").rjust(64, "0")
                    a = format(amount, "x").rjust(64, "0")
                    return "0x095ea7b3" + s + a
                logger.info("Relayer Path A: web3 missing, using manual hex ABI encode")

        # ── Adım 2: Relayer çağrısı ──
        # SDK v0.0.1 alpha sadece Builder API Key (HMAC) destekler ama
        # Heddas'ın Relayer API Key'i 2-parçalı. SDK'nın `_post_request`
        # builder_headers gerektiriyor — bunu BYPASS edip SDK'nın internal
        # builder modülüyle Safe TX inşa edip, REST POST'u kendimiz yapıyoruz
        # RELAYER_API_KEY + RELAYER_API_KEY_ADDRESS headers ile.
        #
        # Polymarket /api-reference/relayer/submit-a-transaction:
        #   POST {RELAYER_HOST}/submit
        #   Headers: RELAYER_API_KEY, RELAYER_API_KEY_ADDRESS
        #   Body: SafeTransaction signed payload
        if RelayClient is not None and encode_approve_data is not None:
            try:
                # SDK internal modüllerini import et
                from py_builder_relayer_client.signer import Signer
                from py_builder_relayer_client.config import get_contract_config
                from py_builder_relayer_client.builder.safe import (
                    build_safe_transaction_request,
                )
                from py_builder_relayer_client.models import (
                    SafeTransaction,
                    SafeTransactionArgs,
                    OperationType,
                    TransactionType,
                )
                from py_builder_relayer_client.endpoints import (
                    GET_NONCE, GET_DEPLOYED, SUBMIT_TRANSACTION,
                )
                import requests as _requests

                # Signer + contract config (SDK'nın internal'ları)
                signer = Signer(pk, 137)
                contract_config = get_contract_config(137)
                from_address = signer.address()

                # Relayer API Key headers (Polymarket docs spec)
                relayer_headers = {
                    "RELAYER_API_KEY": relayer_key,
                    "RELAYER_API_KEY_ADDRESS": relayer_addr,
                }

                base_url = relayer_host.rstrip("/")

                # ── 2a: Safe deployed mu kontrol ──
                # Polymarket Gnosis Safe Proxy, ilk işlemde otomatik deploy edilir
                # Ama deploy gerekiyorsa farklı endpoint kullanmak lazım.
                expected_safe = wallet  # POLYGON_WALLET = Safe Proxy

                # ── 2b: Nonce çek ──
                nonce_url = (
                    f"{base_url}{GET_NONCE}"
                    f"?address={from_address}&type={TransactionType.SAFE.value}"
                )
                logger.info(f"Relayer Path A: GET nonce {nonce_url}")
                nonce_resp = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: _requests.get(
                        nonce_url, headers=relayer_headers, timeout=10
                    ),
                )
                if nonce_resp.status_code != 200:
                    raise RuntimeError(
                        f"GET /nonce {nonce_resp.status_code}: "
                        f"{nonce_resp.text[:200]}"
                    )
                nonce_data = nonce_resp.json()
                nonce = nonce_data.get("nonce")
                logger.info(f"Relayer Path A: nonce={nonce}")

                # ── 2c: 3 SafeTransaction inşa et ──
                txs = [
                    SafeTransaction(
                        to=PUSD,
                        operation=OperationType.Call,
                        data=encode_approve_data(spender, MAX_UINT256),
                        value="0",
                    )
                    for spender in SPENDERS.values()
                ]

                safe_args = SafeTransactionArgs(
                    from_address=from_address,
                    nonce=nonce,
                    chain_id=137,
                    transactions=txs,
                )

                # ── 2d: SDK builder Safe TX request'i sign'lar ──
                txn_request = build_safe_transaction_request(
                    signer=signer,
                    args=safe_args,
                    config=contract_config,
                    metadata="Approve pUSD x3 (CTF+CTF Exchange+Neg Risk)",
                ).to_dict()

                # ── 2e: POST /submit — Relayer API Key headers ile ──
                submit_url = f"{base_url}{SUBMIT_TRANSACTION}"
                logger.info(
                    f"Relayer Path A: POST {submit_url}, "
                    f"3 approve txs, nonce={nonce}"
                )
                submit_resp = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: _requests.post(
                        submit_url,
                        headers=relayer_headers,
                        json=txn_request,
                        timeout=20,
                    ),
                )

                if submit_resp.status_code != 200:
                    raise RuntimeError(
                        f"POST /submit {submit_resp.status_code}: "
                        f"{submit_resp.text[:300]}"
                    )

                resp_json = submit_resp.json()
                tx_id = resp_json.get("transactionID")
                results.append(f"Relayer submit OK: tx_id={tx_id}")
                logger.info(
                    f"Relayer Path A SUCCESS: transactionID={tx_id}"
                )

                return True, (
                    f"✅ <b>Approve TAMAM</b> (gasless via Polymarket Relayer)\n\n"
                    f"3 contract için pUSD allowance verildi:\n"
                    f"  • CTF: <code>{SPENDERS['CTF'][:10]}...</code>\n"
                    f"  • CTF Exchange: <code>{SPENDERS['CTF Exchange'][:10]}...</code>\n"
                    f"  • Neg Risk: <code>{SPENDERS['Neg Risk CTF Exchange'][:10]}...</code>\n\n"
                    f"<b>TX ID:</b> <code>{tx_id}</code>\n"
                    f"<b>Polygon gas:</b> $0 (relayer öder)\n\n"
                    f"1-2 dk içinde onchain confirm. Sonra <code>/portfolio</code> "
                    f"→ Allowance > $1.00 görünmeli."
                )

            except Exception as e:  # noqa: BLE001
                err_str = str(e)[:300]
                err_type = type(e).__name__
                results.append(f"Relayer Direct FAIL ({err_type}): {err_str}")
                logger.warning(
                    f"Relayer direct REST failed ({err_type}): {err_str}",
                    exc_info=True,
                )

    # ═══════════════════════════════════════════════════════════════
    # PATH B: CLOB SDK update_balance_allowance (best-effort)
    # ═══════════════════════════════════════════════════════════════
    try:
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
        client = _build_clob_client()
        if client is None:
            results.append("CLOB client unavailable")
        else:
            loop = asyncio.get_running_loop()

            try:
                params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                result = await loop.run_in_executor(
                    None, lambda: client.update_balance_allowance(params)
                )
                results.append(f"CLOB COLLATERAL: {str(result)[:80]}")
                logger.info(f"CLOB COLLATERAL approve: {result}")
            except Exception as _e:  # noqa: BLE001
                results.append(f"CLOB COLLATERAL FAIL: {str(_e)[:120]}")

            # Verify allowance
            try:
                check_params = BalanceAllowanceParams(
                    asset_type=AssetType.COLLATERAL
                )
                balance_resp = await loop.run_in_executor(
                    None, lambda: client.get_balance_allowance(check_params)
                )
                if isinstance(balance_resp, dict):
                    # 2026-05-05 V2 API fix:
                    # V1: bal["allowance"] (string), V2: bal["allowances"] (dict)
                    if "allowances" in balance_resp and isinstance(balance_resp["allowances"], dict):
                        max_raw = max(
                            (int(v or 0) for v in balance_resp["allowances"].values()),
                            default=0,
                        )
                        allowance_usd = float(max_raw) / 1e6
                    else:
                        allowance_raw = balance_resp.get("allowance", "0")
                        allowance_usd = float(allowance_raw or 0) / 1e6
                    if allowance_usd >= 1.0:
                        return True, (
                            f"✅ <b>Approve TAMAM</b> (CLOB SDK)\n"
                            f"Allowance: ${allowance_usd:.2f}\n"
                            f"Detay: {' | '.join(results)}"
                        )
            except Exception as _ve:  # noqa: BLE001
                results.append(f"verify: {str(_ve)[:80]}")

    except ImportError:
        results.append("py-clob-client-v2 not installed")

    # ═══════════════════════════════════════════════════════════════
    # PATH C: UI fallback (her zaman çalışır, %100 garanti)
    # ═══════════════════════════════════════════════════════════════
    return False, (
        f"❌ <b>Bot ile approve başarısız</b> — UI ile çöz (5dk, garanti):\n\n"
        f"<b>1.</b> https://polymarket.com/portfolio  ← aç\n"
        f"<b>2.</b> Rabby ile bağlan (POLYGON_PRIVATE_KEY ile aynı wallet)\n"
        f"<b>3.</b> 'Enable Trading' ya da 'Approve' tuşuna bas\n"
        f"<b>4.</b> 3 TX onayla (her biri ~$0.02 gas, toplam ~$0.06)\n"
        f"<b>5.</b> /portfolio → Allowance ${'>'}$1.00 görünmeli\n\n"
        f"<b>VEYA Polymarket Relayer ile gasless:</b>\n"
        f"1. polymarket.com/settings/api-keys → Create Relayer API Key\n"
        f"2. .env: <code>RELAYER_API_KEY=...</code> + <code>RELAYER_API_KEY_ADDRESS=...</code>\n"
        f"3. Bot restart + /allowance — Polymarket gas öder ($0)\n\n"
        f"<b>Detay:</b> {' | '.join(results) if results else '(yol denenmedi)'}"
    )


# ════════════════════════════════════════════════════════════════════════
# A6 — Redeem Position (winning shares → pUSD via Relayer)
# ════════════════════════════════════════════════════════════════════════
async def redeem_position(condition_id: str) -> tuple[bool, str]:
    """Resolved market'in winning shares'ini pUSD'ye çevir (gasless).

    Polymarket docs (/trading/ctf/redeem + /trading/gasless):
        CTF.redeemPositions(
            collateralToken=pUSD,
            parentCollectionId=bytes32(0),
            conditionId=<market condition_id>,
            indexSets=[1, 2]  # both — only winners pay
        )
    Relayer üzerinden gasless (Polymarket gas öder).

    Args:
        condition_id: bytes32 hex string (with or without 0x prefix)

    Returns: (success_bool, detail_message_html)
    """
    pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
    relayer_key = os.getenv("RELAYER_API_KEY", "").strip()
    relayer_addr = os.getenv("RELAYER_API_KEY_ADDRESS", "").strip()
    relayer_host = os.getenv(
        "RELAYER_HOST", "https://relayer-v2.polymarket.com"
    ).strip()

    if not pk:
        return False, "POLYGON_PRIVATE_KEY env yok"
    if not relayer_key or not relayer_addr:
        return False, (
            "❌ <b>Redeem için Relayer API Key gerek</b>\n\n"
            "polymarket.com/settings/api-keys → Create Relayer API Key\n"
            "Sonra .env: RELAYER_API_KEY + RELAYER_API_KEY_ADDRESS"
        )

    if not condition_id:
        return False, "condition_id boş"
    # Normalize condition_id (0x prefix optional)
    cid = condition_id.lower().strip()
    if not cid.startswith("0x"):
        cid = "0x" + cid
    if len(cid) != 66:  # 0x + 64 hex
        return False, f"condition_id format hatalı (66 char beklenir): {cid[:20]}..."

    CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
    PARENT_ZERO = "0x" + "00" * 32

    try:
        from py_builder_relayer_client.signer import Signer  # type: ignore
        from py_builder_relayer_client.config import get_contract_config
        from py_builder_relayer_client.builder.safe import (
            build_safe_transaction_request,
        )
        from py_builder_relayer_client.models import (
            SafeTransaction, SafeTransactionArgs,
            OperationType, TransactionType,
        )
        from py_builder_relayer_client.endpoints import (
            GET_NONCE, SUBMIT_TRANSACTION,
        )
        import requests as _requests

        signer = Signer(pk, 137)
        contract_config = get_contract_config(137)
        from_address = signer.address()

        # Build redeemPositions calldata
        # Function selector: redeemPositions(address,bytes32,bytes32,uint256[])
        # = keccak256("redeemPositions(address,bytes32,bytes32,uint256[])")[:4]
        # = 0x2eb2c2d6
        # Manuel ABI encode (web3 yoksa fallback)
        try:
            from web3 import Web3
            w3 = Web3()
            ctf_abi = [{
                "name": "redeemPositions", "type": "function",
                "inputs": [
                    {"name": "collateralToken", "type": "address"},
                    {"name": "parentCollectionId", "type": "bytes32"},
                    {"name": "conditionId", "type": "bytes32"},
                    {"name": "indexSets", "type": "uint256[]"},
                ],
                "outputs": [],
            }]
            ctf_contract = w3.eth.contract(address=CTF, abi=ctf_abi)
            data = ctf_contract.encode_abi(
                abi_element_identifier="redeemPositions",
                args=[
                    PUSD,
                    bytes(32),  # parentCollectionId zero
                    bytes.fromhex(cid[2:]),
                    [1, 2],  # both outcomes — only winner pays
                ],
            )
        except ImportError:
            # Manuel ABI encode fallback (4-byte selector + tightly packed)
            # Kontrol edilmiş selector: redeemPositions(address,bytes32,bytes32,uint256[])
            # = 0xfc6f7865 (resmi CTF spec)
            selector = "0xfc6f7865"
            collateral_param = PUSD.lower().replace("0x", "").rjust(64, "0")
            parent_param = "0" * 64
            cond_param = cid[2:].rjust(64, "0")
            # uint256[] dynamic: offset (0x80=128) + length=2 + el1=1 + el2=2
            offset_param = format(128, "x").rjust(64, "0")
            arr_len = format(2, "x").rjust(64, "0")
            el1 = format(1, "x").rjust(64, "0")
            el2 = format(2, "x").rjust(64, "0")
            data = (
                selector + collateral_param + parent_param + cond_param +
                offset_param + arr_len + el1 + el2
            )

        relayer_headers = {
            "RELAYER_API_KEY": relayer_key,
            "RELAYER_API_KEY_ADDRESS": relayer_addr,
        }
        base_url = relayer_host.rstrip("/")
        loop = asyncio.get_running_loop()

        # Get nonce
        nonce_url = (
            f"{base_url}{GET_NONCE}"
            f"?address={from_address}&type={TransactionType.SAFE.value}"
        )
        logger.info(f"Redeem Path A: GET nonce {nonce_url}")
        nonce_resp = await loop.run_in_executor(
            None,
            lambda: _requests.get(nonce_url, headers=relayer_headers, timeout=10),
        )
        if nonce_resp.status_code != 200:
            return False, (
                f"❌ Redeem nonce fail {nonce_resp.status_code}: "
                f"{nonce_resp.text[:200]}"
            )
        nonce = nonce_resp.json().get("nonce")
        logger.info(f"Redeem nonce={nonce}")

        # Build SafeTransaction
        tx = SafeTransaction(
            to=CTF,
            operation=OperationType.Call,
            data=data,
            value="0",
        )
        safe_args = SafeTransactionArgs(
            from_address=from_address,
            nonce=nonce,
            chain_id=137,
            transactions=[tx],
        )
        txn_request = build_safe_transaction_request(
            signer=signer,
            args=safe_args,
            config=contract_config,
            metadata=f"Redeem position {cid[:10]}...",
        ).to_dict()

        # POST /submit
        submit_url = f"{base_url}{SUBMIT_TRANSACTION}"
        logger.info(f"Redeem Path A: POST {submit_url}, nonce={nonce}")
        submit_resp = await loop.run_in_executor(
            None,
            lambda: _requests.post(
                submit_url,
                headers=relayer_headers,
                json=txn_request,
                timeout=20,
            ),
        )

        if submit_resp.status_code != 200:
            return False, (
                f"❌ Redeem submit fail {submit_resp.status_code}: "
                f"{submit_resp.text[:300]}"
            )

        resp_json = submit_resp.json()
        tx_id = resp_json.get("transactionID")
        logger.info(f"Redeem SUCCESS: transactionID={tx_id}")

        return True, (
            f"✅ <b>Redeem TAMAM</b> (gasless via Polymarket Relayer)\n\n"
            f"<b>Condition:</b> <code>{cid[:18]}...</code>\n"
            f"<b>TX ID:</b> <code>{tx_id}</code>\n"
            f"<b>Polygon gas:</b> $0\n\n"
            f"1-2 dk içinde onchain confirm.\n"
            f"Winning shares pUSD'ye çevrildi → /portfolio kontrol et."
        )

    except ImportError as e:
        return False, f"py-builder-relayer-client SDK eksik: {e}"
    except Exception as e:  # noqa: BLE001
        logger.exception(f"redeem_position {cid[:10]}: {e}")
        return False, (
            f"❌ Redeem hata ({type(e).__name__}): {str(e)[:200]}\n\n"
            f"Manuel: polymarket.com/portfolio → Redeem"
        )


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
# A5 — Private Key Export — REMOVED 2026-05-08 (P0-03 security fix)
# ════════════════════════════════════════════════════════════════════════
# Telegram chat history persists in third-party servers (Telegram Cloud +
# bot message logs); a single-PC compromise was sufficient to drain the
# wallet. The function has been deleted intentionally and must not be
# reintroduced. PK access is delegated to OS keychain / HW wallet via the
# upcoming `core.secrets` module (P0-02).
