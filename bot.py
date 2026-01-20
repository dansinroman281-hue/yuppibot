
import discord
from discord import app_commands
from discord.ext import commands
import os
import sqlite3
import asyncio
import math
from dotenv import load_dotenv

# ================= CONFIG =================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

START_ELO = 1000
K_FACTOR = 32

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ================= DB =================
conn = sqlite3.connect("stats.db")
cursor = conn.cursor()

def init_db():
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            channel_id INTEGER PRIMARY KEY,
            game TEXT,
            p1 INTEGER,
            p2 INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS elo (
            user_id INTEGER,
            game TEXT,
            elo INTEGER,
            PRIMARY KEY (user_id, game)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_results (
            channel_id INTEGER PRIMARY KEY,
            reporter_id INTEGER,
            winner_id INTEGER,
            loser_id INTEGER,
            game TEXT
        )
    """)
    conn.commit()

def get_elo(uid, game):
    cursor.execute("SELECT elo FROM elo WHERE user_id=? AND game=?", (uid, game))
    row = cursor.fetchone()
    return row[0] if row else START_ELO

def set_elo(uid, game, elo):
    cursor.execute(
        "INSERT OR REPLACE INTO elo VALUES (?, ?, ?)",
        (uid, game, max(0, int(elo)))
    )

def calculate_elo(winner_elo, loser_elo):
    expected_win = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_loss = 1 - expected_win

    new_winner = winner_elo + K_FACTOR * (1 - expected_win)
    new_loser = loser_elo + K_FACTOR * (0 - expected_loss)

    return round(new_winner), round(new_loser)

# ================= GAMES =================
GAMES = [
    app_commands.Choice(name="CounterStrike2", value="CounterStrike2"),
    app_commands.Choice(name="DeadByDaylight", value="DeadByDaylight"),
    app_commands.Choice(name="CS:GO", value="CS:GO"),
]

# ================= EVENTS =================
@bot.event
async def on_ready():
    init_db()
    print(f"Logged in as {bot.user}")

    for guild in bot.guilds:
        for ch in ["challenge", "1x1", "find-party", "leaderboard"]:
            if not discord.utils.get(guild.text_channels, name=ch):
                await guild.create_text_channel(ch)

        for cat in ["Matches", "Parties"]:
            if not discord.utils.get(guild.categories, name=cat):
                await guild.create_category(cat)

    await bot.tree.sync()

# ================= CHALLENGE =================
@bot.tree.command(name="challenge", description="Создать 1x1 челлендж")
@app_commands.choices(game=GAMES)
@app_commands.describe(
    opponent="Конкретный оппонент (необязательно)",
    anyone="Если True — любой может принять"
)
async def challenge(
    interaction: discord.Interaction,
    game: app_commands.Choice[str],
    opponent: discord.Member | None = None,
    anyone: bool = False
):
    one_x_one = discord.utils.get(interaction.guild.text_channels, name="1x1")
    if not one_x_one:
        await interaction.response.send_message(
            "❌ Канал #1x1 не найден",
            ephemeral=True
        )
        return

    # Проверки
    if opponent and anyone:
        await interaction.response.send_message(
            "❌ Выберите либо opponent, либо anyone",
            ephemeral=True
        )
        return
    if not opponent and not anyone:
        await interaction.response.send_message(
            "❌ Нужно указать opponent или anyone=True",
            ephemeral=True
        )
        return
    if opponent == interaction.user:
        await interaction.response.send_message(
            "❌ Нельзя вызвать самого себя",
            ephemeral=True
        )
        return

    # Создаём текст для сообщения
    if anyone:
        text = (
            f"⚔ **OPEN 1x1 CHALLENGE**\n"
            f"{interaction.user.mention} ищет соперника в **{game.value}**\n"
            f"Нажми ✅ чтобы принять"
        )
    else:
        text = (
            f"⚔ **1x1 CHALLENGE**\n"
            f"{interaction.user.mention} бросает вызов {opponent.mention}\n"
            f"Игра: **{game.value}**\n"
            f"Нажми ✅ чтобы принять"
        )

    msg = await one_x_one.send(text)
    await msg.add_reaction("✅")

    # 🔒 авто-блок на повторное принятие
    accepted = False

    def check(r, user):
        nonlocal accepted
        if accepted:
            return False
        if r.message.id != msg.id or str(r.emoji) != "✅":
            return False
        if user == interaction.user:
            return False
        if opponent and user != opponent:
            return False
        return True

    try:
        reaction, acceptor = await bot.wait_for("reaction_add", timeout=300, check=check)
    except asyncio.TimeoutError:
        await msg.edit(content="⌛ Челлендж истёк")
        await msg.clear_reactions()
        return

    # Заблокируем дальнейшие реакции
    accepted = True
    await msg.clear_reactions()
    await msg.edit(content=text + "\n\n✅ **Челлендж принят**")

    # Создаем приватный канал
    category = discord.utils.get(interaction.guild.categories, name="Challenges")
    if not category:
        category = await interaction.guild.create_category("Challenges")

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        acceptor: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        bot.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }

    channel = await interaction.guild.create_text_channel(
        f"challenge-{game.value}-{interaction.user.name}-vs-{acceptor.name}",
        category=category,
        overwrites=overwrites
    )

    # Сохраняем матч
    cursor.execute(
        "INSERT INTO matches VALUES (?, ?, ?, ?)",
        (channel.id, game.value, interaction.user.id, acceptor.id)
    )
    conn.commit()

    await channel.send(
        f"🔥 **Челлендж принят!**\n"
        f"{interaction.user.mention} vs {acceptor.mention}\n"
        f"Игра: **{game.value}**\n\n"
        f"Отчёт: `/iwon` / `/ilost`\n"
        f"Завершить: `/end`"
    )

    await interaction.response.send_message(
        f"✅ Челлендж принят: {channel.mention}",
        ephemeral=True
    )


    # 🔒 БЛОКИРУЕМ ПОВТОРНОЕ ПРИНЯТИЕ
    accepted = True
    await msg.clear_reactions()
    await msg.edit(content=text + "\n\n✅ **Челлендж принят**")

    # Создаём канал
    category = discord.utils.get(interaction.guild.categories, name="Challenges")
    if not category:
        category = await interaction.guild.create_category("Challenges")

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        acceptor: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        bot.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }

    channel = await interaction.guild.create_text_channel(
        name=f"challenge-{game.value}-{interaction.user.name}-vs-{acceptor.name}",
        category=category,
        overwrites=overwrites
    )

    cursor.execute(
        "INSERT INTO matches VALUES (?, ?, ?, ?)",
        (channel.id, game.value, interaction.user.id, acceptor.id)
    )
    conn.commit()

    await channel.send(
        f"🔥 **Челлендж принят!**\n"
        f"{interaction.user.mention} vs {acceptor.mention}\n"
        f"Игра: **{game.value}**\n\n"
        f"Отчёт: `/iwon` / `/ilost`\n"
        f"Завершить: `/end`"
    )

    await interaction.response.send_message(
        f"✅ Челлендж принят: {channel.mention}",
        ephemeral=True
    )


# ================= RESULT CONFIRM =================
async def submit_result(interaction, is_winner: bool):
    cursor.execute("SELECT game, p1, p2 FROM matches WHERE channel_id=?", (interaction.channel.id,))
    match = cursor.fetchone()
    if not match:
        await interaction.response.send_message("Матч не найден", ephemeral=True)
        return

    game, p1, p2 = match
    reporter = interaction.user.id

    winner = reporter if is_winner else (p2 if reporter == p1 else p1)
    loser = p2 if winner == p1 else p1
    opponent = loser if reporter == winner else winner

    cursor.execute(
        "INSERT OR REPLACE INTO pending_results VALUES (?, ?, ?, ?, ?)",
        (interaction.channel.id, reporter, winner, loser, game)
    )
    conn.commit()

    msg = await interaction.channel.send(
        f"<@{opponent}> подтверди результат\n"
        f"🏆 Победитель: <@{winner}>\n"
        f"✅ / ❌"
    )
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    def check(r, u):
        return u.id == opponent and r.message.id == msg.id and str(r.emoji) in ("✅", "❌")

    reaction, _ = await bot.wait_for("reaction_add", check=check)

    if str(reaction.emoji) == "❌":
        await interaction.channel.send("❌ Результат отклонён")
        return

    w_elo = get_elo(winner, game)
    l_elo = get_elo(loser, game)

    new_w, new_l = calculate_elo(w_elo, l_elo)

    set_elo(winner, game, new_w)
    set_elo(loser, game, new_l)

    conn.commit()

    await interaction.channel.send(
        f"✅ **ELO обновлено**\n"
        f"<@{winner}>: {w_elo} → {new_w}\n"
        f"<@{loser}>: {l_elo} → {new_l}"
    )

@bot.tree.command(name="iwon")
async def iwon(interaction: discord.Interaction):
    await submit_result(interaction, True)

@bot.tree.command(name="ilost")
async def ilost(interaction: discord.Interaction):
    await submit_result(interaction, False)

# ================= LEADERBOARD =================
@bot.tree.command(name="elo", description="Посмотреть ELO игрока")
@app_commands.choices(game=GAMES)
async def elo(
    interaction: discord.Interaction,
    game: app_commands.Choice[str],
    user: discord.Member | None = None
):
    # Только канал leaderboard
    if interaction.channel.name != "leaderboard":
        await interaction.response.send_message(
            "Команда доступна только в #leaderboard",
            ephemeral=True
        )
        return

    target = user or interaction.user
    elo_value = get_elo(target.id, game.value)

    await interaction.response.send_message(
        f"🏅 **ELO — {game.value}**\n"
        f"{target.mention}: **{elo_value}**"
    )

# ================= END =================
@bot.tree.command(name="end")
async def end(interaction: discord.Interaction):
    cursor.execute("DELETE FROM matches WHERE channel_id=?", (interaction.channel.id,))
    cursor.execute("DELETE FROM pending_results WHERE channel_id=?", (interaction.channel.id,))
    conn.commit()
    await interaction.channel.delete()

bot.run(TOKEN)
