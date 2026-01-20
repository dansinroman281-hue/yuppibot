import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
import psycopg2  # Замена sqlite

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
DB_URL = os.getenv('DATABASE_URL')  # Из Render env

def get_db_connection():
    return psycopg2.connect(DB_URL)

def update_elo(cursor, user_id, opponent_id, game_name, user_wins, user_losses):
    # Получаем текущие ELO
    cursor.execute('SELECT elo FROM elo WHERE user_id = %s AND game_name = %s', (user_id, game_name))
    row = cursor.fetchone()
    elo_user = row[0] if row else 50.0
    
    cursor.execute('SELECT elo FROM elo WHERE user_id = %s AND game_name = %s', (opponent_id, game_name))
    row = cursor.fetchone()
    elo_opp = row[0] if row else 50.0
    
    # Обновляем ELO: +10 за win, -15 за loss
    elo_user += 10 * user_wins - 15 * user_losses
    elo_opp += 10 * user_losses - 15 * user_wins  # wins пользователя = losses оппонента
    
    # Сохраняем обновленные ELO
    cursor.execute('INSERT INTO elo (user_id, game_name, elo) VALUES (%s, %s, %s) ON CONFLICT (user_id, game_name) DO UPDATE SET elo = EXCLUDED.elo', (user_id, game_name, elo_user))
    cursor.execute('INSERT INTO elo (user_id, game_name, elo) VALUES (%s, %s, %s) ON CONFLICT (user_id, game_name) DO UPDATE SET elo = EXCLUDED.elo', (opponent_id, game_name, elo_opp))

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    conn = get_db_connection()
    cursor = conn.cursor()
    # Создаём таблицы если нет (Postgres синтаксис)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            user_id BIGINT PRIMARY KEY,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS elo (
            user_id BIGINT,
            game_name TEXT,
            elo REAL DEFAULT 50,
            PRIMARY KEY (user_id, game_name)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            channel_id BIGINT PRIMARY KEY,
            game_name TEXT,
            player1_id BIGINT,
            player2_id BIGINT
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()
    
    # Создаём каналы и категории (то же)
    for guild in bot.guilds:
        # ... (остальной код создания каналов без изменений)

    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} commands')
    except Exception as e:
        print(f'Failed to sync: {e}')

# Остальные команды без изменений, кроме /challenge, /iwon etc., где cursor.execute адаптирован для psycopg2 (%s вместо ?)

@bot.tree.command(name='challenge', description='Брось вызов оппоненту в игре')
@app_commands.describe(opponent='Выбери оппонента', game_name='Название игры')
@app_commands.choices(game_name=[
    app_commands.Choice(name='CounterStrike2', value='CounterStrike2'),
    app_commands.Choice(name='DeadByDaylight', value='DeadByDaylight'),
    app_commands.Choice(name='CS:GO', value='CS:GO'),
])
async def challenge(interaction: discord.Interaction, opponent: discord.Member, game_name: app_commands.Choice[str]):
    # ... (код без изменений, кроме БД части)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO matches (channel_id, game_name, player1_id, player2_id) VALUES (%s, %s, %s, %s)',
                   (channel.id, game_name.value, interaction.user.id, opponent.id))
    conn.commit()
    cursor.close()
    conn.close()

@bot.tree.command(name='iwon', description='Отчёт о выигрыше: wins losses')
@app_commands.describe(wins='Число выигранных раундов', losses='Число проигранных раундов')
async def iwon(interaction: discord.Interaction, wins: int, losses: int):
    # ... (начало то же)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT game_name, player1_id, player2_id FROM matches WHERE channel_id = %s', (channel.id,))
        row = cursor.fetchone()
        if not row:
            await interaction.response.send_message("Матч не найден в БД.", ephemeral=True)
            return
        # ... (остальное)
        cursor.execute('SELECT wins, losses FROM stats WHERE user_id = %s', (user_id,))
        row = cursor.fetchone()
        # ... (update stats с %s)
        conn.commit()
    except Exception as e:
        print(f'DB error in iwon: {e}')
        await interaction.response.send_message("Ошибка с БД, но попробуй снова.", ephemeral=True)
    finally:
        cursor.close()
        conn.close()

# Аналогично адаптируй /ilost, /end (DELETE с %s), /elo, /leaderboard (SELECT с %s)

@bot.tree.command(name='elo', description='Показать своё ELO в игре')
@app_commands.describe(game_name='Название игры')
@app_commands.choices(game_name=[
    app_commands.Choice(name='CounterStrike2', value='CounterStrike2'),
    app_commands.Choice(name='DeadByDaylight', value='DeadByDaylight'),
    app_commands.Choice(name='CS:GO', value='CS:GO'),
])
async def show_elo(interaction: discord.Interaction, game_name: app_commands.Choice[str]):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT elo FROM elo WHERE user_id = %s AND game_name = %s', (interaction.user.id, game_name.value))
    row = cursor.fetchone()
    elo = int(row[0]) if row else 50
    await interaction.response.send_message(f"Твоё ELO в {game_name.value}: {elo}")
    cursor.close()
    conn.close()

@bot.tree.command(name='leaderboard', description='Показать топ-10 по ELO в игре')
@app_commands.describe(game_name='Название игры')
@app_commands.choices(game_name=[
    app_commands.Choice(name='CounterStrike2', value='CounterStrike2'),
    app_commands.Choice(name='DeadByDaylight', value='DeadByDaylight'),
    app_commands.Choice(name='CS:GO', value='CS:GO'),
])
async def leaderboard(interaction: discord.Interaction, game_name: app_commands.Choice[str]):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, elo FROM elo WHERE game_name = %s ORDER BY elo DESC LIMIT 10', (game_name.value,))
    rows = cursor.fetchall()
    # ... (остальное то же)
    cursor.close()
    conn.close()

# Для /end:
@bot.tree.command(name='end', description='Завершить челлендж или пати и удалить канал')
async def end(interaction: discord.Interaction):
    # ... (начало то же)
    if channel.name.startswith('challenge-'):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM matches WHERE channel_id = %s', (channel.id,))
        conn.commit()
        cursor.close()
        conn.close()
    # ... (delete channel)

# Для /findparty нет БД, так что без изменений.

@bot.event
async def on_close():
    # Нет conn.close(), потому что conn per function.

bot.run(TOKEN)
