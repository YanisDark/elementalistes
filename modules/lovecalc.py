# modules/lovecalc/lovecalc.py
import discord
from discord.ext import commands
import aiosqlite
import hashlib
import os
from typing import Optional

class LoveCalc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "lovecalc.db"
        
    async def setup_database(self):
        """Initialize database table if not exists"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS love_results (
                    user_pair_hash TEXT PRIMARY KEY,
                    user1_id INTEGER,
                    user2_id INTEGER,
                    love_percentage INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()
    
    def generate_user_hash(self, user1_id: int, user2_id: int) -> str:
        """Generate consistent hash for user pair"""
        sorted_ids = sorted([user1_id, user2_id])
        combined = f"{sorted_ids[0]}-{sorted_ids[1]}"
        return hashlib.md5(combined.encode()).hexdigest()
    
    async def get_or_calculate_love(self, user1_id: int, user2_id: int) -> int:
        """Get existing love percentage or calculate new one"""
        user_hash = self.generate_user_hash(user1_id, user2_id)
        
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT love_percentage FROM love_results WHERE user_pair_hash = ?",
                (user_hash,)
            )
            result = await cursor.fetchone()
            
            if result:
                return result[0]
            
            # Calculate new percentage based on user IDs
            combined_id = abs(user1_id + user2_id)
            love_percentage = combined_id % 101
            
            await db.execute("""
                INSERT INTO love_results (user_pair_hash, user1_id, user2_id, love_percentage)
                VALUES (?, ?, ?, ?)
            """, (user_hash, user1_id, user2_id, love_percentage))
            await db.commit()
            
            return love_percentage
    
    def get_love_comment(self, percentage: int) -> str:
        """Get comment based on love percentage"""
        if percentage == 0:
            return "💔 Aucune affinité... Il vaut mieux rester amis !"
        elif 1 <= percentage <= 20:
            return "😐 Il y a peut-être quelque chose, mais c'est très léger..."
        elif 21 <= percentage <= 40:
            return "😊 Une petite étincelle ! Qui sait ce que l'avenir réserve ?"
        elif 41 <= percentage <= 60:
            return "💕 Une belle complicité se dessine ! C'est prometteur !"
        elif 61 <= percentage <= 80:
            return "💖 Wow ! Il y a de la magie dans l'air ! L'amour est là !"
        elif 81 <= percentage <= 99:
            return "💝 C'est de l'amour fou ! Vous êtes faits l'un pour l'autre !"
        else:  # 100
            return "💍 AMOUR PARFAIT ! Les étoiles se sont alignées ! C'est le destin !"
    
    def check_incantations_channel(self, ctx):
        """Check if command is used in INCANTATIONS channel"""
        incantations_id = int(os.getenv('INCANTATIONS_CHANNEL_ID', '0'))
        return ctx.channel.id == incantations_id
    
    @commands.command(name='lovecalc', aliases=['amour'])
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def lovecalc_prefix(self, ctx, user1: Optional[discord.Member] = None, user2: Optional[discord.Member] = None):
        """Calculate love percentage between users (prefix command)"""
        if not self.check_incantations_channel(ctx):
            return
        
        if user1 is None:
            await ctx.send("❌ Tu dois mentionner au moins un utilisateur !")
            return
        
        if user2 is None:
            # Calculate love between author and user1
            target_user = user1
            author = ctx.author
        else:
            # Calculate love between user1 and user2
            target_user = user2
            author = user1
        
        if author.id == target_user.id:
            await ctx.send("😅 Tu ne peux pas calculer l'amour avec toi-même !")
            return
        
        percentage = await self.get_or_calculate_love(author.id, target_user.id)
        comment = self.get_love_comment(percentage)
        
        message = f"💘 **Calcul d'amour** 💘\n"
        message += f"**{author.display_name}** 💕 **{target_user.display_name}**\n\n"
        message += f"🎯 **Pourcentage d'amour : {percentage}%**\n"
        message += f"{comment}"
        
        await ctx.send(message)
    
    @discord.app_commands.command(name="lovecalc", description="Calcule le pourcentage d'amour entre deux utilisateurs")
    @discord.app_commands.describe(
        user1="Premier utilisateur (optionnel si tu veux calculer avec toi)",
        user2="Deuxième utilisateur (obligatoire si user1 est spécifié)"
    )
    @discord.app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def lovecalc_slash(self, interaction: discord.Interaction, user1: Optional[discord.Member] = None, user2: Optional[discord.Member] = None):
        """Calculate love percentage between users (slash command)"""
        incantations_id = int(os.getenv('INCANTATIONS_CHANNEL_ID', '0'))
        if interaction.channel.id != incantations_id:
            await interaction.response.send_message("❌ Cette commande ne peut être utilisée que dans le canal des incantations !", ephemeral=True)
            return
        
        if user1 is None:
            await interaction.response.send_message("❌ Tu dois mentionner au moins un utilisateur !", ephemeral=True)
            return
        
        if user2 is None:
            # Calculate love between author and user1
            target_user = user1
            author = interaction.user
        else:
            # Calculate love between user1 and user2
            target_user = user2
            author = user1
        
        if author.id == target_user.id:
            await interaction.response.send_message("😅 Tu ne peux pas calculer l'amour avec toi-même !", ephemeral=True)
            return
        
        percentage = await self.get_or_calculate_love(author.id, target_user.id)
        comment = self.get_love_comment(percentage)
        
        message = f"💘 **Calcul d'amour** 💘\n"
        message += f"**{author.display_name}** 💕 **{target_user.display_name}**\n\n"
        message += f"🎯 **Pourcentage d'amour : {percentage}%**\n"
        message += f"{comment}"
        
        await interaction.response.send_message(message)
    
    @lovecalc_prefix.error
    async def lovecalc_prefix_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.author.send(f"⏰ Tu dois attendre {error.retry_after:.1f} secondes avant d'utiliser cette commande à nouveau !")
    
    @lovecalc_slash.error
    async def lovecalc_slash_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            await interaction.response.send_message(f"⏰ Tu dois attendre {error.retry_after:.1f} secondes avant d'utiliser cette commande à nouveau !", ephemeral=True)
    
    @commands.Cog.listener()
    async def on_ready(self):
        await self.setup_database()

async def setup(bot):
    await bot.add_cog(LoveCalc(bot))
