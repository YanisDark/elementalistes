# modules/rank_management.py
import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv

load_dotenv()

class RankManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # Role IDs from environment
        self.oracle_role_id = int(os.getenv('ORACLE_ROLE_ID'))
        self.moderator_role_id = int(os.getenv('MODERATOR_ROLE_ID'))
        self.member_role_id = int(os.getenv('MEMBER_ROLE_ID'))
        self.gerant_staff_role_id = int(os.getenv('GERANT_STAFF_ROLE_ID'))
        self.conseil_role_id = int(os.getenv('CONSEIL_ROLE_ID'))
        
    def has_permission(self, member, action, target_member):
        """Check if member has permission to perform the action"""
        if member.guild_permissions.administrator:
            return True
            
        member_roles = [role.id for role in member.roles]
        target_roles = [role.id for role in target_member.roles]
        
        # GERANT_STAFF can do everything
        if self.gerant_staff_role_id in member_roles:
            return True
            
        # MODERATOR can only demote Oracle to Member
        if self.moderator_role_id in member_roles and action == "demote" and self.oracle_role_id in target_roles:
            return True
            
        return False
    
    @app_commands.command(name="promote", description="Promouvoir un utilisateur")
    @app_commands.describe(user="L'utilisateur à promouvoir")
    async def promote(self, interaction: discord.Interaction, user: discord.Member):
        if not self.has_permission(interaction.user, "promote", user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
            
        guild = interaction.guild
        target_roles = [role.id for role in user.roles]
        
        # Member → Oracle
        if self.member_role_id in target_roles and self.oracle_role_id not in target_roles and self.moderator_role_id not in target_roles:
            member_role = guild.get_role(self.member_role_id)
            oracle_role = guild.get_role(self.oracle_role_id)
            conseil_role = guild.get_role(self.conseil_role_id)
            
            try:
                await user.remove_roles(member_role, reason=f"Promu par {interaction.user}")
                await user.add_roles(oracle_role, reason=f"Promu par {interaction.user}")
                
                if conseil_role:
                    await user.add_roles(conseil_role, reason=f"Promu Oracle par {interaction.user}")
                
                try:
                    await user.send(f"🎉 Félicitations ! Vous avez été promu au rang d'**Oracle** sur {guild.name} !")
                except discord.Forbidden:
                    pass
                    
                await interaction.response.send_message(f"✅ {user.mention} a été promu de **Membre** à **Oracle**.")
                
            except discord.Forbidden:
                await interaction.response.send_message("❌ Je n'ai pas les permissions nécessaires pour modifier les rôles.", ephemeral=True)
                
        # Oracle → Moderator
        elif self.oracle_role_id in target_roles:
            oracle_role = guild.get_role(self.oracle_role_id)
            moderator_role = guild.get_role(self.moderator_role_id)
            
            try:
                await user.remove_roles(oracle_role, reason=f"Promu par {interaction.user}")
                await user.add_roles(moderator_role, reason=f"Promu par {interaction.user}")
                
                try:
                    await user.send(f"🎉 Félicitations ! Vous avez été promu au rang de **Gardien** sur {guild.name} !")
                except discord.Forbidden:
                    pass
                    
                await interaction.response.send_message(f"✅ {user.mention} a été promu d'**Oracle** à **Gardien**.")
                
            except discord.Forbidden:
                await interaction.response.send_message("❌ Je n'ai pas les permissions nécessaires pour modifier les rôles.", ephemeral=True)
                
        else:
            await interaction.response.send_message("❌ Cet utilisateur ne peut pas être promu ou a déjà le rang maximum.", ephemeral=True)
    
    @app_commands.command(name="demote", description="Rétrograder un utilisateur")
    @app_commands.describe(user="L'utilisateur à rétrograder")
    async def demote(self, interaction: discord.Interaction, user: discord.Member):
        if not self.has_permission(interaction.user, "demote", user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
            
        guild = interaction.guild
        target_roles = [role.id for role in user.roles]
        
        # Moderator → Oracle
        if self.moderator_role_id in target_roles:
            moderator_role = guild.get_role(self.moderator_role_id)
            oracle_role = guild.get_role(self.oracle_role_id)
            conseil_role = guild.get_role(self.conseil_role_id)
            
            try:
                await user.remove_roles(moderator_role, reason=f"Rétrogradé par {interaction.user}")
                await user.add_roles(oracle_role, reason=f"Rétrogradé par {interaction.user}")
                
                if conseil_role:
                    await user.add_roles(conseil_role, reason=f"Rétrogradé à Oracle par {interaction.user}")
                
                try:
                    await user.send(f"📉 Vous avez été rétrogradé au rang d'**Oracle** sur {guild.name}.")
                except discord.Forbidden:
                    pass
                    
                await interaction.response.send_message(f"✅ {user.mention} a été rétrogradé de **Gardien** à **Oracle**.")
                
            except discord.Forbidden:
                await interaction.response.send_message("❌ Je n'ai pas les permissions nécessaires pour modifier les rôles.", ephemeral=True)
                
        # Oracle → Member
        elif self.oracle_role_id in target_roles:
            oracle_role = guild.get_role(self.oracle_role_id)
            member_role = guild.get_role(self.member_role_id)
            conseil_role = guild.get_role(self.conseil_role_id)
            
            try:
                await user.remove_roles(oracle_role, reason=f"Rétrogradé par {interaction.user}")
                await user.add_roles(member_role, reason=f"Rétrogradé par {interaction.user}")
                
                # Remove CONSEIL_ROLE when demoted from Oracle
                if conseil_role and conseil_role in user.roles:
                    await user.remove_roles(conseil_role, reason=f"Rétrogradé d'Oracle par {interaction.user}")
                
                try:
                    await user.send(f"📉 Vous avez été rétrogradé au rang de **Membre** sur {guild.name}.")
                except discord.Forbidden:
                    pass
                    
                await interaction.response.send_message(f"✅ {user.mention} a été rétrogradé d'**Oracle** à **Membre**.")
                
            except discord.Forbidden:
                await interaction.response.send_message("❌ Je n'ai pas les permissions nécessaires pour modifier les rôles.", ephemeral=True)
                
        else:
            await interaction.response.send_message("❌ Cet utilisateur ne peut pas être rétrogradé ou a déjà le rang minimum.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(RankManagement(bot))
