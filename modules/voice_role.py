# modules/voice_role.py

import discord
from discord.ext import commands, tasks
import os
import logging
import asyncio
from typing import Optional
from modules.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)

class VoiceRoleManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.vocal_role_id = int(os.getenv('VOCAL_ROLE_ID', 0))
        self.guild_id = int(os.getenv('GUILD_ID'))
        self.rate_limiter = get_rate_limiter()
        self.vocal_role_check.start()

    def cog_unload(self):
        self.vocal_role_check.cancel()

    async def _safe_add_role(self, member: discord.Member, role: discord.Role, reason: str = None):
        """Ajout de rôle avec rate limiting"""
        try:
            await self.rate_limiter.execute_request(
                member.add_roles(role, reason=reason),
                route=f'PATCH /guilds/{member.guild.id}/members/{member.id}',
                major_params={'guild_id': member.guild.id}
            )
            return True
        except Exception as e:
            logger.error(f"Erreur lors de l'ajout du rôle vocal pour {member.display_name}: {e}")
            return False

    async def _safe_remove_role(self, member: discord.Member, role: discord.Role, reason: str = None):
        """Retrait de rôle avec rate limiting"""
        try:
            await self.rate_limiter.execute_request(
                member.remove_roles(role, reason=reason),
                route=f'PATCH /guilds/{member.guild.id}/members/{member.id}',
                major_params={'guild_id': member.guild.id}
            )
            return True
        except Exception as e:
            logger.error(f"Erreur lors du retrait du rôle vocal pour {member.display_name}: {e}")
            return False

    @tasks.loop(minutes=5)
    async def vocal_role_check(self):
        """Vérification périodique des rôles vocaux avec rate limiting"""
        if not self.vocal_role_id:
            return
            
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        vocal_role = guild.get_role(self.vocal_role_id)
        if not vocal_role:
            logger.warning(f"Rôle vocal {self.vocal_role_id} introuvable")
            return

        # Récupérer tous les membres actuellement en vocal
        members_in_voice = set()
        for voice_channel in guild.voice_channels:
            for member in voice_channel.members:
                if not member.bot:
                    members_in_voice.add(member)

        # Batch operations pour éviter le spam de requests
        members_to_remove = []
        members_to_add = []

        # Préparer les membres à modifier
        for member in vocal_role.members:
            if member not in members_in_voice and not member.bot:
                members_to_remove.append(member)

        for member in members_in_voice:
            if vocal_role not in member.roles:
                members_to_add.append(member)

        # Traitement avec délai entre les requests pour respecter les rate limits
        for i, member in enumerate(members_to_remove):
            if await self._safe_remove_role(member, vocal_role, "Automatique: plus en vocal"):
                logger.info(f"Rôle vocal retiré à {member.display_name} (vérification périodique)")
            
            # Petit délai pour éviter les rate limits même avec le système de rate limiting
            if i % 5 == 4:  # Pause tous les 5 membres
                await asyncio.sleep(0.5)

        for i, member in enumerate(members_to_add):
            if await self._safe_add_role(member, vocal_role, "Automatique: en vocal"):
                logger.info(f"Rôle vocal ajouté à {member.display_name} (vérification périodique)")
            
            if i % 5 == 4:  # Pause tous les 5 membres
                await asyncio.sleep(0.5)

    @vocal_role_check.before_loop
    async def before_vocal_role_check(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Gestion des changements d'état vocal avec rate limiting"""
        if member.bot or not self.vocal_role_id:
            return

        guild = self.bot.get_guild(self.guild_id)
        if not guild or member.guild.id != self.guild_id:
            return

        vocal_role = guild.get_role(self.vocal_role_id)
        if not vocal_role:
            return

        # Membre rejoint un canal vocal
        if before.channel is None and after.channel is not None:
            if vocal_role not in member.roles:
                if await self._safe_add_role(member, vocal_role, "Rejoint un canal vocal"):
                    logger.info(f"Rôle vocal ajouté à {member.display_name}")

        # Membre quitte un canal vocal
        elif before.channel is not None and after.channel is None:
            if vocal_role in member.roles:
                if await self._safe_remove_role(member, vocal_role, "Quitté le canal vocal"):
                    logger.info(f"Rôle vocal retiré à {member.display_name}")

    @commands.command(name="vocal_sync")
    @commands.has_permissions(administrator=True)
    async def sync_vocal_roles(self, ctx):
        """Synchronise manuellement les rôles vocaux avec rate limiting"""
        if not self.vocal_role_id:
            await ctx.send("❌ ID du rôle vocal non configuré.")
            return

        vocal_role = ctx.guild.get_role(self.vocal_role_id)
        if not vocal_role:
            await ctx.send("❌ Rôle vocal introuvable.")
            return

        # Message initial
        embed = discord.Embed(
            title="🔄 Synchronisation en cours...",
            description="Traitement des rôles vocaux...",
            color=0xffaa00
        )
        status_msg = await self.rate_limiter.safe_send(ctx.channel, embed=embed)

        # Compter les actions
        added = 0
        removed = 0
        errors = 0

        # Récupérer tous les membres en vocal
        members_in_voice = set()
        for voice_channel in ctx.guild.voice_channels:
            for member in voice_channel.members:
                if not member.bot:
                    members_in_voice.add(member)

        # Préparer les listes
        members_to_remove = [m for m in vocal_role.members if m not in members_in_voice and not m.bot]
        members_to_add = [m for m in members_in_voice if vocal_role not in m.roles]

        total_operations = len(members_to_remove) + len(members_to_add)
        
        # Traitement avec rate limiting et mise à jour du statut
        for i, member in enumerate(members_to_remove):
            if await self._safe_remove_role(member, vocal_role, "Synchronisation manuelle"):
                removed += 1
            else:
                errors += 1

            # Mise à jour du statut tous les 10 membres
            if i % 10 == 9 or i == len(members_to_remove) - 1:
                progress = ((i + 1) / total_operations) * 100
                embed.description = f"Retrait des rôles: {i + 1}/{len(members_to_remove)} ({progress:.1f}%)"
                try:
                    await self.rate_limiter.safe_edit(status_msg, embed=embed)
                except:
                    pass

            if i % 5 == 4:
                await asyncio.sleep(0.3)

        # Ajout des rôles
        for i, member in enumerate(members_to_add):
            if await self._safe_add_role(member, vocal_role, "Synchronisation manuelle"):
                added += 1
            else:
                errors += 1

            # Mise à jour du statut
            if i % 10 == 9 or i == len(members_to_add) - 1:
                total_processed = len(members_to_remove) + i + 1
                progress = (total_processed / total_operations) * 100
                embed.description = f"Ajout des rôles: {i + 1}/{len(members_to_add)} ({progress:.1f}%)"
                try:
                    await self.rate_limiter.safe_edit(status_msg, embed=embed)
                except:
                    pass

            if i % 5 == 4:
                await asyncio.sleep(0.3)

        # Résultat final
        final_embed = discord.Embed(
            title="🔄 Synchronisation terminée",
            color=0x00ff00 if errors == 0 else 0xff6600
        )
        
        result_text = f"✅ **{added}** rôles ajoutés\n❌ **{removed}** rôles retirés"
        if errors > 0:
            result_text += f"\n⚠️ **{errors}** erreurs"
        
        final_embed.description = result_text
        
        # Statistiques du rate limiter
        metrics = self.rate_limiter.get_metrics()
        final_embed.add_field(
            name="📊 Rate Limiter",
            value=f"Rate limited: {metrics['rate_limited_requests']}\nRetries: {metrics['retry_attempts']}",
            inline=True
        )

        await self.rate_limiter.safe_edit(status_msg, embed=final_embed)

    @commands.command(name="vocal_stats")
    @commands.has_permissions(administrator=True)
    async def vocal_stats(self, ctx):
        """Affiche les statistiques du système de rôles vocaux"""
        if not self.vocal_role_id:
            await ctx.send("❌ ID du rôle vocal non configuré.")
            return

        vocal_role = ctx.guild.get_role(self.vocal_role_id)
        if not vocal_role:
            await ctx.send("❌ Rôle vocal introuvable.")
            return

        # Comptage des membres
        members_in_voice = set()
        voice_channels_count = len(ctx.guild.voice_channels)
        
        for voice_channel in ctx.guild.voice_channels:
            for member in voice_channel.members:
                if not member.bot:
                    members_in_voice.add(member)

        members_with_role = len([m for m in vocal_role.members if not m.bot])
        members_should_have = len(members_in_voice)
        
        # Détection des désynchronisations
        should_add = len([m for m in members_in_voice if vocal_role not in m.roles])
        should_remove = len([m for m in vocal_role.members if m not in members_in_voice and not m.bot])

        embed = discord.Embed(
            title="📊 Statistiques Rôles Vocaux",
            color=0x00ff00 if should_add == 0 and should_remove == 0 else 0xff6600
        )

        embed.add_field(name="👥 Membres en vocal", value=members_should_have, inline=True)
        embed.add_field(name="🎭 Avec le rôle", value=members_with_role, inline=True)
        embed.add_field(name="📢 Canaux vocaux", value=voice_channels_count, inline=True)
        
        if should_add > 0 or should_remove > 0:
            embed.add_field(name="⚠️ Désynchronisé", value="Oui", inline=True)
            embed.add_field(name="➕ À ajouter", value=should_add, inline=True)
            embed.add_field(name="➖ À retirer", value=should_remove, inline=True)
        else:
            embed.add_field(name="✅ Synchronisé", value="Oui", inline=True)

        # Métriques du rate limiter
        metrics = self.rate_limiter.get_metrics()
        embed.add_field(
            name="📈 Rate Limiter",
            value=f"Requêtes: {metrics['total_requests']}\nRate limited: {metrics['rate_limited_requests']}\nÉchecs: {metrics['failed_requests']}",
            inline=False
        )

        await self.rate_limiter.safe_send(ctx.channel, embed=embed)

async def setup(bot):
    await bot.add_cog(VoiceRoleManager(bot))
