# modules/mentions.py
import discord
from discord.ext import commands
import aiosqlite
import os
from datetime import datetime, timedelta
import asyncio

class MentionsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "mentions_usage.db"
        asyncio.create_task(self.setup_database())

    async def setup_database(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS mention_usage (
                    user_id INTEGER PRIMARY KEY,
                    date TEXT,
                    usage_count INTEGER DEFAULT 0,
                    last_used TEXT
                )
            ''')
            await db.commit()

    async def get_user_usage(self, user_id: int):
        today = datetime.now().strftime('%Y-%m-%d')
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT date, usage_count, last_used FROM mention_usage WHERE user_id = ?', (user_id,)) as cursor:
                row = await cursor.fetchone()
                if not row or row[0] != today:
                    return 0, None
                return row[1], datetime.fromisoformat(row[2]) if row[2] else None

    async def update_user_usage(self, user_id: int):
        today = datetime.now().strftime('%Y-%m-%d')
        now = datetime.now().isoformat()
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT OR REPLACE INTO mention_usage (user_id, date, usage_count, last_used)
                VALUES (?, ?, 
                    CASE 
                        WHEN (SELECT date FROM mention_usage WHERE user_id = ?) = ? 
                        THEN (SELECT usage_count FROM mention_usage WHERE user_id = ?) + 1
                        ELSE 1
                    END,
                    ?
                )
            ''', (user_id, today, user_id, today, user_id, now))
            await db.commit()

    def has_required_role(self, member):
        required_roles = [
            int(os.getenv('MODERATOR_ROLE_ID')),
            int(os.getenv('SEIGNEUR_ROLE_ID', '0')),
            int(os.getenv('ANIMATOR_ROLE_ID'))
        ]
        return any(role.id in required_roles for role in member.roles)

    def is_seigneur(self, member):
        seigneur_role_id = int(os.getenv('SEIGNEUR_ROLE_ID', '0'))
        return any(role.id == seigneur_role_id for role in member.roles)

    @discord.app_commands.command(name="mention", description="Mentionner les rôles d'animation")
    @discord.app_commands.describe(type="Type de mention à envoyer")
    @discord.app_commands.choices(type=[
        discord.app_commands.Choice(name="Film", value="film"),
        discord.app_commands.Choice(name="Animation", value="animation"),
        discord.app_commands.Choice(name="Jeux", value="jeux")
    ])
    async def mention_command(self, interaction: discord.Interaction, type: str):
        if not self.has_required_role(interaction.user):
            await interaction.response.send_message("❌ Vous n'avez pas les permissions nécessaires.", ephemeral=True)
            return

        # Check cooldown and daily limit for non-Seigneurs
        if not self.is_seigneur(interaction.user):
            usage_count, last_used = await self.get_user_usage(interaction.user.id)
            
            # Check daily limit
            if usage_count >= 2:
                await interaction.response.send_message("❌ Limite quotidienne atteinte (2/2).", ephemeral=True)
                return
            
            # Check 4h cooldown
            if last_used and datetime.now() - last_used < timedelta(hours=4):
                remaining = timedelta(hours=4) - (datetime.now() - last_used)
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes = remainder // 60
                await interaction.response.send_message(f"❌ Cooldown actif. Temps restant: {hours}h {minutes}m", ephemeral=True)
                return

        # Get channel
        animation_channel_id = int(os.getenv('ANIMATION_CHANNEL_ID', '0'))
        animation_channel = self.bot.get_channel(animation_channel_id)
        
        if not animation_channel:
            await interaction.response.send_message("❌ Canal d'animation introuvable.", ephemeral=True)
            return

        # Build message based on type
        if type == "film":
            film_role_id = int(os.getenv('FILM_ROLE_ID', '0'))
            message = f"<@&{film_role_id}>"
        elif type == "jeux":
            jeu_role_id = int(os.getenv('JEU_ROLE_ID', '0'))
            message = f"<@&{jeu_role_id}>"
        else:  # animation
            animation_role_id = int(os.getenv('ANIMATION_ROLE_ID', '0'))
            message = f"<@&{animation_role_id}>"

        # Send message
        await animation_channel.send(message)
        
        # Update usage for non-Seigneurs
        if not self.is_seigneur(interaction.user):
            await self.update_user_usage(interaction.user.id)

        await interaction.response.send_message("", ephemeral=True, delete_after=1)

async def setup(bot):
    await bot.add_cog(MentionsCog(bot))
