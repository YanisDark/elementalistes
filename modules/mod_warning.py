# modules/mod_warning.py
import discord
from discord.ext import commands
import os
from datetime import datetime, timedelta
import asyncio

class ModWarning(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.commandes_channel_id = int(os.getenv('COMMANDES_CHANNEL_ID'))
        self.target_channel_id = 1379086125141852180
        
    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        await asyncio.sleep(1)
        try:
            async for entry in guild.audit_logs(action=discord.AuditLogAction.ban, limit=1):
                if entry.target.id == user.id and not entry.user.bot:
                    await self._send_warning(entry.user, "banni", user)
                    break
        except:
            pass
    
    @commands.Cog.listener()
    async def on_member_remove(self, member):
        await asyncio.sleep(1)
        try:
            async for entry in member.guild.audit_logs(action=discord.AuditLogAction.kick, limit=1):
                if entry.target.id == member.id and not entry.user.bot:
                    await self._send_warning(entry.user, "expulsé", member)
                    break
        except:
            pass
    
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.timed_out_until != after.timed_out_until:
            if after.timed_out_until and after.timed_out_until > discord.utils.utcnow():
                await asyncio.sleep(1)
                try:
                    async for entry in after.guild.audit_logs(action=discord.AuditLogAction.member_update, limit=3):
                        if entry.target.id == after.id and not entry.user.bot:
                            await self._send_warning(entry.user, "mis en timeout", after)
                            break
                except:
                    pass
    
    async def _send_warning(self, moderator, action, target_user):
        channel = self.bot.get_channel(self.commandes_channel_id)
        if channel:
            message = (f"{moderator.mention}, tu as {action} {target_user.mention} avec les "
                      f"fonctionnalités Discord. Utilise les commandes du bot dans <#{self.target_channel_id}> "
                      f"à la place.")
            await channel.send(message)

async def setup(bot):
    await bot.add_cog(ModWarning(bot))
