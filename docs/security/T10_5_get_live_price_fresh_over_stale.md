# Epic 10 T10.5 — `get_live_price` Malformed Entry Prod Review

**Scope:** `data/websocket_client.py::PolymarketWebSocket.get_live_price`.
**Inherited from:** Epic 9 post-audit finding (2026-04-22).
**Date:** 2026-04-22.
**Risk:** MED (prod-path, price-freshness doctrine).

## Problem (as of pre-T10.5)

```python
def get_live_price(self, token_id: str) -> Optional[float]:
    data = self.live_prices.get(token_id)
    if not data:
        return None
    try:
        entry_dt = datetime.fromisoformat(data["ts"].replace("Z", "+00:00"))
        if self._connected_since and entry_dt.timestamp() < self._connected_since:
            return None
        age = (datetime.now(timezone.utc) - entry_dt).total_seconds()
        _stale = float(os.getenv("WS_STALE_SEC", "60"))
        if age > _stale:
            return None
    except Exception:
        pass          # ← swallows KeyError / ValueError / AttributeError
    return data.get("price")   # ← serves the price ANYWAY
```

Eğer cache entry'de `'ts'` yoksa veya parse edilemeyen bir ISO
string'se, `except Exception: pass` ile susturuluyor ve method
`return data.get("price")`'a düşüyor — **freshness'i bilinmeyen bir
fiyat trade kararlarına serve ediliyor**. Bu davranış projenin
"fresh > stale" doktrinini ihlal ediyor.

**Doktrin (`.auto-memory/feedback_price_freshness_doctrine.md`):**
> Fresh > stale, cap + prune, no silent drops, reconnect backfill.
> "Fiyatlar hep güncel, eski eskide, şişmeyelim, hareketi kaçırmayalım."

## Decision

**(a) Tighten to return None on any freshness-check failure.**

Gerekçe:
1. **Fresh > stale:** bilinmeyen-taze bir fiyat, bilinen-olmayan bir
   fiyattan daha tehlikeli (trade tetikleyebilir ama hareketi kaçırmış
   olabilir).
2. **Normal flow sağlam:** `_handle_message` 3 cache-write site'ında
   (L333, L344, L353) `'ts'` HER ZAMAN `now_iso` ile set ediliyor.
   Missing-`ts` ancak corruption / gelecekteki refactor bug'ı / elle
   yazılmış test fixture yoluyla gerçekleşir.
3. **Epic 5 T5.4 pattern parity:** `_connected_since < entry.ts` zaten
   "invalidate" dönerek aynı fail-safe ilkesini uyguluyor. Malformed-`ts`
   aynı davranışı almalı.
4. **Safe fail mode:** None (no-trade) > stale-trade.

**(b) Reject decision:** defensive fallback zayıf bir önlem — prod
davranışının "fresh'lik bilmiyoruz ama olsun" olarak görünmesi kafa
karıştırıcı ve T10.1 audit doctrine'iyle çelişiyor.

## Fix

```python
except (KeyError, ValueError, TypeError, AttributeError) as e:
    # Epic 10 T10.5: malformed cache entry — freshness unknown.
    # KeyError: 'ts' missing. ValueError: bad ISO string.
    # TypeError: 'ts' not str. AttributeError: .replace on non-str.
    # None = safe fail (fresh > stale doctrine).
    logger.debug(
        "get_live_price malformed entry for %s: %s (%s); "
        "returning None", token_id, type(e).__name__, e)
    return None
return data.get("price")
```

Değişiklik:
- `except Exception: pass` → dar kapsamlı `(KeyError, ValueError,
  TypeError, AttributeError)`.
- Swallow + fall-through → `return None` + `logger.debug` breadcrumb.
- Downstream caller'lar zaten None'ı "trade atma" olarak yorumluyor.

## Regression test updates

Pre-T10.5: `test_missing_ts_returns_cached_price_no_crash` pinned the
old defensive fallback (`assert result == 0.50`).

Post-T10.5: 3 test (eski `test_missing_ts_*` yeniden adlandırıldı +
2 yeni test):
1. `test_missing_ts_returns_none_fresh_over_stale` — `'ts'` yok → None.
2. `test_malformed_ts_string_returns_none` — `"not-an-iso-date"` → None.
3. `test_non_string_ts_returns_none` — int epoch ts → None.

Üçü de `get_live_price`'ın hem `KeyError`/`ValueError`/`AttributeError`
yolunu hem de "fresh > stale" doktrinini pinliyor.

## Impact

- **Production:** Normal WS flow `'ts'` ALWAYS set ediyor → yeni
  davranış sıfır kez tetiklenir.
- **Edge cases:** Future refactor bug / corruption tespitini None +
  `logger.debug` breadcrumb ile destekler. Eskiden sessiz kayıptı.
- **Freshness doctrine:** Tam uyum. Aynı fonksiyonun Epic 5 T5.4
  reconnect-invalidate dalı zaten None dönüyor → bu değişiklik o
  tutarlılığı malformed-entry dalına da taşıyor.

## Regression baseline

Pre-T10.5: 731 pass + 8 skip + 0 fail
Post-T10.5: **733 pass + 8 skip + 0 fail** (+2 yeni test; 1 eski test
adı + assertion değişti ama pozisyonu aynı).

## Artifact

- `data/websocket_client.py:363-405` — updated `get_live_price`.
- `tests/integration/test_ws_reconnect_smoke.py:185-227` — 3 pin test.
- This document: `docs/security/T10_5_get_live_price_fresh_over_stale.md`.

## Forward work

- **Epic 11 T11.x:** generalize "fresh > stale" doktrinini tüm cache
  sınıflarına (orderbook cache, regime_detector cache, signal cache)
  aynı pattern ile uygula. `stats_utils`'taki benzer helper ile
  consolidate edilebilir.
- **Epic 11 pre-commit:** `except Exception: pass` grep'i — varsa
  T1.4 narrowing audit için PR yorumu.
