# modules/counters.py
import discord
from discord.ext import commands, tasks
import logging
import os
from typing import Optional

class MemberCounter(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.category_id = int(os.getenv('INFO_CATEGORY_ID', '1345049999070990368'))
        self.guild_id = int(os.getenv('GUILD_ID'))
        
    async def cog_load(self):
        """Démarre le compteur lors du chargement du cog"""
        self.update_member_counter.start()
        
    def cog_unload(self):
        """Arrête le compteur lors du déchargement"""
        self.update_member_counter.cancel()
        
    @tasks.loop(minutes=10)
    async def update_member_counter(self):
        """Met à jour le compteur de membres toutes les 10 minutes"""
        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                return
                
            # Compter seulement les vrais membres (pas les bots)
            member_count = len([member for member in guild.members if not member.bot])
            
            # Obtenir la catégorie
            category = guild.get_channel(self.category_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                logging.error(f"Catégorie {self.category_id} introuvable ou invalide")
                return
                
            # Nouveau nom avec le compteur
            new_name = f"✼ Informations - {member_count} membres"
            
            # Mettre à jour seulement si le nom a changé
            if category.name != new_name:
                await category.edit(name=new_name)
                logging.info(f"Compteur de membres mis à jour: {member_count}")
                
        except Exception as e:
            logging.error(f"Erreur lors de la mise à jour du compteur: {e}")
    
    @update_member_counter.before_loop
    async def before_update_member_counter(self):
        """Attendre que le bot soit prêt avant de commencer le compteur"""
        await self.bot.wait_until_ready()
        
    async def force_update(self):
        """Force la mise à jour du compteur (pour les commandes manuelles)"""
        await self.update_member_counter()

async def setup(bot):
    await bot.add_cog(MemberCounter(bot))
