# modules/leveling_system.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import aiosqlite
from datetime import datetime, timedelta
import os
from typing import Optional, Dict, List, Tuple
import json
import math

class LevelingSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "leveling_system.db"
        self.db_ready = False
        
        # Configuration
        self.config = {
            # EXP Configuration
            "exp_per_message": 15,
            "exp_per_voice_minute": 10,
            "message_cooldown": 60,  # seconds
            
            # Blacklisted channels (no EXP)
            "blacklisted_channels": [
                int(os.getenv('COMMANDES_CHANNEL_ID', 0)),
                int(os.getenv('LOGS_CHANNEL_ID', 0)),
                int(os.getenv('COMMANDES_ADMIN_CHANNEL_ID', 0))
            ],
            
            # Role multipliers {role_id: multiplier bonus (without the base 1)}
            "role_multipliers": {
                int(os.getenv('DIVINATEUR_ROLE_ID', 0)): 0.5,   # 1.5x total
                int(os.getenv('BUMP_ROLE_ID', 0)): 0.25          # 1.25x total
            },
            
            # Level rewards (every 10 levels) - Role IDs
            "level_rewards": {
                10: 1377682365115011093,  # Élémentaliste Novice
                20: 1377682365115011093,  # Élémentaliste Apprenti
                30: 1377682365115011093,  # Élémentaliste Confirmé
                40: 1377682365115011093,  # Élémentaliste Expert
                50: 1377682365115011093   # Maître Élémentaliste
            },
            
            # Reward messages (configurable)
            "reward_messages": {
                10: "🌟 {user} vient de débloquer le rang **Élémentaliste Novice** ! Les éléments commencent à répondre à ton appel...",
                20: "⚡ {user} a atteint le rang **Élémentaliste Apprenti** ! Ta maîtrise des éléments s'améliore...",
                30: "🔥 {user} est maintenant un **Élémentaliste Confirmé** ! Les éléments dansent sous tes ordres...",
                40: "💎 {user} devient un **Élémentaliste Expert** ! Peu peuvent rivaliser avec ta maîtrise élémentaire...",
                50: "👑 {user} a atteint le rang suprême de **Maître Élémentaliste** ! Tu es devenu un avec les éléments !"
            },
            
            # Whether to remove previous level rewards when getting a higher one
            "remove_previous_rewards": False
        }
        
        # Cache pour les cooldowns et temps vocal
        self.message_cooldowns = {}
        self.voice_times = {}
        
    async def cog_load(self):
        """Initialise la connexion à la base de données"""
        await self.setup_database()
        # Démarrer les tâches après l'initialisation de la DB
        if self.db_ready:
            self.voice_exp_task.start()
        
    async def cog_unload(self):
        """Nettoie les ressources"""
        if hasattr(self, 'voice_exp_task'):
            self.voice_exp_task.cancel()

    async def setup_database(self):
        """Configure la base de données SQLite locale et crée les tables"""
        try:
            print(f"🔧 Initialisation de la base de données SQLite: {self.db_path}")
            
            # Créer la base de données et les tables
            async with aiosqlite.connect(self.db_path) as db:
                # Table des utilisateurs et niveaux
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS user_levels (
                        user_id INTEGER PRIMARY KEY,
                        exp INTEGER DEFAULT 0,
                        level INTEGER DEFAULT 0,
                        total_messages INTEGER DEFAULT 0,
                        voice_time INTEGER DEFAULT 0,
                        last_message_time TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                # Table des récompenses obtenues
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS user_rewards (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        level_reached INTEGER,
                        role_id INTEGER,
                        obtained_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES user_levels(user_id) ON DELETE CASCADE
                    )
                """)
                
                # Créer les index pour les performances
                await db.execute("CREATE INDEX IF NOT EXISTS idx_user_levels_exp ON user_levels(exp DESC)")
                await db.execute("CREATE INDEX IF NOT EXISTS idx_user_rewards_user_id ON user_rewards(user_id)")
                
                await db.commit()
                
            print("✅ Base de données SQLite initialisée avec succès")
            self.db_ready = True
            
        except Exception as e:
            print(f"❌ Erreur lors de l'initialisation de la base de données: {e}")
            self.db_ready = False

    async def wait_for_db(self):
        """Attend que la base de données soit prête"""
        max_wait = 30
        waited = 0
        while not self.db_ready and waited < max_wait:
            await asyncio.sleep(1)
            waited += 1
        
        if not self.db_ready:
            print("⚠️ Timeout en attendant la base de données")

    async def get_user_data(self, user_id: int) -> Dict:
        """Récupère les données d'un utilisateur"""
        if not self.db_ready:
            return {'user_id': user_id, 'exp': 0, 'level': 0, 'total_messages': 0, 'voice_time': 0}
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT user_id, exp, level, total_messages, voice_time FROM user_levels WHERE user_id = ?",
                    (user_id,)
                )
                result = await cursor.fetchone()
                
                if result:
                    return {
                        'user_id': result[0],
                        'exp': result[1],
                        'level': result[2],
                        'total_messages': result[3],
                        'voice_time': result[4]
                    }
                else:
                    # Créer un nouvel utilisateur
                    await db.execute(
                        "INSERT INTO user_levels (user_id, exp, level) VALUES (?, 0, 0)",
                        (user_id,)
                    )
                    await db.commit()
                    return {'user_id': user_id, 'exp': 0, 'level': 0, 'total_messages': 0, 'voice_time': 0}
        except Exception as e:
            print(f"Erreur get_user_data: {e}")
            return {'user_id': user_id, 'exp': 0, 'level': 0, 'total_messages': 0, 'voice_time': 0}

    async def update_user_exp(self, user_id: int, exp_gain: int, from_voice: bool = False):
        """Met à jour l'EXP d'un utilisateur et gère les montées de niveau"""
        if not self.db_ready:
            return 0, 0, 0
        
        try:
            user_data = await self.get_user_data(user_id)
            old_level = user_data['level']
            new_exp = max(0, user_data['exp'] + exp_gain)
            new_level = self.calculate_level(new_exp)
            
            # Mettre à jour la base de données
            async with aiosqlite.connect(self.db_path) as db:
                if from_voice:
                    await db.execute(
                        "UPDATE user_levels SET exp = ?, level = ?, voice_time = voice_time + 1, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                        (new_exp, new_level, user_id)
                    )
                else:
                    await db.execute(
                        "UPDATE user_levels SET exp = ?, level = ?, total_messages = total_messages + 1, last_message_time = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                        (new_exp, new_level, user_id)
                    )
                await db.commit()
            
            # Vérifier les récompenses de niveau (seulement si niveau augmenté)
            if new_level > old_level:
                await self.check_level_rewards(user_id, old_level, new_level)
            
            return old_level, new_level, exp_gain
        except Exception as e:
            print(f"Erreur update_user_exp: {e}")
            return 0, 0, 0

    def calculate_level(self, exp: int) -> int:
        """Calcule le niveau basé sur l'EXP (formule: 75*level²)"""
        if exp < 75:
            return 0
        # Résoudre l'équation: exp = 75 * level²
        # level = sqrt(exp / 75)
        return int(math.sqrt(exp / 75))

    def calculate_exp_for_level(self, level: int) -> int:
        """Calcule l'EXP requise pour un niveau (formule: 75*level²)"""
        return 75 * level * level

    def calculate_exp_from_activity(self, messages: int, voice_minutes: int) -> int:
        """Calcule l'EXP total basé sur les messages et temps vocal"""
        return (messages * self.config['exp_per_message']) + (voice_minutes * self.config['exp_per_voice_minute'])

    async def sync_user_rewards(self, user_id: int):
        """Synchronise les récompenses avec le niveau actuel de l'utilisateur"""
        try:
            guild = self.bot.get_guild(int(os.getenv('GUILD_ID')))
            if not guild:
                return
            
            member = guild.get_member(user_id)
            if not member:
                return
            
            user_data = await self.get_user_data(user_id)
            current_level = user_data['level']
            
            # Déterminer quelles récompenses l'utilisateur devrait avoir
            should_have_rewards = []
            for level, role_id in self.config['level_rewards'].items():
                if current_level >= level:
                    should_have_rewards.append((level, role_id))
            
            # Récupérer les récompenses actuellement possédées
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT level_reached, role_id FROM user_rewards WHERE user_id = ?",
                    (user_id,)
                )
                current_rewards = await cursor.fetchall()
            
            current_reward_levels = {reward[0] for reward in current_rewards}
            should_have_levels = {reward[0] for reward in should_have_rewards}
            
            # Ajouter les récompenses manquantes
            missing_rewards = should_have_levels - current_reward_levels
            for level in missing_rewards:
                role_id = self.config['level_rewards'][level]
                role = guild.get_role(role_id)
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role, reason=f"Récompense niveau {level} (sync)")
                        
                        # Enregistrer dans la DB
                        async with aiosqlite.connect(self.db_path) as db:
                            await db.execute(
                                "INSERT INTO user_rewards (user_id, level_reached, role_id) VALUES (?, ?, ?)",
                                (user_id, level, role_id)
                            )
                            await db.commit()
                        
                        # Annoncer la récompense
                        await self.announce_reward(member, level)
                        print(f"✅ Récompense niveau {level} synchronisée pour {member.display_name}")
                        
                    except discord.Forbidden:
                        print(f"❌ Pas la permission d'attribuer le rôle niveau {level}")
                    except Exception as e:
                        print(f"❌ Erreur lors de l'attribution du rôle niveau {level}: {e}")
            
            # Retirer les récompenses qui ne devraient plus être possédées
            excess_rewards = current_reward_levels - should_have_levels
            for level in excess_rewards:
                # Trouver le role_id correspondant
                role_id = None
                for reward in current_rewards:
                    if reward[0] == level:
                        role_id = reward[1]
                        break
                
                if role_id:
                    role = guild.get_role(role_id)
                    if role and role in member.roles:
                        try:
                            await member.remove_roles(role, reason=f"Niveau {level} plus atteint (sync)")
                            print(f"✅ Rôle niveau {level} retiré de {member.display_name}")
                        except discord.Forbidden:
                            print(f"❌ Pas la permission de retirer le rôle niveau {level}")
                        except Exception as e:
                            print(f"❌ Erreur lors du retrait du rôle niveau {level}: {e}")
                    
                    # Supprimer de la DB
                    async with aiosqlite.connect(self.db_path) as db:
                        await db.execute(
                            "DELETE FROM user_rewards WHERE user_id = ? AND level_reached = ?",
                            (user_id, level)
                        )
                        await db.commit()
            
            # Gérer la suppression des récompenses précédentes si configuré
            if self.config['remove_previous_rewards'] and should_have_rewards:
                # Garder seulement la récompense de niveau le plus élevé
                highest_level_reward = max(should_have_rewards, key=lambda x: x[0])
                for level, role_id in should_have_rewards:
                    if level != highest_level_reward[0]:
                        role = guild.get_role(role_id)
                        if role and role in member.roles:
                            try:
                                await member.remove_roles(role, reason="Récompense précédente remplacée")
                                print(f"✅ Récompense précédente niveau {level} retirée de {member.display_name}")
                            except Exception as e:
                                print(f"❌ Erreur lors du retrait de la récompense précédente: {e}")
                                
        except Exception as e:
            print(f"Erreur sync_user_rewards: {e}")

    async def announce_reward(self, member: discord.Member, level: int):
        """Annonce une récompense dans le channel niveaux"""
        niveaux_channel_id = os.getenv('NIVEAUX_CHANNEL_ID')
        if niveaux_channel_id and niveaux_channel_id != 'niveaux_channel_id':
            channel = member.guild.get_channel(int(niveaux_channel_id))
            if channel:
                if level in self.config['reward_messages']:
                    message = self.config['reward_messages'][level].format(user=member.mention)
                    await channel.send(message)
                else:
                    role_id = self.config['level_rewards'].get(level)
                    role = member.guild.get_role(role_id) if role_id else None
                    role_mention = role.mention if role else f"<@&{role_id}>"
                    await channel.send(f"🎉 {member.mention} a atteint le niveau {level} et obtient le rôle {role_mention} !")

    async def check_level_rewards(self, user_id: int, old_level: int, new_level: int):
        """Vérifie et attribue les récompenses de niveau"""
        try:
            guild = self.bot.get_guild(int(os.getenv('GUILD_ID')))
            if not guild:
                return
            
            member = guild.get_member(user_id)
            if not member:
                return
            
            # Vérifier chaque niveau entre old_level et new_level
            for level in range(old_level + 1, new_level + 1):
                if level % 10 == 0 and level in self.config['level_rewards']:
                    role_id = self.config['level_rewards'][level]
                    
                    # Récupérer le rôle existant
                    role = guild.get_role(role_id)
                    if not role:
                        print(f"⚠️ Rôle {role_id} introuvable pour le niveau {level}")
                        continue
                    
                    # Vérifier si l'utilisateur a déjà ce rôle
                    if role in member.roles:
                        continue
                    
                    # Attribuer le rôle
                    try:
                        await member.add_roles(role, reason=f"Niveau {level} atteint")
                        
                        # Enregistrer la récompense
                        if self.db_ready:
                            async with aiosqlite.connect(self.db_path) as db:
                                await db.execute(
                                    "INSERT INTO user_rewards (user_id, level_reached, role_id) VALUES (?, ?, ?)",
                                    (user_id, level, role_id)
                                )
                                await db.commit()
                        
                        # Gérer la suppression des récompenses précédentes si configuré
                        if self.config['remove_previous_rewards']:
                            for prev_level, prev_role_id in self.config['level_rewards'].items():
                                if prev_level < level and prev_level % 10 == 0:
                                    prev_role = guild.get_role(prev_role_id)
                                    if prev_role and prev_role in member.roles:
                                        try:
                                            await member.remove_roles(prev_role, reason="Récompense précédente remplacée")
                                        except Exception as e:
                                            print(f"❌ Erreur lors du retrait de la récompense précédente: {e}")
                        
                        # Annoncer la récompense
                        await self.announce_reward(member, level)
                        print(f"✅ Rôle {role.name} attribué à {member.display_name} pour le niveau {level}")
                            
                    except discord.Forbidden:
                        print(f"❌ Pas la permission d'attribuer le rôle {role.name}")
                    except Exception as e:
                        print(f"❌ Erreur lors de l'attribution du rôle: {e}")
        except Exception as e:
            print(f"Erreur check_level_rewards: {e}")

    def get_multiplier(self, member: discord.Member) -> float:
        """Calcule le multiplicateur d'EXP basé sur les rôles (additif)"""
        base_multiplier = 1.0
        bonus_multiplier = 0.0
        
        # Additionner tous les bonus de multiplicateurs
        for role in member.roles:
            if role.id in self.config['role_multipliers']:
                bonus_multiplier += self.config['role_multipliers'][role.id]
        
        return base_multiplier + bonus_multiplier

    def is_admin(self, user: discord.Member) -> bool:
        """Vérifie si l'utilisateur est admin"""
        admin_role_id = os.getenv('ADMIN_ROLE_ID')
        if not admin_role_id or admin_role_id == 'your_admin_role_id':
            return False
        return any(role.id == int(admin_role_id) for role in user.roles)

    async def display_level_info(self, interaction: discord.Interaction, utilisateur: Optional[discord.Member] = None):
        """Fonction partagée pour afficher les informations de niveau"""
        if not self.db_ready:
            await interaction.response.send_message("❌ Base de données non disponible. Le système de niveaux est temporairement indisponible.", ephemeral=True)
            return
        
        target = utilisateur or interaction.user
        user_data = await self.get_user_data(target.id)
        
        current_level = user_data['level']
        current_exp = user_data['exp']
        exp_for_current = self.calculate_exp_for_level(current_level)
        exp_for_next = self.calculate_exp_for_level(current_level + 1)
        exp_progress = current_exp - exp_for_current
        exp_needed = exp_for_next - exp_for_current
        
        # Créer l'embed
        embed = discord.Embed(
            title=f"📊 Profil de {target.display_name}",
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="🎯 Niveau", value=f"`{current_level}`", inline=True)
        embed.add_field(name="⭐ EXP Total", value=f"`{current_exp:,}`", inline=True)
        embed.add_field(name="📈 Progression", value=f"`{exp_progress:,}/{exp_needed:,}`", inline=True)
        embed.add_field(name="💬 Messages", value=f"`{user_data['total_messages']:,}`", inline=True)
        embed.add_field(name="🎤 Temps Vocal", value=f"`{user_data['voice_time']:,}` min", inline=True)
        
        # Barre de progression
        progress_bar_length = 20
        progress = min(exp_progress / exp_needed, 1.0) if exp_needed > 0 else 1.0
        filled_length = int(progress_bar_length * progress)
        bar = "█" * filled_length + "░" * (progress_bar_length - filled_length)
        embed.add_field(name="📊 Progression vers le niveau suivant", value=f"`{bar}` {progress*100:.1f}%", inline=False)
        
        # Prochain niveau et récompense
        next_reward_level = ((current_level // 10) + 1) * 10
        if next_reward_level in self.config['level_rewards']:
            role_id = self.config['level_rewards'][next_reward_level]
            role = interaction.guild.get_role(role_id)
            role_mention = role.mention if role else f"<@&{role_id}>"
            embed.add_field(name="🎁 Prochaine Récompense", value=f"Niveau {next_reward_level}: {role_mention}", inline=False)
        
        embed.set_footer(text=f"Serveur: {interaction.guild.name}")
        await interaction.response.send_message(embed=embed)

    async def display_leaderboard(self, interaction: discord.Interaction, page: Optional[int] = 1):
        """Fonction partagée pour afficher le classement"""
        if not self.db_ready:
            await interaction.response.send_message("❌ Base de données non disponible. Le système de niveaux est temporairement indisponible.", ephemeral=True)
            return
        
        page = max(1, page)
        offset = (page - 1) * 10
        
        try:
            # Récupérer les données du leaderboard
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT user_id, exp, level FROM user_levels ORDER BY level DESC, exp DESC LIMIT 10 OFFSET ?",
                    (offset,)
                )
                results = await cursor.fetchall()
                
                # Compter le total d'utilisateurs
                cursor = await db.execute("SELECT COUNT(*) FROM user_levels")
                total_users = (await cursor.fetchone())[0]
        except Exception as e:
            await interaction.response.send_message("❌ Erreur lors de la récupération des données.", ephemeral=True)
            return
        
        if not results:
            embed = discord.Embed(
                title="📋 Classement des Niveaux",
                description="Aucun utilisateur trouvé pour cette page.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
            return
        
        # Créer l'embed
        embed = discord.Embed(
            title="🏆 Classement des Niveaux",
            color=discord.Color.gold()
        )
        
        description = ""
        for i, (user_id, exp, level) in enumerate(results, start=offset + 1):
            user = self.bot.get_user(user_id)
            user_name = user.display_name if user else f"Utilisateur {user_id}"
            
            # Emojis pour le podium
            if i == 1:
                emoji = "🥇"
            elif i == 2:
                emoji = "🥈"
            elif i == 3:
                emoji = "🥉"
            else:
                emoji = f"`{i}.`"
            
            description += f"{emoji} **{user_name}** - Niveau `{level}` (`{exp:,}` EXP)\n"
        
        embed.description = description
        
        # Informations de pagination
        max_pages = math.ceil(total_users / 10) if total_users > 0 else 1
        embed.set_footer(text=f"Page {page}/{max_pages} • {total_users} utilisateurs au total")
        
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Donne de l'EXP pour les messages"""
        if message.author.bot or not message.guild:
            return
        
        if message.guild.id != int(os.getenv('GUILD_ID')):
            return
        
        # Attendre que la DB soit prête
        if not self.db_ready:
            return
        
        # Vérifier si le channel est blacklisté
        if message.channel.id in self.config['blacklisted_channels']:
            return
        
        user_id = message.author.id
        current_time = datetime.now()
        
        # Vérifier le cooldown
        if user_id in self.message_cooldowns:
            time_diff = (current_time - self.message_cooldowns[user_id]).total_seconds()
            if time_diff < self.config['message_cooldown']:
                return
        
        # Calculer l'EXP avec multiplicateur
        base_exp = self.config['exp_per_message']
        multiplier = self.get_multiplier(message.author)
        final_exp = int(base_exp * multiplier)
        
        # Mettre à jour l'EXP (les récompenses sont gérées dans update_user_exp -> check_level_rewards)
        old_level, new_level, exp_gained = await self.update_user_exp(user_id, final_exp)
        
        # Mettre à jour le cooldown
        self.message_cooldowns[user_id] = current_time

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Gère l'entrée/sortie des channels vocaux"""
        if member.bot or member.guild.id != int(os.getenv('GUILD_ID')):
            return
        
        current_time = datetime.now()
        
        # Utilisateur rejoint un vocal
        if before.channel is None and after.channel is not None:
            if after.channel.id not in self.config['blacklisted_channels']:
                self.voice_times[member.id] = current_time
        
        # Utilisateur quitte un vocal
        elif before.channel is not None and after.channel is None:
            if member.id in self.voice_times:
                del self.voice_times[member.id]
        
        # Utilisateur change de vocal
        elif before.channel != after.channel:
            if after.channel and after.channel.id not in self.config['blacklisted_channels']:
                self.voice_times[member.id] = current_time
            elif member.id in self.voice_times:
                del self.voice_times[member.id]

    @tasks.loop(minutes=1)
    async def voice_exp_task(self):
        """Donne de l'EXP aux utilisateurs en vocal chaque minute"""
        if not self.voice_times or not self.db_ready:
            return
        
        guild = self.bot.get_guild(int(os.getenv('GUILD_ID')))
        if not guild:
            return
        
        for user_id in list(self.voice_times.keys()):
            member = guild.get_member(user_id)
            if not member or not member.voice or not member.voice.channel:
                if user_id in self.voice_times:
                    del self.voice_times[user_id]
                continue
            
            # Calculer l'EXP vocal avec multiplicateur
            base_exp = self.config['exp_per_voice_minute']
            multiplier = self.get_multiplier(member)
            final_exp = int(base_exp * multiplier)
            
            # Mettre à jour l'EXP
            await self.update_user_exp(user_id, final_exp, from_voice=True)

    @voice_exp_task.before_loop
    async def before_voice_exp_task(self):
        await self.bot.wait_until_ready()
        await self.wait_for_db()

    # Slash Commands
    @app_commands.command(name="niveau", description="Affiche tes informations de niveau")
    async def level_info(self, interaction: discord.Interaction, utilisateur: Optional[discord.Member] = None):
        """Affiche les informations de niveau d'un utilisateur"""
        await self.display_level_info(interaction, utilisateur)

    @app_commands.command(name="level", description="Affiche tes informations de niveau")
    async def level_alias(self, interaction: discord.Interaction, utilisateur: Optional[discord.Member] = None):
        """Alias pour /niveau"""
        await self.display_level_info(interaction, utilisateur)

    @app_commands.command(name="classement", description="Affiche le classement des niveaux")
    async def leaderboard_fr(self, interaction: discord.Interaction, page: Optional[int] = 1):
        """Affiche le leaderboard avec pagination"""
        await self.display_leaderboard(interaction, page)

    @app_commands.command(name="leaderboard", description="Affiche le classement des niveaux")
    async def leaderboard(self, interaction: discord.Interaction, page: Optional[int] = 1):
        """Alias pour /classement"""
        await self.display_leaderboard(interaction, page)

    @app_commands.command(name="toplevel", description="Affiche le classement des niveaux")
    async def toplevel(self, interaction: discord.Interaction, page: Optional[int] = 1):
        """Alias pour /classement"""
        await self.display_leaderboard(interaction, page)

    @app_commands.command(name="exp-add", description="Ajoute de l'EXP à un utilisateur (Admin)")
    @app_commands.describe(utilisateur="L'utilisateur à qui ajouter de l'EXP", montant="Montant d'EXP à ajouter")
    async def add_exp(self, interaction: discord.Interaction, utilisateur: discord.Member, montant: int):
        """Ajoute de l'EXP à un utilisateur (commande admin)"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if not self.db_ready:
            await interaction.response.send_message("❌ Base de données non disponible.", ephemeral=True)
            return
        
        if montant <= 0:
            await interaction.response.send_message("❌ Le montant doit être positif.", ephemeral=True)
            return
        
        old_level, new_level, _ = await self.update_user_exp(utilisateur.id, montant)
        
        embed = discord.Embed(
            title="✅ EXP Ajoutée",
            description=f"**{montant:,}** EXP ajoutée à {utilisateur.mention}",
            color=discord.Color.green()
        )
        
        if new_level > old_level:
            embed.add_field(name="📈 Niveau", value=f"{old_level} → {new_level}", inline=False)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="exp-remove", description="Retire de l'EXP à un utilisateur (Admin)")
    @app_commands.describe(utilisateur="L'utilisateur à qui retirer de l'EXP", montant="Montant d'EXP à retirer")
    async def remove_exp(self, interaction: discord.Interaction, utilisateur: discord.Member, montant: int):
        """Retire de l'EXP à un utilisateur (commande admin)"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if not self.db_ready:
            await interaction.response.send_message("❌ Base de données non disponible.", ephemeral=True)
            return
        
        if montant <= 0:
            await interaction.response.send_message("❌ Le montant doit être positif.", ephemeral=True)
            return
        
        user_data = await self.get_user_data(utilisateur.id)
        if user_data['exp'] < montant:
            await interaction.response.send_message("❌ L'utilisateur n'a pas assez d'EXP.", ephemeral=True)
            return
        
        old_level, new_level, _ = await self.update_user_exp(utilisateur.id, -montant)
        
        # Synchroniser les récompenses après modification manuelle
        await self.sync_user_rewards(utilisateur.id)
        
        embed = discord.Embed(
            title="✅ EXP Retirée",
            description=f"**{montant:,}** EXP retirée à {utilisateur.mention}",
            color=discord.Color.orange()
        )
        
        if new_level < old_level:
            embed.add_field(name="📉 Niveau", value=f"{old_level} → {new_level}", inline=False)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="exp-set", description="Définit l'EXP d'un utilisateur (Admin)")
    @app_commands.describe(utilisateur="L'utilisateur dont modifier l'EXP", montant="Nouveau montant d'EXP")
    async def set_exp(self, interaction: discord.Interaction, utilisateur: discord.Member, montant: int):
        """Définit l'EXP d'un utilisateur (commande admin)"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if not self.db_ready:
            await interaction.response.send_message("❌ Base de données non disponible.", ephemeral=True)
            return
        
        if montant < 0:
            await interaction.response.send_message("❌ Le montant ne peut pas être négatif.", ephemeral=True)
            return
        
        try:
            user_data = await self.get_user_data(utilisateur.id)
            old_level = user_data['level']
            new_level = self.calculate_level(montant)
            
            # Mettre à jour directement
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE user_levels SET exp = ?, level = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (montant, new_level, utilisateur.id)
                )
                await db.commit()
            
            # Synchroniser toutes les récompenses
            await self.sync_user_rewards(utilisateur.id)
            
            embed = discord.Embed(
                title="✅ EXP Définie",
                description=f"EXP de {utilisateur.mention} définie à **{montant:,}**",
                color=discord.Color.blue()
            )
            embed.add_field(name="📊 Niveau", value=f"{old_level} → {new_level}", inline=False)
            
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message("❌ Erreur lors de la mise à jour.", ephemeral=True)

    @app_commands.command(name="set-activity", description="Définit l'activité d'un utilisateur et calcule l'EXP (Admin)")
    @app_commands.describe(
        utilisateur="L'utilisateur dont modifier l'activité",
        messages="Nombre de messages",
        temps_vocal="Temps vocal en minutes"
    )
    async def set_activity(self, interaction: discord.Interaction, utilisateur: discord.Member, messages: int, temps_vocal: int):
        """Définit l'activité d'un utilisateur et recalcule l'EXP"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if not self.db_ready:
            await interaction.response.send_message("❌ Base de données non disponible.", ephemeral=True)
            return
        
        if messages < 0 or temps_vocal < 0:
            await interaction.response.send_message("❌ Les valeurs ne peuvent pas être négatives.", ephemeral=True)
            return
        
        try:
            # Calculer l'EXP total basé sur l'activité
            total_exp = self.calculate_exp_from_activity(messages, temps_vocal)
            new_level = self.calculate_level(total_exp)
            
            user_data = await self.get_user_data(utilisateur.id)
            old_level = user_data['level']
            
            # Mettre à jour la base de données
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """UPDATE user_levels 
                       SET exp = ?, level = ?, total_messages = ?, voice_time = ?, updated_at = CURRENT_TIMESTAMP 
                       WHERE user_id = ?""",
                    (total_exp, new_level, messages, temps_vocal, utilisateur.id)
                )
                await db.commit()
            
            # Synchroniser les récompenses
            await self.sync_user_rewards(utilisateur.id)
            
            embed = discord.Embed(
                title="✅ Activité Définie",
                description=f"Activité de {utilisateur.mention} mise à jour",
                color=discord.Color.blue()
            )
            embed.add_field(name="💬 Messages", value=f"`{messages:,}`", inline=True)
            embed.add_field(name="🎤 Temps Vocal", value=f"`{temps_vocal:,}` min", inline=True)
            embed.add_field(name="⭐ EXP Calculée", value=f"`{total_exp:,}`", inline=True)
            embed.add_field(name="📊 Niveau", value=f"{old_level} → {new_level}", inline=False)
            
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            await interaction.response.send_message("❌ Erreur lors de la mise à jour.", ephemeral=True)

    @app_commands.command(name="sync-rewards", description="Synchronise les récompenses d'un utilisateur (Admin)")
    @app_commands.describe(utilisateur="L'utilisateur dont synchroniser les récompenses")
    async def sync_rewards_command(self, interaction: discord.Interaction, utilisateur: discord.Member):
        """Synchronise les récompenses d'un utilisateur avec son niveau actuel"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if not self.db_ready:
            await interaction.response.send_message("❌ Base de données non disponible.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        try:
            await self.sync_user_rewards(utilisateur.id)
            
            embed = discord.Embed(
                title="✅ Récompenses Synchronisées",
                description=f"Les récompenses de {utilisateur.mention} ont été synchronisées avec son niveau actuel.",
                color=discord.Color.green()
            )
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send("❌ Erreur lors de la synchronisation.", ephemeral=True)

    @app_commands.command(name="toggle-remove-previous", description="Active/désactive la suppression des récompenses précédentes (Admin)")
    async def toggle_remove_previous(self, interaction: discord.Interaction):
        """Toggle la suppression des récompenses précédentes"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        self.config['remove_previous_rewards'] = not self.config['remove_previous_rewards']
        status = "activée" if self.config['remove_previous_rewards'] else "désactivée"
        
        embed = discord.Embed(
            title="⚙️ Configuration Mise à Jour",
            description=f"La suppression des récompenses précédentes est maintenant **{status}**",
            color=discord.Color.blue()
        )
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="level-debug", description="Informations de debug pour le système de niveau (Admin)")
    async def level_debug(self, interaction: discord.Interaction):
        """Commande de debug pour vérifier l'état du système"""
        if not self.is_admin(interaction.user):
            await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🔧 Debug - Système de Niveaux",
            color=discord.Color.blue()
        )
        
        # État de la base de données
        db_status = "✅ Connectée" if self.db_ready else "❌ Déconnectée"
        embed.add_field(name="Base de Données", value=db_status, inline=True)
        
        # Fichier de base de données
        embed.add_field(name="Fichier DB", value=f"`{self.db_path}`", inline=True)
        
        # Taille du fichier
        try:
            db_size = os.path.getsize(self.db_path) / 1024  # KB
            embed.add_field(name="Taille DB", value=f"`{db_size:.1f} KB`", inline=True)
        except:
            embed.add_field(name="Taille DB", value="`N/A`", inline=True)
        
        # Configuration
        remove_prev = "✅ Activée" if self.config['remove_previous_rewards'] else "❌ Désactivée"
        embed.add_field(name="Suppression Précédentes", value=remove_prev, inline=True)
        
        # Cache
        embed.add_field(name="Utilisateurs en vocal", value=f"`{len(self.voice_times)}`", inline=True)
        embed.add_field(name="Cooldowns actifs", value=f"`{len(self.message_cooldowns)}`", inline=True)
        
        # Tâches
        voice_task_status = "✅ Active" if hasattr(self, 'voice_exp_task') and not self.voice_exp_task.is_being_cancelled() else "❌ Inactive"
        embed.add_field(name="Tâche Vocal", value=voice_task_status, inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(LevelingSystem(bot))
