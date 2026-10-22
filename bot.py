import logging
import os
import subprocess
import zipfile
import asyncio
from pathlib import Path
import aiosqlite
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8810752036:AAEWH9rf0JHuI-p0lKp7DTH85hlQN9AWCAI")
ADMIN_ID = 8137776838
DB = Path(__file__).parent / "bot.db"
BASE = Path(__file__).parent
BINARY = str(BASE / "zip-password-finder")
WORDLIST = str(BASE / "rockyou.txt")
DOWNLOADS = BASE / "downloads"
EXTRACTS = BASE / "extracted"
DOWNLOADS.mkdir(exist_ok=True)
EXTRACTS.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

ASK_UID, ASK_AMT, RENAME_PICK, RENAME_NEW = 1, 2, 3, 4
FORCE_TYPE, FORCE_VAL, FORCE_DEL = 5, 6, 7
QUOTE_KEY, QUOTE_DEL = 8, 9
BCAST_MSG = 10
DASH_KEY, DASH_VIDEO, DASH_DEL = 11, 12, 13
BTN_NAME, BTN_TEXT = 14, 15
EDIT_KEY, EDIT_TEXT, EDIT_VIDEO = 16, 17, 18
COLOR_PICK = 19
REDEEM_CREDITS, REDEEM_NAME, REDEEM_LIMIT = 20, 21, 22

CRACK_COST = 5
REFERRAL_REWARD = 1

DEFAULT = {
    "crack_file":  {"label": "- Crack File -",   "text": "Send me a .zip file to crack. {cost} credits per crack."},
    "refer_earn":  {"label": "- Refer & Earn -", "text": "Your referral link:\n{reflink}\n\nReferrals: {refs}"},
    "my_balance":  {"label": "- My Balance -",   "text": "Balance: {balance} credits"},
    "buy_credits": {"label": "- Buy Credits -",  "text": "Contact admin to buy credits."},
    "redeem_code": {"label": "- Redeem Code -",  "text": "Send your redeem code:"},
}
START_DEFAULT = "Hey {name}, welcome!\nBalance: {balance}"
FORCE_DEFAULT = "Join our channels first, then tap Verify."
REFER_DEFAULT = "New user joined!\nUser ID - {id}\nTotal balance - {balance}"

ADMIN_BUTTONS = {
    "Edit Name", "Name Change & Color", "Give Credits", "Remove Credits",
    "Edit Message", "Add Force", "Remove Force", "Quote ON", "Quote OFF",
    "Broadcast", "Add Dashboard", "Remove Dashboard", "Add Custom Button",
    "Make Redeem", "Back",
}

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, refs INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS menus (key TEXT PRIMARY KEY, label TEXT, text TEXT, color TEXT DEFAULT 'none');
        CREATE TABLE IF NOT EXISTS msgs (key TEXT PRIMARY KEY, text TEXT, video_id TEXT);
        CREATE TABLE IF NOT EXISTS force_channels (chat_id TEXT PRIMARY KEY, username TEXT, invite_link TEXT, is_private INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS quotes (key TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS dashboards (menu_key TEXT PRIMARY KEY, file_id TEXT);
        CREATE TABLE IF NOT EXISTS custom_buttons (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, text TEXT);
        CREATE TABLE IF NOT EXISTS redeem_codes (code TEXT PRIMARY KEY, credits INTEGER, uses_left INTEGER);
        CREATE TABLE IF NOT EXISTS redeem_used (code TEXT, user_id INTEGER, PRIMARY KEY (code, user_id));
        """)
        for k, v in DEFAULT.items():
            await db.execute("INSERT OR IGNORE INTO menus(key,label,text,color) VALUES(?,?,?,?)", (k, v["label"], v["text"], "none"))
        await db.execute("INSERT OR IGNORE INTO msgs(key,text,video_id) VALUES(?,?,NULL)", ("start", START_DEFAULT))
        await db.execute("INSERT OR IGNORE INTO msgs(key,text,video_id) VALUES(?,?,NULL)", ("force_join", FORCE_DEFAULT))
        await db.execute("INSERT OR IGNORE INTO msgs(key,text,video_id) VALUES(?,?,NULL)", ("refer_msg", REFER_DEFAULT))
        await db.commit()

async def ensure_user(uid):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
        await db.commit()

async def get_balance(uid):
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (uid,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else 0

async def get_refs(uid):
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT refs FROM users WHERE user_id=?", (uid,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else 0

async def change_balance(uid, amt):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET balance = MAX(0, balance + ?) WHERE user_id=?", (amt, uid))
        await db.commit()

async def add_ref(uid):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET refs = refs + 1 WHERE user_id=?", (uid,))
        await db.commit()

async def get_menus():
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM menus") as cur:
            return {r["key"]: dict(r) for r in await cur.fetchall()}

async def set_label(key, label):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE menus SET label=? WHERE key=?", (label, key))
        await db.commit()

async def set_color(key, color):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE menus SET color=? WHERE key=?", (color, key))
        await db.commit()

async def get_msg(key):
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM msgs WHERE key=?", (key,)) as cur:
            r = await cur.fetchone()
            return dict(r) if r else {"key": key, "text": "", "video_id": None}

async def set_msg_text(key, text):
    async with aiosqlite.connect(DB) as db:
        cur = await get_msg(key)
        await db.execute("INSERT OR REPLACE INTO msgs(key,text,video_id) VALUES(?,?,?)", (key, text, cur["video_id"]))
        await db.commit()

async def set_msg_video(key, video_id):
    async with aiosqlite.connect(DB) as db:
        cur = await get_msg(key)
        await db.execute("INSERT OR REPLACE INTO msgs(key,text,video_id) VALUES(?,?,?)", (key, cur["text"], video_id))
        await db.commit()

async def get_force():
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM force_channels") as cur:
            return [dict(r) for r in await cur.fetchall()]

async def add_force(chat_id, username, invite_link, is_private):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO force_channels(chat_id,username,invite_link,is_private) VALUES(?,?,?,?)", (chat_id, username, invite_link, is_private))
        await db.commit()

async def del_force(chat_id):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM force_channels WHERE chat_id=?", (chat_id,))
        await db.commit()

async def is_quoted(key):
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT 1 FROM quotes WHERE key=?", (key,)) as cur:
            return (await cur.fetchone()) is not None

async def set_quote(key, on):
    async with aiosqlite.connect(DB) as db:
        if on:
            await db.execute("INSERT OR IGNORE INTO quotes(key) VALUES(?)", (key,))
        else:
            await db.execute("DELETE FROM quotes WHERE key=?", (key,))
        await db.commit()

async def get_quotes():
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT key FROM quotes") as cur:
            return [r[0] for r in await cur.fetchall()]

async def get_all_uids():
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            return [r[0] for r in await cur.fetchall()]

async def set_dash(menu_key, file_id):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO dashboards(menu_key,file_id) VALUES(?,?)", (menu_key, file_id))
        await db.commit()

async def get_dash(menu_key):
    async with aiosqlite.connect(DB) as db:
        async with db.execute("SELECT file_id FROM dashboards WHERE menu_key=?", (menu_key,)) as cur:
            r = await cur.fetchone()
            return r[0] if r else None

async def del_dash(menu_key):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM dashboards WHERE menu_key=?", (menu_key,))
        await db.commit()

async def get_custom_buttons():
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM custom_buttons ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]

async def add_custom_button(name, text):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO custom_buttons(name,text) VALUES(?,?)", (name, text))
        await db.commit()

async def create_redeem(code, credits, uses):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR REPLACE INTO redeem_codes(code,credits,uses_left) VALUES(?,?,?)", (code, credits, uses))
        await db.commit()

async def try_redeem(code, uid):
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM redeem_codes WHERE code=?", (code,)) as cur:
            r = await cur.fetchone()
            if not r: return None, "invalid"
            if r["uses_left"] <= 0: return None, "exhausted"
            async with db.execute("SELECT 1 FROM redeem_used WHERE code=? AND user_id=?", (code, uid)) as cur:
                if await cur.fetchone(): return None, "already"
        await db.execute("UPDATE redeem_codes SET uses_left = uses_left - 1 WHERE code=?", (code,))
        await db.execute("INSERT INTO redeem_used(code,user_id) VALUES(?,?)", (code, uid))
        await db.commit()
    await change_balance(uid, r["credits"])
    return r["credits"], None

def style_for(color):
    return {"red": "danger", "green": "success", "blue": "primary"}.get(color)

async def menu_kb():
    menus = await get_menus()
    rows = [
        [KeyboardButton(menus["crack_file"]["label"], style=style_for(menus["crack_file"]["color"])), KeyboardButton(menus["my_balance"]["label"], style=style_for(menus["my_balance"]["color"]))],
        [KeyboardButton(menus["refer_earn"]["label"], style=style_for(menus["refer_earn"]["color"])), KeyboardButton(menus["buy_credits"]["label"], style=style_for(menus["buy_credits"]["color"]))],
        [KeyboardButton(menus["redeem_code"]["label"], style=style_for(menus["redeem_code"]["color"]))],
    ]
    for cb in await get_custom_buttons():
        rows.append([KeyboardButton(cb["name"])])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def admin_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("Edit Name")], [KeyboardButton("Name Change & Color")],
        [KeyboardButton("Give Credits"), KeyboardButton("Remove Credits")],
        [KeyboardButton("Edit Message")],
        [KeyboardButton("Add Force"), KeyboardButton("Remove Force")],
        [KeyboardButton("Quote ON"), KeyboardButton("Quote OFF")],
        [KeyboardButton("Broadcast")],
        [KeyboardButton("Add Dashboard"), KeyboardButton("Remove Dashboard")],
        [KeyboardButton("Add Custom Button")],
        [KeyboardButton("Make Redeem")], [KeyboardButton("Back")],
    ], resize_keyboard=True)

def cancel_kb(): return ReplyKeyboardMarkup([[KeyboardButton("/cancel")]], resize_keyboard=True)

def pick_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("Crack File")], [KeyboardButton("Refer & Earn")],
        [KeyboardButton("My Balance")], [KeyboardButton("Buy Credits")],
        [KeyboardButton("Redeem Code")], [KeyboardButton("/cancel")],
    ], resize_keyboard=True)

def dash_pick_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("Start")], [KeyboardButton("Crack File")], [KeyboardButton("Refer & Earn")],
        [KeyboardButton("My Balance")], [KeyboardButton("Buy Credits")],
        [KeyboardButton("Redeem Code")], [KeyboardButton("Force Join")], [KeyboardButton("/cancel")],
    ], resize_keyboard=True)

def color_pick_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Red", callback_data="color:red", style="danger")],
        [InlineKeyboardButton("Green", callback_data="color:green", style="success")],
        [InlineKeyboardButton("Blue", callback_data="color:blue", style="primary")],
    ])

def verify_kb(channels):
    rows = []
    for i, ch in enumerate(channels, 1):
        url = ch["invite_link"] if ch["is_private"] else f"https://t.me/{ch['username']}"
        rows.append([InlineKeyboardButton(f"Channel {i}", url=url, style="success")])
    rows.append([InlineKeyboardButton("Verify", callback_data="verify", style="danger")])
    return InlineKeyboardMarkup(rows)

async def render(key, user, ctx, from_menu=False):
    menus = await get_menus()
    text = menus[key]["text"] if from_menu else (await get_msg(key))["text"]
    bal, refs = await get_balance(user.id), await get_refs(user.id)
    reflink = f"https://t.me/{ctx.bot.username}?start=ref_{user.id}"
    
    text = text.replace("{name}", user.full_name or user.first_name or "")
    text = text.replace("{mention}", user.mention_html())
    text = text.replace("{id}", str(user.id))
    text = text.replace("{balance}", str(bal))
    text = text.replace("{refs}", str(refs))
    text = text.replace("{reflink}", reflink)
    text = text.replace("{link}", reflink)
    text = text.replace("{cost}", str(CRACK_COST))
    
    channels = await get_force()
    if "{channels}" in text:
        lines = []
        for i, ch in enumerate(channels, 1):
            url = ch["invite_link"] if ch["is_private"] else f"https://t.me/{ch['username']}"
            lines.append(f'<a href="{url}">Channel {i}</a>')
        text = text.replace("{channels}", "\n".join(lines) if lines else "No channels")
        
    if await is_quoted(key): 
        text = f"<blockquote>{text}</blockquote>"
        
    dash = await get_dash(key)
    if not dash and not from_menu:
        dash = (await get_msg(key)).get("video_id")
    return text, dash

async def send_render(target, key, user, ctx, reply_markup=None, from_menu=False):
    text, vid = await render(key, user, ctx, from_menu=from_menu)
    if vid: 
        return await target.reply_video(vid, caption=text, parse_mode="HTML", reply_markup=reply_markup)
    return await target.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

async def check_force(uid, bot):
    channels = await get_force()
    if not channels: return True, []
    missing = []
    for ch in channels:
        try:
            m = await bot.get_chat_member(chat_id=ch["chat_id"], user_id=uid)
            if m.status in ("left", "kicked"): missing.append(ch)
        except Exception: missing.append(ch)
    return len(missing) == 0, missing

def is_encrypted_zip(path):
    try:
        with zipfile.ZipFile(path) as zf: 
            return any(info.flag_bits & 0x1 for info in zf.infolist())
    except: return False

def crack_zip_blocking(zip_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.exists(BINARY): return None, "Cracker binary missing."
    if not os.path.exists(WORDLIST): return None, "rockyou.txt missing."
    try:
        result = subprocess.run([BINARY, zip_path, "-p", WORDLIST], capture_output=True, text=True, timeout=3600)
    except Exception as e: return None, f"Cracker error: {e}"
    pw = None
    for line in result.stdout.splitlines():
        if "found" in line.lower() or "password" in line.lower():
            if ":" in line:
                candidate = line.split(":")[-1].strip().strip("'\"")
                if candidate: pw = candidate; break
    if pw:
        try:
            with zipfile.ZipFile(zip_path) as zf: zf.extractall(path=out_dir, pwd=pw.encode("utf-8"))
            return pw, None
        except Exception as e: return pw, f"unzip failed: {e}"
    return None, "Password not found."

async def cmd_start(update, ctx):
    u = update.effective_user
    await ensure_user(u.id)
    if ctx.args and ctx.args[0].startswith("ref_"):
        try:
            ref = int(ctx.args[0].split("_", 1)[1])
            if ref != u.id:
                await change_balance(ref, REFERRAL_REWARD)
                await add_ref(ref)
                body = (await get_msg("refer_msg"))["text"].replace("{id}", str(u.id)).replace("{balance}", str(await get_balance(ref)))
                await ctx.bot.send_message(ref, body, parse_mode="HTML")
        except: pass
    ok, missing = await check_force(u.id, ctx.bot)
    if not ok:
        text, vid = await render("force_join", u, ctx)
        if vid: await update.message.reply_video(vid, caption=text, parse_mode="HTML", reply_markup=verify_kb(missing))
        else: await update.message.reply_text(text, parse_mode="HTML", reply_markup=verify_kb(missing))
        return
    await send_render(update.message, "start", u, ctx, reply_markup=await menu_kb())

async def verify_cb(update, ctx):
    q = update.callback_query; await q.answer(); u = q.from_user
    ok, missing = await check_force(u.id, ctx.bot)
    if not ok: return await q.answer("Join all channels first!", show_alert=True)
    await q.message.delete()
    await send_render(q.message, "start", u, ctx, reply_markup=await menu_kb())

async def cmd_admin(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        await update.message.reply_text("Admin Panel", reply_markup=admin_kb())

async def cmd_cancel(update, ctx):
    ctx.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
    return ConversationHandler.END

async def handle_zip_file(update, ctx):
    u = update.effective_user; await ensure_user(u.id)
    bal = await get_balance(u.id)
    if bal < CRACK_COST: 
        return await update.message.reply_text(f"Need {CRACK_COST} credits. You have {bal}.")
    
    doc = update.message.document
    safe = "".join(c for c in (doc.file_name or "f.zip") if c.isalnum() or c in "._-")
    if not safe.lower().endswith(".zip"): 
        return await update.message.reply_text("Only .zip files.")
        
    local = str(DOWNLOADS / f"{u.id}_{safe}")
    await update.message.reply_text("Downloading...")
    await (await doc.get_file()).download_to_drive(local)
    
    if not zipfile.is_zipfile(local): 
        return await update.message.reply_text("Not a valid zip.")
        
    if not is_encrypted_zip(local):
        out_dir = str(EXTRACTS / f"{u.id}_{safe}_x"); os.makedirs(out_dir, exist_ok=True)
        try:
            with zipfile.ZipFile(local) as zf: zf.extractall(out_dir)
        except Exception as e: 
            return await update.message.reply_text(f"Extract error: {e}")
        await change_balance(u.id, -CRACK_COST)
        await update.message.reply_text(f"Zip not encrypted. Extracted. -{CRACK_COST} credits.")
        for root, _, files in os.walk(out_dir):
            for f in files:
                try: await update.message.reply_document(open(os.path.join(root, f), "rb"), filename=f)
                except: pass
        return
        
    await update.message.reply_text(f"Encrypted. Cracking... -{CRACK_COST} credits.")
    out_dir = str(EXTRACTS / f"{u.id}_{safe}_cracked")
    pw, err = await asyncio.get_event_loop().run_in_executor(None, crack_zip_blocking, local, out_dir)
    await change_balance(u.id, -CRACK_COST)
    
    if pw:
        await update.message.reply_text(f"Password: `{pw}`\n-{CRACK_COST} credits.", parse_mode="Markdown")
        for root, _, files in os.walk(out_dir):
            for f in files:
                try: await update.message.reply_document(open(os.path.join(root, f), "rb"), filename=f)
                except: pass
    else: 
        await update.message.reply_text(f"Failed: {err}\n-{CRACK_COST} credits.")

async def menu_click(update, ctx):
    raw = update.message.text
    if raw in ADMIN_BUTTONS: return
    u = update.effective_user; await ensure_user(u.id); menus = await get_menus()
    for k, m in menus.items():
        if raw == m["label"]:
            if k == "redeem_code":
                await update.message.reply_text("Send your redeem code:", reply_markup=cancel_kb())
                ctx.user_data["awaiting_redeem"] = True
                return
            return await send_render(update.message, k, u, ctx, reply_markup=await menu_kb(), from_menu=True)
            
    if ctx.user_data.get("awaiting_redeem"):
        ctx.user_data.pop("awaiting_redeem", None)
        credits, err = await try_redeem(raw.strip(), u.id)
        if err == "invalid": await update.message.reply_text("Invalid code.", reply_markup=await menu_kb())
        elif err == "exhausted": await update.message.reply_text("Code expired.", reply_markup=await menu_kb())
        elif err == "already": await update.message.reply_text("Already used.", reply_markup=await menu_kb())
        else: await update.message.reply_text(f"Redeemed! +{credits} credits.", reply_markup=await menu_kb())
        return
        
    for cb in await get_custom_buttons():
        if raw == cb["name"]:
            bal = await get_balance(u.id)
            body = cb["text"].replace("{mention}", u.mention_html()).replace("{balance}", str(bal))
            await update.message.reply_text(body, parse_mode="HTML", reply_markup=await menu_kb())
            return

# ------------------ ADMIN FLOWS ------------------
async def rename_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        await update.message.reply_text("Which button?", reply_markup=pick_kb())
        return RENAME_PICK
    return ConversationHandler.END

async def rename_pick(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    mapping = {"Crack File": "crack_file", "Refer & Earn": "refer_earn", "My Balance": "my_balance", "Buy Credits": "buy_credits", "Redeem Code": "redeem_code"}
    key = mapping.get(update.message.text)
    if not key: return RENAME_PICK
    ctx.user_data["rename_key"] = key
    await update.message.reply_text("New name (exact):", reply_markup=cancel_kb())
    return RENAME_NEW

async def rename_save(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    await set_label(ctx.user_data["rename_key"], update.message.text)
    ctx.user_data.clear()
    await update.message.reply_text("Renamed.", reply_markup=admin_kb())
    return ConversationHandler.END

async def color_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        await update.message.reply_text("Which option?", reply_markup=pick_kb())
        return COLOR_PICK
    return ConversationHandler.END

async def color_pick_target(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    mapping = {"Crack File": "crack_file", "Refer & Earn": "refer_earn", "My Balance": "my_balance", "Buy Credits": "buy_credits", "Redeem Code": "redeem_code"}
    key = mapping.get(update.message.text)
    if not key: return COLOR_PICK
    ctx.user_data["color_key"] = key
    await update.message.reply_text("Pick a color:", reply_markup=color_pick_kb())
    return COLOR_PICK

async def color_cb(update, ctx):
    q = update.callback_query; await q.answer()
    color = q.data.split(":", 1)[1]; key = ctx.user_data.get("color_key")
    if not key: 
        await q.message.edit_text("No target.")
        return ConversationHandler.END
    await set_color(key, color)
    await q.message.edit_text(f"Done: {key} -> {color}")
    await ctx.bot.send_message(q.from_user.id, "Admin Panel", reply_markup=admin_kb())
    ctx.user_data.clear()
    return ConversationHandler.END

async def give_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        ctx.user_data["mode"] = "give"
        await update.message.reply_text("User ID:", reply_markup=cancel_kb())
        return ASK_UID
    return ConversationHandler.END

async def remove_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        ctx.user_data["mode"] = "remove"
        await update.message.reply_text("User ID:", reply_markup=cancel_kb())
        return ASK_UID
    return ConversationHandler.END

async def ask_uid(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    try: uid = int(update.message.text.strip())
    except: 
        await update.message.reply_text("Invalid ID.")
        return ASK_UID
    ctx.user_data["uid"] = uid
    await update.message.reply_text("Amount:", reply_markup=cancel_kb())
    return ASK_AMT

async def ask_amt(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    try: amt = int(update.message.text.strip())
    except: 
        await update.message.reply_text("Invalid amount.")
        return ASK_AMT
    uid = ctx.user_data["uid"]; await ensure_user(uid)
    if ctx.user_data["mode"] == "give": 
        await change_balance(uid, amt)
        await update.message.reply_text(f"Added {amt} to {uid}", reply_markup=admin_kb())
    else: 
        await change_balance(uid, -amt)
        await update.message.reply_text(f"Removed {amt} from {uid}", reply_markup=admin_kb())
    ctx.user_data.clear()
    return ConversationHandler.END

async def force_add_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        await update.message.reply_text("Public or Private?", reply_markup=ReplyKeyboardMarkup([["Public"], ["Private"], ["/cancel"]], resize_keyboard=True))
        return FORCE_TYPE
    return ConversationHandler.END

async def force_type(update, ctx):
    t = update.message.text
    if t == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    if t == "Public": 
        ctx.user_data["priv"] = 0
        await update.message.reply_text("Username (without @):", reply_markup=cancel_kb())
    elif t == "Private": 
        ctx.user_data["priv"] = 1
        await update.message.reply_text("Private channel ID only:", reply_markup=cancel_kb())
    else: return FORCE_TYPE
    return FORCE_VAL

async def force_val(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    priv = ctx.user_data["priv"]; text = update.message.text.strip()
    if priv:
        try: 
            chat = await ctx.bot.get_chat(chat_id=text)
            uname = chat.username or ""
            invite = await ctx.bot.export_chat_invite_link(chat_id=text)
        except Exception as e: 
            await update.message.reply_text(f"Cannot access. Bot must be admin.\n{e}")
            return FORCE_VAL
        await add_force(text, uname, invite, 1)
    else:
        uname = text.lstrip("@")
        try: 
            chat = await ctx.bot.get_chat(f"@{uname}")
            chat_id = str(chat.id)
        except Exception as e: 
            await update.message.reply_text(f"Cannot resolve: {e}")
            return FORCE_VAL
        await add_force(chat_id, uname, f"https://t.me/{uname}", 0)
    await update.message.reply_text("Channel added.", reply_markup=admin_kb())
    ctx.user_data.clear()
    return ConversationHandler.END

async def force_del_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID:
        chans = await get_force()
        if not chans: 
            await update.message.reply_text("No channels.", reply_markup=admin_kb())
            return ConversationHandler.END
        rows = [[KeyboardButton(c["username"] or c["chat_id"])] for c in chans]
        rows.append([KeyboardButton("/cancel")])
        await update.message.reply_text("Which to remove?", reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))
        return FORCE_DEL
    return ConversationHandler.END

async def force_del(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    for c in await get_force():
        if (c["username"] or c["chat_id"]) == update.message.text:
            await del_force(c["chat_id"])
            await update.message.reply_text("Removed.", reply_markup=admin_kb())
            return ConversationHandler.END
    return FORCE_DEL

async def quote_on_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        await update.message.reply_text("Key to quote ON:", reply_markup=cancel_kb())
        return QUOTE_KEY
    return ConversationHandler.END

async def quote_on_input(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    await set_quote(update.message.text.strip(), True)
    await update.message.reply_text("Quote ON.", reply_markup=admin_kb())
    return ConversationHandler.END

async def quote_off_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID:
        active = await get_quotes()
        if not active: 
            await update.message.reply_text("No active quotes.", reply_markup=admin_kb())
            return ConversationHandler.END
        rows = [[KeyboardButton(k)] for k in active]
        rows.append([KeyboardButton("/cancel")])
        await update.message.reply_text("Which to turn OFF?", reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True))
        return QUOTE_DEL
    return ConversationHandler.END

async def quote_off_pick(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    await set_quote(update.message.text.strip(), False)
    await update.message.reply_text("Quote OFF.", reply_markup=admin_kb())
    return ConversationHandler.END

async def bcast_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        await update.message.reply_text("Broadcast:", reply_markup=cancel_kb())
        return BCAST_MSG
    return ConversationHandler.END

async def bcast_send(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    sent = 0
    for uid in await get_all_uids():
        try: 
            await ctx.bot.send_message(uid, update.message.text, parse_mode="HTML")
            sent += 1
        except: pass
    await update.message.reply_text(f"Sent {sent}", reply_markup=admin_kb())
    return ConversationHandler.END

async def dash_add_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        await update.message.reply_text("Which menu?", reply_markup=dash_pick_kb())
        return DASH_KEY
    return ConversationHandler.END

async def dash_pick(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    mapping = {"Start": "start", "Crack File": "crack_file", "Refer & Earn": "refer_earn", "My Balance": "my_balance", "Buy Credits": "buy_credits", "Redeem Code": "redeem_code", "Force Join": "force_join"}
    key = mapping.get(update.message.text)
    if not key: return DASH_KEY
    ctx.user_data["dash_key"] = key
    await update.message.reply_text("Send video:", reply_markup=cancel_kb())
    return DASH_VIDEO

async def dash_video(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    if not update.message.video: 
        await update.message.reply_text("Send video.")
        return DASH_VIDEO
    await set_dash(ctx.user_data["dash_key"], update.message.video.file_id)
    await update.message.reply_text("Video set.", reply_markup=admin_kb())
    ctx.user_data.clear()
    return ConversationHandler.END

async def dash_del_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        await update.message.reply_text("Which menu?", reply_markup=dash_pick_kb())
        return DASH_DEL
    return ConversationHandler.END

async def dash_del_pick(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    mapping = {"Start": "start", "Crack File": "crack_file", "Refer & Earn": "refer_earn", "My Balance": "my_balance", "Buy Credits": "buy_credits", "Redeem Code": "redeem_code", "Force Join": "force_join"}
    key = mapping.get(update.message.text)
    if not key: return DASH_DEL
    await del_dash(key)
    await update.message.reply_text("Removed.", reply_markup=admin_kb())
    return ConversationHandler.END

async def btn_add_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        await update.message.reply_text("Button name:", reply_markup=cancel_kb())
        return BTN_NAME
    return ConversationHandler.END

async def btn_name_input(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    ctx.user_data["btn_name"] = update.message.text.strip()
    await update.message.reply_text("Message when clicked:", reply_markup=cancel_kb())
    return BTN_TEXT

async def btn_text_input(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    await add_custom_button(ctx.user_data["btn_name"], update.message.text)
    ctx.user_data.clear()
    await update.message.reply_text("Added.", reply_markup=admin_kb())
    return ConversationHandler.END

async def redeem_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID: 
        await update.message.reply_text("Credits:", reply_markup=cancel_kb())
        return REDEEM_CREDITS
    return ConversationHandler.END

async def redeem_credits_input(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    try: c = int(update.message.text.strip())
    except: 
        await update.message.reply_text("Send number.")
        return REDEEM_CREDITS
    ctx.user_data["redeem_credits"] = c
    await update.message.reply_text("Code name:", reply_markup=cancel_kb())
    return REDEEM_NAME

async def redeem_name_input(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    ctx.user_data["redeem_name"] = update.message.text.strip()
    await update.message.reply_text("Max users:", reply_markup=cancel_kb())
    return REDEEM_LIMIT

async def redeem_limit_input(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    try: lim = int(update.message.text.strip())
    except: 
        await update.message.reply_text("Send number.")
        return REDEEM_LIMIT
    await create_redeem(ctx.user_data["redeem_name"], ctx.user_data["redeem_credits"], lim)
    await update.message.reply_text(f"Redeem created:\nCode: {ctx.user_data['redeem_name']}\nCredits: {ctx.user_data['redeem_credits']}\nUses: {lim}", reply_markup=admin_kb())
    ctx.user_data.clear()
    return ConversationHandler.END

async def edit_entry(update, ctx):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text("Which to edit?", reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("Start")], [KeyboardButton("Force Join")], [KeyboardButton("Refer Msg")],
            [KeyboardButton("Crack File")], [KeyboardButton("Refer & Earn")],
            [KeyboardButton("My Balance")], [KeyboardButton("Buy Credits")],
            [KeyboardButton("Redeem Code")], [KeyboardButton("/cancel")],
        ], resize_keyboard=True))
        return EDIT_KEY
    return ConversationHandler.END

async def edit_pick(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    mapping = {"Start": ("start", False), "Force Join": ("force_join", False), "Refer Msg": ("refer_msg", False), "Crack File": ("crack_file", True), "Refer & Earn": ("refer_earn", True), "My Balance": ("my_balance", True), "Buy Credits": ("buy_credits", True), "Redeem Code": ("redeem_code", True)}
    if update.message.text not in mapping: return EDIT_KEY
    key, is_menu = mapping[update.message.text]
    ctx.user_data["edit_key"] = key
    ctx.user_data["edit_is_menu"] = is_menu
    cur = (await get_menus())[key]["text"] if is_menu else (await get_msg(key))["text"]
    await update.message.reply_text(f"Current:\n{cur}\n\nSend new text. Placeholders: {{name}} {{id}} {{balance}} {{refs}} {{reflink}} {{cost}} {{mention}} {{channels}}\n/skip keep, /cancel abort.", reply_markup=cancel_kb())
    return EDIT_TEXT

async def edit_text_input(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    if update.message.text != "/skip":
        key = ctx.user_data["edit_key"]
        if ctx.user_data["edit_is_menu"]:
            async with aiosqlite.connect(DB) as db: 
                await db.execute("UPDATE menus SET text=? WHERE key=?", (update.message.text, key))
                await db.commit()
        else: 
            await set_msg_text(key, update.message.text)
    await update.message.reply_text("Send video, or /skip.", reply_markup=ReplyKeyboardMarkup([["/skip"], ["/cancel"]], resize_keyboard=True))
    return EDIT_VIDEO

async def edit_video_input(update, ctx):
    if update.message.text == "/cancel": 
        await update.message.reply_text("Cancelled.", reply_markup=admin_kb())
        return ConversationHandler.END
    if update.message.text == "/skip": 
        await update.message.reply_text("Done.", reply_markup=admin_kb())
        ctx.user_data.clear()
        return ConversationHandler.END
    if update.message.video:
        key = ctx.user_data["edit_key"]
        if not ctx.user_data["edit_is_menu"]: 
            await set_msg_video(key, update.message.video.file_id)
        await update.message.reply_text("Video set.", reply_markup=admin_kb())
    ctx.user_data.clear()
    return ConversationHandler.END

async def post_init(app):
    await init_db()

def build_convs():
    A = filters.User(ADMIN_ID)
    return [
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Edit Name$") & A, rename_entry)], states={RENAME_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_pick)], RENAME_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_save)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Name Change & Color$") & A, color_entry)], states={COLOR_PICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, color_pick_target)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Give Credits$") & A, give_entry), MessageHandler(filters.Regex("^Remove Credits$") & A, remove_entry)], states={ASK_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_uid)], ASK_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_amt)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Add Force$") & A, force_add_entry)], states={FORCE_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, force_type)], FORCE_VAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, force_val)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Remove Force$") & A, force_del_entry)], states={FORCE_DEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, force_del)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Quote ON$") & A, quote_on_entry)], states={QUOTE_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, quote_on_input)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Quote OFF$") & A, quote_off_entry)], states={QUOTE_DEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, quote_off_pick)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Broadcast$") & A, bcast_entry)], states={BCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, bcast_send)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Add Dashboard$") & A, dash_add_entry)], states={DASH_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, dash_pick)], DASH_VIDEO: [MessageHandler(filters.VIDEO | filters.Regex("^/cancel$"), dash_video)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Remove Dashboard$") & A, dash_del_entry)], states={DASH_DEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, dash_del_pick)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Add Custom Button$") & A, btn_add_entry)], states={BTN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, btn_name_input)], BTN_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, btn_text_input)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Make Redeem$") & A, redeem_entry)], states={REDEEM_CREDITS: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_credits_input)], REDEEM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_name_input)], REDEEM_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_limit_input)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^Edit Message$") & A, edit_entry)], states={EDIT_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_pick)], EDIT_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_text_input)], EDIT_VIDEO: [MessageHandler((filters.VIDEO | filters.TEXT) & ~filters.COMMAND, edit_video_input)]}, fallbacks=[CommandHandler("cancel", cmd_cancel)]),
    ]