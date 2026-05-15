# Polymarket Builder Program — Outreach & Eligibility Report

**Date:** 2026-05-11
**Goal:** Heddas joins the Polymarket Builder Program for the PolyPaper Bot project. This doc covers (1) DM draft for @mustafap0ly, (2) eligibility assessment, (3) signup walkthrough, (4) expected benefits & revenue.

---

## 1. Twitter DM to @mustafap0ly (English, ready to send)

> **Hi Mustafa,**
>
> Hope you're doing well. Quick intro: I've been building a Polymarket trading project solo for the past 2 months, working on it daily, and I went live on mainnet 3 days ago.
>
> **What I'm building (PolyPaper Bot):**
> A Telegram-controlled automation bot that runs end-to-end on Polymarket — paper trading, live trading (real pUSD via the V2 CLOB + Relayer for gasless tx), full backtest engine on real L2 history, strategy generation with AI-assisted parameter tuning (Claude + Groq), and a 9-gate risk safety layer. Currently focused on crypto Up/Down markets (BTC/ETH/SOL/XRP across 5m–24h timeframes) with 12 backtest strategies plus a Classic user-directed plugin and an AI Brain that proposes/tunes strategies under a human approval gate. Roadmap includes a web UI, multi-tenant SaaS layer, and expansion into geopolitics + sports markets.
>
> **Why I'm reaching out:**
> I'd love to get into the Polymarket Builder Program. A few things I'm trying to figure out:
>
> 1. Is the program a good fit for a solo dev running a single-tenant bot today (with a clear path to multi-tenant SaaS)? The docs hint that solo Relayer-API-key users qualify for unlimited tx, but I want to confirm before I attach a `builderCode` to my orders.
> 2. What benefits do builders actually see in practice — the docs mention weekly USDC rewards + grants for Verified tier; how does that work in real terms?
> 3. Is there a developer community I can join? I'd love to swap notes with others working on Polymarket integrations.
> 4. If there's anything like a points program or an upcoming token airdrop, it'd be great to be on the radar early — I've put 2 months of solo work into this already.
>
> **GitHub:** https://github.com/Heddas98/polypaper-bot
> (Repo is currently private — happy to grant temporary access if useful.)
>
> Any guidance you can share — or pointing me to the right person at Polymarket — would mean a lot. Thanks for your time!
>
> — Heddas

**Tone notes:**
- Professional but personal (mentions solo + 2 months + daily grind).
- Concrete (specific features, mainnet date, V2 stack).
- Asks 4 clear questions, not a wall of asks.
- Soft on the airdrop ask (mentions it last, frames as "early radar" not "give me tokens").
- Repo link included (Heddas: switch repo to public OR add temp collaborator before sending).

**Optional adds before sending:**
- Pin a tweet with a 30-sec screen recording of the bot + put the link in DM.
- Mention any common ground if known (e.g. shared mutual, country, prior interactions).

---

## 2. Builder Program — Eligibility (Heddas's bot)

### Verdict: **✅ Eligible today, can apply for Verified tier in 2-4 weeks once live volume accrues.**

Polymarket's Builder Program is open to *any* developer routing orders through their integration. The docs explicitly confirm solo / single-user setups qualify:

> *"If you're not routing orders for other users (wallets), you can get unlimited daily Relay transactions by obtaining a Relayer API key."*
> — [docs.polymarket.com/builders/tiers](https://docs.polymarket.com/builders/tiers)

PolyPaper Bot's current state matches the Unverified tier requirements out of the box:

| Requirement | PolyPaper status |
|---|---|
| Has API integration with CLOB | ✅ `py-clob-client-v2==1.0.0` |
| Uses gasless Relayer | ✅ `RELAYER_API_KEY` already configured (memory: `2026-05-05 Relayer Setup`) |
| Stable wallet setup | ✅ Gnosis Safe Proxy + 3-contract approvals done |
| Active trading | ✅ Mainnet shadow since 2026-05-03, live since 2026-05-09 |

**Gap analysis:**
- ⚠️ Orders currently **do not include `builderCode`** — Heddas needs to:
  1. Create builder profile at `polymarket.com/settings?tab=builder`
  2. Copy the bytes32 builder code
  3. Add `POLY_BUILDER_CODE` to `.env`
  4. Update `core/live_trader.py` to pass `builder_code=os.environ["POLY_BUILDER_CODE"]` on every order

---

## 3. Tiers — what you get at each level

| Feature | Unverified | **Verified** | Partner |
|---|---|---|---|
| Daily Relay tx limit | 100/day | **10,000/day** | Unlimited |
| API rate limits | Standard | Standard | Highest |
| Gasless trading | ✅ | ✅ | ✅ |
| Order attribution | ✅ | ✅ | ✅ |
| Builder fees (charge users) | ✅ | ✅ | ✅ |
| Builder Leaderboard visibility | — | ✅ | ✅ |
| Private Telegram channel | — | ✅ | ✅ |
| Engineering support | — | Standard | Elevated |
| Marketing support | — | Standard | Elevated |
| **Weekly USDC rewards** | — | ✅ (subject to approval) | ✅ |
| **Grants** | — | ✅ (subject to approval) | ✅ |
| Priority access | — | — | ✅ |

**How to upgrade Unverified → Verified (per docs):**
1. Run on Unverified for a while, demonstrate consistent volume.
2. Email `builder@polymarket.com` with:
   - Builder API Key
   - Use case description
   - Expected volume
   - Other relevant info (links, docs, decks)
3. Polymarket team reviews, responds in a few business days.

**Verdict for Heddas:** Apply for Verified after **2–4 weeks of accrued live trade volume** (post 2026-05-09 launch). Use case description writes itself given the feature set.

---

## 4. Builder Fees — direct revenue path

CLOB V2 introduced a fee layer that lets builders charge fees on orders routed through their app. **This is on top of platform fees, not instead of.**

| Fee | Max | Granularity |
|---|---|---|
| Taker fee (charged on aggressive orders) | 100 bps (1%) | 1 bp (0.01%) |
| Maker fee (charged on resting orders) | 50 bps (0.5%) | 1 bp (0.01%) |

**Example revenue (per docs):**
> A 1,000 pUSD taker buy routed through a builder charging 100 bps (1%) taker fee:
> `builder_fee = 1000 × 100 / 10000 = 10 pUSD`

**Rate change cooldown:** One rate change per 7 days, 3-day advance notice.

**PolyPaper Bot — practical revenue scenario:**
- Today: single user (Heddas), small live volume → builder fee ~$0 (charging yourself).
- After SaaS pivot (P2-01 backlog): N users each trading ~$100/day at 50bps maker + 100bps taker = ~$1.50/user/day → ~$45/user/month.
- Heddas keeps builder fees flat at 0 bps initially → switch on only when SaaS launches.

---

## 5. Concrete next steps (signup walkthrough)

### Phase A — Builder profile setup (10 minutes, do today)
1. Visit `polymarket.com/settings?tab=builder` in browser (logged in as `POLYGON_WALLET` proxy address).
2. Click **+ Create New** → generate Builder API key.
3. Set profile picture + builder name (suggestion: `PolyPaper Bot` or `Heddas / polyscout`).
4. Copy the bytes32 builder code (looks like `0x000...0001`).
5. Decide initial fee rates — recommendation: **`taker = 0 bps, maker = 0 bps`** until SaaS launch (no point taxing yourself).

### Phase B — Bot integration (1 minute, after Phase A)

**Good news — the bot already wires `POLYMARKET_BUILDER_CODE` natively!**

`core/live_trader.py:900` reads `POLYMARKET_BUILDER_CODE` from env and attaches
it to both `MarketOrderArgs` (primary) and `OrderArgs` (fallback) paths. All
orders — manual `/buy`, strategy-driven, AI Brain — route through the same
`_sync_order()`, so a single env-var flip wires builder attribution everywhere.

Add to `.env`:
```ini
POLYMARKET_BUILDER_CODE=0x...   # bytes32 from Phase A step 4 — that's it.
```

Restart the bot. Every order from now on carries your builder code onchain
in the V2 order struct's `builder` field.

**No code change required.** This was wired in as part of the 2026-05-05 V2
SDK migration, ready for activation.

### Phase C — Accrue volume (2–4 weeks, passive)
Bot keeps running normally. Builder Leaderboard tracks accrued volume automatically.

### Phase D — Apply for Verified (5 minutes when ready)
Email `builder@polymarket.com`:

> **Subject:** Verified Tier Application — PolyPaper Bot
>
> Hello Polymarket Builder team,
>
> I'd like to apply for Verified tier for the following builder profile:
>
> - Builder API Key: `[paste key]`
> - Builder Name: `PolyPaper Bot`
> - GitHub: https://github.com/Heddas98/polypaper-bot
> - Live since: 2026-05-09
> - Live volume to date: `[fill in once you have it]`
>
> **Use case:** PolyPaper Bot is a production-grade autonomous trading bot for Polymarket binary markets. It runs paper and live execution from a single Telegram chat surface, with a 6-signal fusion engine, 12 backtest strategies, AI-assisted strategy generation (Claude + Groq), full L2 backtest replay, 9-gate risk safety, and 3,500+ passing tests. Currently focused on crypto Up/Down markets, with roadmap expansion into geopolitics and sports.
>
> **Expected near-term volume:** `[best estimate based on first 2-4 weeks]`
>
> **Roadmap:**
> - Multi-tenant SaaS layer (web dashboard + Stripe billing)
> - Geopolitics 0-fee market discovery
> - Fill probability ML model
>
> Happy to share more details (repo access, design docs, decks) on request. Thanks for considering!
>
> — Heddas

---

## 6. Airdrop & token speculation

**Official docs say nothing about token/airdrop.** What we know:

- Polymarket has not announced a token.
- Verified-tier builders are eligible for **weekly USDC rewards + grants** — these are real fiat-equivalent revenue, not points.
- If Polymarket ever launches a token, builders with attribution history (i.e. orders with `builderCode` attached, on-chain in `OrderFilled` events) would be the natural priority list.

**Practical implication:** Attaching `builderCode` ASAP is the lowest-risk highest-upside move — it costs nothing, attributes every order to your builder profile in onchain events, and would be the obvious snapshot signal for any future incentive program.

---

## 7. Risks & cautions

- **Public visibility** — builder profile + fee rates are public on `builders.polymarket.com`. Pick a builder name you're comfortable being permanently associated with.
- **Fee rate cooldown** — one change per 7 days. Don't set 100 bps "to test" — start at 0.
- **Revocation clause** — Polymarket reserves the right to revoke builder ability "for any reason" including suspected wash/self-trading. Don't trade against yourself with builder code attached.
- **Onchain forever** — every order with your builder code is forever attributed onchain. Keep the code scoped to apps you actually own.

---

## 8. References (Polymarket docs, fetched via MCP 2026-05-11)

- [Builder Program Overview](https://docs.polymarket.com/builders/overview)
- [Tiers](https://docs.polymarket.com/builders/tiers)
- [API Keys / Builder Code](https://docs.polymarket.com/builders/api-keys)
- [Builder Fees](https://docs.polymarket.com/builders/fees)
- [Order Attribution](https://docs.polymarket.com/trading/clients/builder)
