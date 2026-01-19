import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
import sqlite3

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Инициализация БД
DB_FILE = 'stats.db'
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

def update_elo(cursor, user_id, opponent_id, game_name, user_wins, user_losses):
    # Получаем текущие ELO
    cursor.execute('SELECT elo FROM elo WHERE user_id = ? AND game_name = ?', (user_id, game_name))
    row = cursor.fetchone()
    elo_user = row[0] if row else 50.0
    
    cursor.execute('SELECT elo FROM elo WHERE user_id = ? AND game_name = ?', (opponent_id, game_name))
    row = cursor.fetchone()
    elo_opp = row[0] if row else 50.0
    
    # Обновляем ELO: +10 за win, -15 за loss
    elo_user += 10 * user_wins - 15 * user_losses
    elo_opp += 10 * user_losses - 15 * user_wins  # wins пользователя = losses оппонента
    
    # Сохраняем обновленные ELO
    cursor.execute('INSERT OR REPLACE INTO elo (user_id, game_name, elo) VALUES (?, ?, ?)', (user_id, game_name, elo_user))
    cursor.execute('INSERT OR REPLACE INTO elo (user_id, game_name, elo) VALUES (?, ?, ?)', (opponent_id, game_name, elo_opp))

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    # Создаём таблицы если нет
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            user_id INTEGER PRIMARY KEY,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS elo (
            user_id INTEGER,
            game_name TEXT,
            elo REAL DEFAULT 50,
            PRIMARY KEY (user_id, game_name)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            channel_id INTEGER PRIMARY KEY,
            game_name TEXT,
            player1_id INTEGER,
            player2_id INTEGER
        )
    ''')
    conn.commit()
    
    # Создаём каналы и категории
    for guild in bot.guilds:
        lobby = discord.utils.get(guild.text_channels, name='lobby')
        if not lobby:
            await guild.create_text_channel('lobby', reason='Канал для создания челленджей и пати')
        
        one_x_one = discord.utils.get(guild.text_channels, name='1x1')
        if not one_x_one:
            await guild.create_text_channel('1x1', reason='Канал для принятия 1x1 челленджей')
        
        find_party = discord.utils.get(guild.text_channels, name='find-party')
        if not find_party:
            await guild.create_text_channel('find-party', reason='Канал для принятия пати')
        
        challenges_cat = discord.utils.get(guild.categories, name='Challenges')
        if not challenges_cat:
            challenges_cat = await guild.create_category('Challenges', reason='Категория для челлендж-каналов')
        
        parties_cat = discord.utils.get(guild.categories, name='Parties')
        if not parties_cat:
            parties_cat = await guild.create_category('Parties', reason='Категория для пати-каналов')
        
        leaderboard = discord.utils.get(guild.text_channels, name='leaderboard')
        if not leaderboard:
            await guild.create_text_channel('leaderboard', reason='Канал для лидербордов по ELO')
    
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} commands')
    except Exception as e:
        print(f'Failed to sync: {e}')

@bot.tree.command(name='challenge', description='Брось вызов оппоненту в игре')
@app_commands.describe(opponent='Выбери оппонента', game_name='Название игры')
@app_commands.choices(game_name=[
    app_commands.Choice(name='CounterStrike2', value='CounterStrike2'),
    app_commands.Choice(name='DeadByDaylight', value='DeadByDaylight'),
    app_commands.Choice(name='CS:GO', value='CS:GO'),
])
async def challenge(interaction: discord.Interaction, opponent: discord.Member, game_name: app_commands.Choice[str]):
    if interaction.channel.name != 'lobby':
        await interaction.response.send_message("Челленджи можно создавать только в #lobby.", ephemeral=True)
        return
    
    if opponent == interaction.user:
        await interaction.response.send_message("Нельзя бросить вызов себе, долбоёб.", ephemeral=True)
        return
    
    # Находим канал 1x1 для отправки сообщения о принятии
    one_x_one = discord.utils.get(interaction.guild.text_channels, name='1x1')
    if not one_x_one:
        await interaction.response.send_message("Канал #1x1 не найден.", ephemeral=True)
        return
    
    msg = await one_x_one.send(f"{interaction.user.mention} бросает вызов {opponent.mention} в {game_name.value}! Прими: ✅")
    await msg.add_reaction('✅')
    
    def check(reaction, user):
        return user == opponent and str(reaction.emoji) == '✅' and reaction.message.id == msg.id
    
    try:
        reaction, user = await bot.wait_for('reaction_add', timeout=300.0, check=check)
    except asyncio.TimeoutError:
        await interaction.followup.send("Время на принятие вышло. Челлендж отменён.")
        return
    
    # Получаем категорию Challenges
    challenges_cat = discord.utils.get(interaction.guild.categories, name='Challenges')
    
    # Создаём приватный канал в категории Challenges
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        opponent: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        bot.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    channel = await guild.create_text_channel(f'challenge-{game_name.value}-{interaction.user.name}-vs-{opponent.name}', category=challenges_cat, overwrites=overwrites)
    await channel.send(f"Челлендж принят в {game_name.value}! {interaction.user.mention} vs {opponent.mention}. Играйте и отчитывайтесь здесь с /IWon или /ILost. Завершить: /end")
    await interaction.followup.send(f"Челлендж принят! Канал создан: {channel.mention}")
    
    # Сохраняем матч в БД
    cursor.execute('INSERT INTO matches (channel_id, game_name, player1_id, player2_id) VALUES (?, ?, ?, ?)',
                   (channel.id, game_name.value, interaction.user.id, opponent.id))
    conn.commit()

@bot.tree.command(name='iwon', description='Отчёт о выигрыше: wins losses')
@app_commands.describe(wins='Число выигранных раундов', losses='Число проигранных раундов')
async def iwon(interaction: discord.Interaction, wins: int, losses: int):
    channel = interaction.channel
    if not channel.name.startswith('challenge-'):
        await interaction.response.send_message("Эта команда работает только в челлендж-каналах.", ephemeral=True)
        return
    
    user_id = interaction.user.id
    try:
        # Получаем матч из БД
        cursor.execute('SELECT game_name, player1_id, player2_id FROM matches WHERE channel_id = ?', (channel.id,))
        row = cursor.fetchone()
        if not row:
            await interaction.response.send_message("Матч не найден в БД.", ephemeral=True)
            return
        game_name, p1_id, p2_id = row
        if user_id not in (p1_id, p2_id):
            await interaction.response.send_message("Ты не участник этого матча.", ephemeral=True)
            return
        opponent_id = p1_id if user_id == p2_id else p2_id
        
        # Обновляем ELO
        update_elo(cursor, user_id, opponent_id, game_name, wins, losses)
        
        # Обновляем глобальную стату
        cursor.execute('SELECT wins, losses FROM stats WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        if row:
            current_wins, current_losses = row
            new_wins = current_wins + wins
            new_losses = current_losses + losses
            cursor.execute('UPDATE stats SET wins = ?, losses = ? WHERE user_id = ?', (new_wins, new_losses, user_id))
        else:
            new_wins = wins
            new_losses = losses
            cursor.execute('INSERT INTO stats (user_id, wins, losses) VALUES (?, ?, ?)', (user_id, new_wins, new_losses))
        conn.commit()
        await interaction.response.send_message(f"Записано: +{wins} wins, +{losses} losses. Твоя стата: {new_wins}W / {new_losses}L. ELO обновлено.")
    except Exception as e:
        print(f'DB error in iwon: {e}')
        await interaction.response.send_message("Ошибка с БД, но попробуй снова.", ephemeral=True)

@bot.tree.command(name='ilost', description='Отчёт о проигрыше: wins losses')
@app_commands.describe(wins='Число выигранных раундов', losses='Число проигранных раундов')
async def ilost(interaction: discord.Interaction, wins: int, losses: int):
    await iwon.callback(interaction, wins, losses)  # Переиспользуем логику (аналогично)

@bot.tree.command(name='findparty', description='Найди пати для игры')
@app_commands.describe(game_name='Название игры', max_players='Макс игроков (default 5)')
async def findparty(interaction: discord.Interaction, game_name: str, max_players: int = 5):
    if interaction.channel.name != 'lobby':
        await interaction.response.send_message("Поиск пати можно создавать только в #lobby.", ephemeral=True)
        return
    
    # Находим канал find-party для отправки сообщения о принятии
    find_party_channel = discord.utils.get(interaction.guild.text_channels, name='find-party')
    if not find_party_channel:
        await interaction.response.send_message("Канал #find-party не найден.", ephemeral=True)
        return
    
    players = [interaction.user]
    msg = await find_party_channel.send(f"{interaction.user.mention} ищет пати в {game_name}! Макс: {max_players}. Присоединяйся: ✅\nТекущие: {interaction.user.mention}")
    await msg.add_reaction('✅')
    
    def check(reaction, user):
        return str(reaction.emoji) == '✅' and reaction.message.id == msg.id and user != bot.user and user not in players
    
    while len(players) < max_players:
        try:
            reaction, user = await bot.wait_for('reaction_add', timeout=600.0, check=check)
            players.append(user)
            await msg.edit(content=f"{interaction.user.mention} ищет пати в {game_name}! Макс: {max_players}. Присоединяйся: ✅\nТекущие: {' '.join([p.mention for p in players])}")
        except asyncio.TimeoutError:
            break
    
    if len(players) < 2:
        await interaction.followup.send("Никто не присоединился. Пати отменено.")
        return
    
    # Получаем категорию Parties
    parties_cat = discord.utils.get(interaction.guild.categories, name='Parties')
    
    # Создаём приватный канал в категории Parties
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False)
    }
    for player in players:
        overwrites[player] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    overwrites[bot.user] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    
    channel_name = f'party-{game_name}-{interaction.user.name}'
    channel = await guild.create_text_channel(channel_name, category=parties_cat, overwrites=overwrites)
    mentions = ' '.join([p.mention for p in players])
    await channel.send(f"Пати собрано для {game_name}! Игроки: {mentions}. Координируйтесь здесь. Завершить: /end")
    await interaction.followup.send(f"Пати готово! Канал: {channel.mention}")

@bot.tree.command(name='end', description='Завершить челлендж или пати и удалить канал')
async def end(interaction: discord.Interaction):
    channel = interaction.channel
    if not (channel.name.startswith('challenge-') or channel.name.startswith('party-')):
        await interaction.response.send_message("Эта команда работает только в челлендж- или пати-каналах.", ephemeral=True)
        return
    
    # Проверка, что юзер имеет доступ (участник)
    if not channel.permissions_for(interaction.user).read_messages:
        await interaction.response.send_message("Ты не участник этого канала.", ephemeral=True)
        return
    
    # Удаляем матч из БД если challenge
    if channel.name.startswith('challenge-'):
        cursor.execute('DELETE FROM matches WHERE channel_id = ?', (channel.id,))
        conn.commit()
    
    await interaction.response.send_message("Канал удаляется через 5 секунд...")
    await asyncio.sleep(5)
    await channel.delete()

@bot.tree.command(name='elo', description='Показать своё ELO в игре')
@app_commands.describe(game_name='Название игры')
@app_commands.choices(game_name=[
    app_commands.Choice(name='CounterStrike2', value='CounterStrike2'),
    app_commands.Choice(name='DeadByDaylight', value='DeadByDaylight'),
    app_commands.Choice(name='CS:GO', value='CS:GO'),
])
async def show_elo(interaction: discord.Interaction, game_name: app_commands.Choice[str]):
    user_id = interaction.user.id
    cursor.execute('SELECT elo FROM elo WHERE user_id = ? AND game_name = ?', (user_id, game_name.value))
    row = cursor.fetchone()
    elo = int(row[0]) if row else 50
    await interaction.response.send_message(f"Твоё ELO в {game_name.value}: {elo}")

@bot.tree.command(name='leaderboard', description='Показать топ-10 по ELO в игре')
@app_commands.describe(game_name='Название игры')
@app_commands.choices(game_name=[
    app_commands.Choice(name='CounterStrike2', value='CounterStrike2'),
    app_commands.Choice(name='DeadByDaylight', value='DeadByDaylight'),
    app_commands.Choice(name='CS:GO', value='CS:GO'),
])
async def leaderboard(interaction: discord.Interaction, game_name: app_commands.Choice[str]):
    cursor.execute('SELECT user_id, elo FROM elo WHERE game_name = ? ORDER BY elo DESC LIMIT 10', (game_name.value,))
    rows = cursor.fetchall()
    if not rows:
        await interaction.response.send_message(f"Нет игроков в {game_name.value}.")
        return
    
    msg = f"Топ-10 по ELO в {game_name.value}:\n"
    for i, (uid, elo) in enumerate(rows, 1):
        user = await bot.fetch_user(uid)
        msg += f"{i}. {user.name}: {int(elo)}\n"
    
    # Находим канал leaderboard и шлём туда
    leaderboard_channel = discord.utils.get(interaction.guild.text_channels, name='leaderboard')
    if leaderboard_channel:
        await leaderboard_channel.send(msg)
        await interaction.response.send_message(f"Лидерборд отправлен в #leaderboard.")
    else:
        await interaction.response.send_message(msg)

@bot.event
async def on_close():
    conn.close()

bot.run(TOKEN)
