# modules/8ball.py
import discord
from discord.ext import commands
import random
import asyncio
from typing import Dict
import time

class EightBall(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cooldowns: Dict[int, float] = {}
        
        # Réponses authentiques de la boule magique (traduites en français)
        self.responses = [
            # Positives (10)
            "C'est certain",
            "C'est décidément ainsi",
            "Sans aucun doute",
            "Oui, définitivement",
            "Tu peux t'y fier",
            "Comme je le vois, oui",
            "Très probablement",
            "Les perspectives sont bonnes",
            "Oui",
            "Les signes pointent vers oui",
            
            # Négatives (5)
            "Ne compte pas là-dessus",
            "Ma réponse est non",
            "Mes sources disent non",
            "Les perspectives ne sont pas bonnes",
            "Très douteux",
            
            # Neutres/Réessaie plus tard (5)
            "Réponse floue, réessaie",
            "Demande encore plus tard",
            "Mieux vaut ne pas te le dire maintenant",
            "Impossible de prédire maintenant",
            "Concentre-toi et redemande"
        ]
    
    def _check_cooldown(self, user_id: int) -> bool:
        """Vérifie si l'utilisateur est en cooldown"""
        current_time = time.time()
        if user_id in self.cooldowns:
            time_left = self.cooldowns[user_id] - current_time
            if time_left > 0:
                return False
        return True
    
    def _set_cooldown(self, user_id: int):
        """Définit le cooldown pour l'utilisateur"""
        self.cooldowns[user_id] = time.time() + 15
    
    def _get_cooldown_time(self, user_id: int) -> int:
        """Retourne le temps restant du cooldown"""
        current_time = time.time()
        if user_id in self.cooldowns:
            time_left = self.cooldowns[user_id] - current_time
            return max(0, int(time_left))
        return 0

    @discord.app_commands.command(name="8ball", description="Pose une question à la boule magique !")
    @discord.app_commands.describe(question="Ta question pour la boule magique")
    async def eightball_slash(self, interaction: discord.Interaction, question: str):
        if not self._check_cooldown(interaction.user.id):
            cooldown_time = self._get_cooldown_time(interaction.user.id)
            await interaction.response.send_message(
                f"⏰ Tu dois attendre encore {cooldown_time} secondes avant de poser une autre question !",
                ephemeral=True
            )
            return
        
        self._set_cooldown(interaction.user.id)
        response = random.choice(self.responses)
        
        await interaction.response.send_message(f"🎱 {response}")

    @commands.command(name="8ball", aliases=["8b", "eightball"])
    async def eightball_prefix(self, ctx, *, question=None):
        if question is None:
            await ctx.reply("❓ Tu dois poser une question à la boule magique !")
            return
        
        if not self._check_cooldown(ctx.author.id):
            cooldown_time = self._get_cooldown_time(ctx.author.id)
            msg = await ctx.reply(f"⏰ Tu dois attendre encore {cooldown_time} secondes avant de poser une autre question !")
            await asyncio.sleep(cooldown_time)
            await msg.delete()
            return
        
        self._set_cooldown(ctx.author.id)
        response = random.choice(self.responses)
        
        await ctx.reply(f"🎱 {response}")

async def setup(bot):
    await bot.add_cog(EightBall(bot))
