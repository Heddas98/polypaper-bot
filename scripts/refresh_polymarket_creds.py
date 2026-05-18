"""Polymarket CLOB API creds refresh — .env stored creds yenileme.

OP-02 (2026-05-18 ultra-audit): .env'deki POLYMARKET_API_KEY / API_SECRET /
PASSPHRASE stale — bot boot'ta `verify 401` alip her seferinde derive
fallback'e dusuyor. API key'in kendisi SAGLAM (Polymarket'te duruyor,
derive onu buluyor) — sadece .env'deki saklanan kopyalar eski.

Bu script POLYGON_PRIVATE_KEY'den gecerli L2 CLOB creds'i turetir, bir
authenticated cagri ile dogrular ve .env'in 3 satirini in-place gunceller.
Sonrasinda bot PATH 1 (stored creds) ile DOGRUDAN calisir — derive
fallback'e hic dusmez, her boot'taki 401 + Cloudflare-riskli derive adimi
ortadan kalkar.

Guvenlik: turetilen creds STDOUT'a BASILMAZ. Script yalnizca .env dosyasina
yazar; ekrana sadece api_key'in ilk 8 karakteri (teyit icin) gelir.

Kullanim:
    # repo .env'ini hedefler:
    py -3.11 scripts/refresh_polymarket_creds.py
    # acik .env yolu:
    py -3.11 scripts/refresh_polymarket_creds.py "C:\\path\\to\\.env"

API key rotate edilirse bu scripti tekrar calistirmak yeterli.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ENV = _ROOT / ".env"


def main() -> int:
    env_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _DEFAULT_ENV
    if not env_path.is_file():
        print(f"HATA: .env bulunamadi: {env_path}")
        return 1

    try:
        from dotenv import load_dotenv
    except ImportError:
        print("HATA: python-dotenv kurulu degil.")
        return 1
    load_dotenv(env_path)

    pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
    wallet = os.getenv("POLYGON_WALLET", "").strip()
    try:
        sig_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))
    except (TypeError, ValueError):
        sig_type = 2
    if not pk or not wallet:
        print("HATA: POLYGON_PRIVATE_KEY / POLYGON_WALLET .env'de eksik.")
        return 1

    try:
        from py_clob_client_v2 import ClobClient, TradeParams
    except ImportError as e:
        print(f"HATA: py-clob-client-v2 import edilemedi: {e}")
        return 1

    print("ClobClient kuruluyor (signature_type="
          f"{sig_type}, funder={wallet[:10]}...)")
    client = ClobClient(
        "https://clob.polymarket.com",
        key=pk,
        chain_id=137,
        signature_type=sig_type,
        funder=wallet,
    )

    print("L2 API creds turetiliyor (create_or_derive_api_key)...")
    try:
        creds = client.create_or_derive_api_key()
    except Exception as e:  # noqa: BLE001 — derive HTTP + signature can vary
        msg = str(e)
        if "403" in msg or "Cloudflare" in msg.lower():
            print("HATA: Cloudflare 403 — derive endpoint gecici olarak "
                  "blokladi. Birkac dakika sonra tekrar dene.")
        else:
            print(f"HATA: derive basarisiz ({type(e).__name__}): {e}")
        return 1

    client.set_api_creds(creds)

    print("Turetilen creds dogrulaniyor (get_trades)...")
    try:
        client.get_trades(TradeParams())
    except Exception as e:  # noqa: BLE001
        print(f"HATA: turetilen creds verify edilemedi "
              f"({type(e).__name__}): {e}")
        return 1

    api_key = str(getattr(creds, "api_key", "")).strip()
    api_secret = str(getattr(creds, "api_secret", "")).strip()
    api_pass = str(getattr(creds, "api_passphrase", "")).strip()
    if not (api_key and api_secret and api_pass):
        print("HATA: turetilen creds eksik alan iceriyor.")
        return 1

    # .env'i in-place guncelle — yalnizca 3 satir. Var olan satirlar
    # line-match ile degistirilir; yoksa sona eklenir. Lambda replacement
    # kullanildi cunku api_secret base64 (+/=) ozel karakter icerebilir.
    text = env_path.read_text(encoding="utf-8")
    updates = {
        "POLYMARKET_API_KEY": api_key,
        "POLYMARKET_API_SECRET": api_secret,
        "POLYMARKET_PASSPHRASE": api_pass,
    }
    for key, val in updates.items():
        pat = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pat.search(text):
            text = pat.sub(lambda _m, _v=f"{key}={val}": _v, text)
        else:
            text = text.rstrip("\n") + f"\n{key}={val}\n"
    env_path.write_text(text, encoding="utf-8")

    print()
    print(f"OK: .env guncellendi -> {env_path}")
    print(f"    POLYMARKET_API_KEY/SECRET/PASSPHRASE taze L2 creds ile "
          f"(api_key: {api_key[:8]}...).")
    print("    Bot restart edince PATH 1 (stored creds) ile dogrudan "
          "calisir; derive fallback + 401 adimi ortadan kalkar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
