"""
PolyPaper Bot - /start Handler
Mirrors Polyscout's onboarding: Terms → Accept → Wallet Creation → Deposit Instructions
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import Settings
from db.database import Database
from db.models import User, Wallet
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.start")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - show terms and conditions."""
    db: Database = context.bot_data["db"]
    context.bot_data["settings"]
    tg_user = update.effective_user

    # Check if user already exists
    user = await db.get_user_by_telegram_id(tg_user.id)

    if user and user.accepted_terms:
        # Returning user - show welcome back
        await db.get_active_wallet(user.id)

        text = (
            f"Good to have you here.\n\n"
            f"Your Polyscout setup is live and I just "
            f"spun up your first trading wallet so "
            f"you can start building strategies right away.\n"
            f"🏦 Wallet Address:\n"
            f"<code>PAPER-{user.id[:8]}</code>\n\n"
            f"Before you jump in, you need to "
            f"deposit USDC.e into your wallet. Our "
            f"relayer handles everything in the "
            f"background so your trades stay "
            f"gasless and smooth. Tap below for "
            f"deposit instructions."
        )
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💰 Deposit", callback_data="deposit_instructions")],
            ]
        )
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    # New user - show terms
    text = (
        "Alright, let us knock this out so you "
        "can get to the good part.\n\n"
        "PolyPaper is a paper trading simulator "
        "built on top of Polymarket data. It is not officially "
        "affiliated with Polymarket.\n\n"
        "By using this service, you confirm "
        "that you agree to Polymarket's Terms "
        "of Use and understand this is a "
        "<b>SIMULATION</b> using virtual funds only.\n\n"
        "⚠️ <b>No real money is involved.</b> All trades "
        "use simulated USDC for educational "
        "and research purposes.\n\n"
        "Website: polyscout.io\n"
        "Terms of Use: polymarket.com/tos"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("I Accept", callback_data="accept_terms")],
        ]
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def accept_terms_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'I Accept' button press - create user and wallet."""
    query = update.callback_query
    await query.answer()

    db: Database = context.bot_data["db"]
    settings: Settings = context.bot_data["settings"]
    tg_user = update.effective_user

    # Check if already exists
    user = await db.get_user_by_telegram_id(tg_user.id)

    if not user:
        # Create new user
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            accepted_terms=True,
        )
        user = await db.create_user(user)

        # Create default wallet with starting balance
        wallet = Wallet(
            user_id=user.id,
            label="primary",
            balance=settings.DEFAULT_BALANCE,
            is_primary=True,
        )
        wallet = await db.create_wallet(wallet)

        # Update user's default wallet
        user.default_wallet_id = wallet.id
        await db.update_user(user)

        logger.info(f"New user created: {tg_user.username} (ID: {tg_user.id})")
    else:
        user.accepted_terms = True
        await db.update_user(user)
        wallets = await db.get_wallets_by_user(user.id)
        wallet = wallets[0] if wallets else None

    balance = f"{wallet.balance:.4f}" if wallet else f"{settings.DEFAULT_BALANCE:.4f}"

    # Show wallet created message (like Polyscout)
    text = (
        f"Good to have you here.\n\n"
        f"Your PolyPaper setup is live and I just "
        f"spun up your first trading wallet so "
        f"you can start building strategies right "
        f"away.\n"
        f"🏦 Wallet Address:\n"
        f"<code>PAPER-{user.id[:20]}</code>\n\n"
        f"💰 Starting Balance: <b>{balance} USDC.e</b>\n\n"
        f"This is <b>simulated money</b> for paper trading. "
        f"You can add more virtual funds anytime.\n\n"
        f"Tap below to go to your dashboard and "
        f"get familiar with setting up your "
        f"trading strategy."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Dashboard", callback_data="show_dashboard")],
        ]
    )

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


async def deposit_instructions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show deposit instructions (add virtual funds)."""
    query = update.callback_query
    await query.answer()

    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)

    if not user:
        await query.edit_message_text("Önce /start komutunu kullanın.")
        return

    wallet = await db.get_active_wallet(user.id)
    balance = f"{wallet.balance:.4f}" if wallet else "0.0000"

    text = (
        f"💰 <b>Deposit Instructions</b>\n"
        f"Your wallet lives on PolyPaper (simulation), so send "
        f"virtual USDC.e to:\n"
        f"<code>PAPER-{user.id[:20]}</code>\n\n"
        f"Current Balance: <b>{balance} USDC.e</b>\n\n"
        f"To add virtual funds, use the command:\n"
        f"<code>/add_funds [amount]</code>\n\n"
        f"Example: <code>/add_funds 1000</code>\n\n"
        f"Minimum deposit: $10\n"
        f"Maximum deposit: $100,000\n\n"
        f"⚠️ This is simulation money only."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Dashboard", callback_data="show_dashboard")],
        ]
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


# ════════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 — merged from wallets.py
# ════════════════════════════════════════════════════════════════════════
from telegram_bot.banners import banner_referrals, banner_wallets


async def wallets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /wallets command."""
    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)
    if not user:
        await update.message.reply_text("Önce /start komutunu kullanın.")
        return
    await _send_wallets(update.message, db, user)


async def wallets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)
    if not user:
        return
    await _send_wallets(query.message, db, user)


async def _send_wallets(message, db: "Database", user):
    wallets = await db.get_wallets_by_user(user.id)
    text = "👛 <b>Your Wallets</b>\n\n"
    buttons = []
    for w in wallets:
        active = "✅" if w.is_primary else "•"
        text += f"{active} <b>{w.label}</b> – Balance: {w.balance:.4f} USDC.e\n"
        # P0-03 (2026-05-08): "🔑" wallet_key_ button removed — even though
        # the underlying handler was a placeholder, the icon misled users
        # into thinking they could surface a private key from the bot.
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{'✅ ' if w.is_primary else '👜 '}{w.label}",
                    callback_data=f"select_wallet_{w.id}",
                ),
                InlineKeyboardButton("ℹ", callback_data=f"wallet_info_{w.id}"),
                InlineKeyboardButton("🗑", callback_data=f"wallet_delete_{w.id}"),
            ]
        )
    text += "\nYou can create or import more wallets with the buttons below."
    buttons.append(
        [
            InlineKeyboardButton("➕ New Wallet", callback_data="new_wallet"),
            InlineKeyboardButton("📥 Import Wallet", callback_data="import_wallet"),
        ]
    )
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="show_dashboard")])
    keyboard = InlineKeyboardMarkup(buttons)
    banner = banner_wallets()
    await message.reply_photo(
        photo=banner,
        caption=text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def new_wallet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db: Database = context.bot_data["db"]
    context.bot_data["settings"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)
    if not user:
        return
    wallets = await db.get_wallets_by_user(user.id)
    label = f"wallet-{len(wallets) + 1}"
    wallet = Wallet(
        user_id=user.id,
        label=label,
        balance=0.0,
        is_primary=False,
    )
    await db.create_wallet(wallet)
    await query.message.reply_text(
        f"✅ New wallet <b>{esc(label)}</b> created!\n" f"Use /add_funds to add virtual USDC.",
        parse_mode="HTML",
    )


# ════════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 — merged from withdraw.py
# ════════════════════════════════════════════════════════════════════════
async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)
    if not user:
        await update.message.reply_text("Please use /start first.")
        return
    wallet = await db.get_active_wallet(user.id)
    balance = f"{wallet.balance:.4f}" if wallet else "0.0000"
    text = (
        f"💰 <b>Withdraw Virtual Funds</b>\n\n"
        f"Current Balance: <b>{balance} USDC.e</b>\n\n"
        f"To withdraw virtual funds:\n"
        f"<code>/withdraw_funds [amount]</code>\n\n"
        f"Example: <code>/withdraw_funds 500</code>\n\n"
        f"⚠️ This removes simulation money from your wallet."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
        ]
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def withdraw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)
    if not user:
        return
    wallet = await db.get_active_wallet(user.id)
    balance = f"{wallet.balance:.4f}" if wallet else "0.0000"
    text = (
        f"💰 <b>Withdraw Virtual Funds</b>\n\n"
        f"Current Balance: <b>{balance} USDC.e</b>\n\n"
        f"To withdraw, use:\n"
        f"<code>/withdraw_funds [amount]</code>"
    )
    await query.message.reply_text(text, parse_mode="HTML")


async def withdraw_funds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)
    if not user:
        await update.message.reply_text("Önce /start komutunu kullanın.")
        return
    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        await update.message.reply_text("Cüzdan bulunamadı.")
        return
    try:
        amount = float(context.args[0]) if context.args else 0
    except (ValueError, IndexError):
        await update.message.reply_text(
            "Usage: <code>/withdraw_funds [amount]</code>",
            parse_mode="HTML",
        )
        return
    if amount <= 0:
        await update.message.reply_text("Tutar pozitif olmalıdır. Tekrar deneyin.")
        return
    if amount > wallet.balance:
        await update.message.reply_text(f"Yetersiz bakiye. Mevcut: {wallet.balance:.4f} USDC.e")
        return
    new_balance = wallet.balance - amount
    await db.update_wallet_balance(wallet.id, new_balance)
    await update.message.reply_text(
        f"✅ <b>Withdrawal Successful</b>\n\n"
        f"Withdrawn: <b>{amount:.2f} USDC.e</b>\n"
        f"New Balance: <b>{new_balance:.4f} USDC.e</b>",
        parse_mode="HTML",
    )


# ════════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 — merged from referrals.py
# ════════════════════════════════════════════════════════════════════════
async def referrals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)
    if not user:
        await update.message.reply_text("Önce /start komutunu kullanın.")
        return
    await _send_referrals(update.message, user)


async def referrals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)
    if not user:
        return
    await _send_referrals(query.message, user)


async def _send_referrals(message, user):
    bot_info = await message.get_bot().get_me()
    bot_username = bot_info.username
    ref_link = f"https://t.me/{bot_username}?start={user.id[:8]}"
    text = (
        f"🤝 <b>Refer a Friend</b>\n\n"
        f"Invite your friends to trade on "
        f"PolyPaper and earn 50% of trading "
        f"fees. Every winning bet they make "
        f"drops passive rewards into your "
        f"pocket.\n\n"
        f"• Your link: {ref_link}\n"
        f"• Referrals: 0\n\n"
        f"💰 <b>Earnings</b>\n"
        f"• Total earned: 0.00 USDC.e\n"
        f"• Pending: 0.00 USDC.e\n"
        f"• Withdrawn: 0.00 USDC.e\n\n"
        f"Share your link and grow your squad."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Back", callback_data="show_dashboard")],
        ]
    )
    banner = banner_referrals()
    await message.reply_photo(
        photo=banner,
        caption=text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )
