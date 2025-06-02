# modules/profile.py
import discord
from discord.ext import commands
from discord import app_commands
import os
from typing import Optional
import math

class ProfileSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
    def get_leveling_system(self):
        """Récupère le système de niveau"""
        return self.bot.get_cog('LevelingSystem')

    async def display_profile(self, interaction: discord.Interaction, utilisateur: Optional[discord.Member] = None):
        """Affiche le profil d'un utilisateur"""
        leveling_system = self.get_leveling_system()
        if not leveling_system:
            await interaction.response.send_message("❌ Système de niveau non disponible.", ephemeral=True)
            return
        
        if not leveling_system.db_ready:
            await interaction.response.send_message("❌ Base de données non disponible. Le système de niveaux est temporairement indisponible.", ephemeral=True)
            return
        
        target = utilisateur or interaction.user
        user_data = await leveling_system.get_user_data(target.id)
        
        current_level = user_data['level']
        current_exp = user_data['exp']
        exp_for_current = leveling_system.calculate_exp_for_level(current_level)
        exp_for_next = leveling_system.calculate_exp_for_level(current_level + 1)
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
        embed.add_field(name="🚀 Bumps", value=f"`{user_data['bumps_count']:,}`", inline=True)
        
        # Barre de progression
        progress_bar_length = 20
        progress = min(exp_progress / exp_needed, 1.0) if exp_needed > 0 else 1.0
        filled_length = int(progress_bar_length * progress)
        bar = "█" * filled_length + "░" * (progress_bar_length - filled_length)
        embed.add_field(name="📊 Progression vers le niveau suivant", value=f"`{bar}` {progress*100:.1f}%", inline=False)
        
        # Prochaine récompense
        next_reward_level = None
        for level in sorted(leveling_system.config['level_rewards'].keys()):
            if level > current_level:
                next_reward_level = level
                break
        
        if next_reward_level:
            role_id = leveling_system.config['level_rewards'][next_reward_level]
            role = interaction.guild.get_role(role_id)
            role_mention = role.mention if role else f"<@&{role_id}>"
            embed.add_field(name="🎁 Prochaine Récompense", value=f"Niveau {next_reward_level}: {role_mention}", inline=False)
        
        embed.set_footer(text=f"Serveur: {interaction.guild.name}")
        await interaction.response.send_message(embed=embed)

    async def display_bump_leaderboard(self, interaction: discord.Interaction, page: Optional[int] = 1):
        """Affiche le classement des bumps"""
        leveling_system = self.get_leveling_system()
        if not leveling_system:
            await interaction.response.send_message("❌ Système de niveau non disponible.", ephemeral=True)
            return
        
        if not leveling_system.db_ready:
            await interaction.response.send_message("❌ Base de données non disponible.", ephemeral=True)
            return
        
        page = max(1, page)
        offset = (page - 1) * 10
        
        try:
            # Récupérer les données du leaderboard des bumps
            bump_data = await leveling_system.get_bump_leaderboard(50)  # Récupère plus pour la pagination
            
            if not bump_data:
                embed = discord.Embed(
                    title="🚀 Classement des Bumps",
                    description="Aucun bump enregistré pour le moment.",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)
                return
            
            # Pagination manuelle
            total_users = len(bump_data)
            max_pages = math.ceil(total_users / 10) if total_users > 0 else 1
            
            start_idx = offset
            end_idx = min(offset + 10, total_users)
            page_data = bump_data[start_idx:end_idx]
            
            if not page_data:
                embed = discord.Embed(
                    title="🚀 Classement des Bumps",
                    description="Aucun utilisateur trouvé pour cette page.",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)
                return
        except Exception as e:
            await interaction.response.send_message("❌ Erreur lors de la récupération des données.", ephemeral=True)
            return
        
        # Créer l'embed
        embed = discord.Embed(
            title="🚀 Classement des Bumps",
            color=discord.Color.gold()
        )
        
        description = ""
        for i, (user_id, bumps_count) in enumerate(page_data, start=start_idx + 1):
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
            
            description += f"{emoji} **{user_name}** - `{bumps_count:,}` bumps\n"
        
        embed.description = description
        
        # Informations de pagination
        embed.set_footer(text=f"Page {page}/{max_pages} • {total_users} utilisateurs au total")
        
        await interaction.response.send_message(embed=embed)

    # Commandes de profil
    @app_commands.command(name="profil", description="Affiche ton profil ou celui d'un autre utilisateur")
    @app_commands.describe(utilisateur="L'utilisateur dont voir le profil")
    async def profil(self, interaction: discord.Interaction, utilisateur: Optional[discord.Member] = None):
        """Affiche le profil d'un utilisateur"""
        await self.display_profile(interaction, utilisateur)

    @app_commands.command(name="profile", description="Affiche ton profil ou celui d'un autre utilisateur")
    @app_commands.describe(utilisateur="L'utilisateur dont voir le profil")
    async def profile(self, interaction: discord.Interaction, utilisateur: Optional[discord.Member] = None):
        """Alias pour /profil"""
        await self.display_profile(interaction, utilisateur)

    @app_commands.command(name="niveau", description="Affiche tes informations de niveau")
    @app_commands.describe(utilisateur="L'utilisateur dont voir le niveau")
    async def niveau(self, interaction: discord.Interaction, utilisateur: Optional[discord.Member] = None):
        """Affiche le profil d'un utilisateur"""
        await self.display_profile(interaction, utilisateur)

    @app_commands.command(name="level", description="Affiche tes informations de niveau")
    @app_commands.describe(utilisateur="L'utilisateur dont voir le niveau")
    async def level(self, interaction: discord.Interaction, utilisateur: Optional[discord.Member] = None):
        """Affiche le profil d'un utilisateur"""
        await self.display_profile(interaction, utilisateur)

    # Classement des bumps
    @app_commands.command(name="bump-classement", description="Affiche le classement des bumps")
    @app_commands.describe(page="Page du classement")
    async def bump_leaderboard_fr(self, interaction: discord.Interaction, page: Optional[int] = 1):
        """Affiche le classement des bumps"""
        await self.display_bump_leaderboard(interaction, page)

    @app_commands.command(name="bump-leaderboard", description="Affiche le classement des bumps")
    @app_commands.describe(page="Page du classement")
    async def bump_leaderboard(self, interaction: discord.Interaction, page: Optional[int] = 1):
        """Affiche le classement des bumps"""
        await self.display_bump_leaderboard(interaction, page)

    @app_commands.command(name="topbump", description="Affiche le classement des bumps")
    @app_commands.describe(page="Page du classement")
    async def topbump(self, interaction: discord.Interaction, page: Optional[int] = 1):
        """Affiche le classement des bumps"""
        await self.display_bump_leaderboard(interaction, page)

async def setup(bot):
    await bot.add_cog(ProfileSystem(bot))
