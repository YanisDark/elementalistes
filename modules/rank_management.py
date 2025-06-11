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
        self.admin_role_id = int(os.getenv('ADMIN_ROLE_ID'))
        self.animator_role_id = int(os.getenv('ANIMATOR_ROLE_ID'))
        self.moderator_role_id = int(os.getenv('MODERATOR_ROLE_ID'))
        self.oracle_role_id = int(os.getenv('ORACLE_ROLE_ID'))
        self.member_role_id = int(os.getenv('MEMBER_ROLE_ID'))
        
        # Optional roles (may not exist in env)
        try:
            self.gerant_staff_role_id = int(os.getenv('GERANT_STAFF_ROLE_ID'))
        except (TypeError, ValueError):
            self.gerant_staff_role_id = None
            
        try:
            self.conseil_role_id = int(os.getenv('CONSEIL_ROLE_ID'))
        except (TypeError, ValueError):
            self.conseil_role_id = None
        
        # Rank hierarchy
        self.ranks = {
            "membre": {
                "role_id": self.member_role_id,
                "name": "Membre",
                "level": 1
            },
            "oracle": {
                "role_id": self.oracle_role_id,
                "name": "Oracle",
                "level": 2
            },
            "gardien": {
                "role_id": self.moderator_role_id,
                "name": "Gardien",
                "level": 3
            },
            "invocateur": {
                "role_id": self.animator_role_id,
                "name": "Invocateur",
                "level": 4
            }
        }
        
    def has_permission(self, member, target_rank, target_user=None):
        """Check if member has permission to set the target rank"""
        if member.guild_permissions.administrator:
            return True
            
        member_roles = [role.id for role in member.roles]
        
        # Admin can do everything
        if self.admin_role_id in member_roles:
            return True
            
        # Gerant staff can manage all ranks except admin
        if self.gerant_staff_role_id and self.gerant_staff_role_id in member_roles:
            return True
            
        # Moderators permissions
        if self.moderator_role_id in member_roles:
            # Can manage Oracle and Membre
            if target_rank in ["oracle", "membre"]:
                return True
            # Can derank Invocateur to Membre only
            if target_rank == "membre" and target_user:
                target_roles = [role.id for role in target_user.roles]
                if self.animator_role_id in target_roles:
                    return True
            
        return False
    
    async def remove_all_rank_roles(self, user):
        """Remove all rank roles from user except Member role"""
        roles_to_remove = []
        
        for rank_key, rank_data in self.ranks.items():
            # Never remove Member role
            if rank_key == "membre":
                continue
                
            role = user.guild.get_role(rank_data["role_id"])
            if role and role in user.roles:
                roles_to_remove.append(role)
        
        if roles_to_remove:
            await user.remove_roles(*roles_to_remove, reason="Changement de rang")
    
    @app_commands.command(name="rank", description="Définir le rang d'un utilisateur")
    @app_commands.describe(
        user="L'utilisateur dont modifier le rang",
        rank="Le nouveau rang à attribuer"
    )
    @app_commands.choices(rank=[
        app_commands.Choice(name="Membre", value="membre"),
        app_commands.Choice(name="Oracle", value="oracle"),
        app_commands.Choice(name="Gardien", value="gardien"),
        app_commands.Choice(name="Invocateur", value="invocateur")
    ])
    async def rank(self, interaction: discord.Interaction, user: discord.Member, rank: str):
        if not self.has_permission(interaction.user, rank, user):
            await interaction.response.send_message("❌ Vous n'avez pas la permission de définir ce rang.", ephemeral=True)
            return
        
        if rank not in self.ranks:
            await interaction.response.send_message("❌ Rang invalide.", ephemeral=True)
            return
            
        guild = interaction.guild
        target_rank_data = self.ranks[rank]
        target_role = guild.get_role(target_rank_data["role_id"])
        member_role = guild.get_role(self.member_role_id)
        
        if not target_role:
            await interaction.response.send_message("❌ Le rôle spécifié n'existe pas sur ce serveur.", ephemeral=True)
            return
        
        # Check if user already has this rank
        if target_role in user.roles:
            await interaction.response.send_message(f"❌ {user.mention} a déjà le rang **{target_rank_data['name']}**.", ephemeral=True)
            return
        
        try:
            # Remove all current rank roles except Member
            await self.remove_all_rank_roles(user)
            
            # Ensure user has Member role
            if member_role and member_role not in user.roles:
                await user.add_roles(member_role, reason="Attribution du rôle Membre de base")
            
            # Add new rank role (only if not Member, since Member should already be there)
            if rank != "membre":
                await user.add_roles(target_role, reason=f"Rang défini par {interaction.user}")
            
            # Handle special cases for Oracle (add conseil role if available)
            if rank == "oracle" and self.conseil_role_id:
                conseil_role = guild.get_role(self.conseil_role_id)
                if conseil_role:
                    await user.add_roles(conseil_role, reason=f"Promu Oracle par {interaction.user}")
            
            # Remove conseil role if demoting from Oracle
            elif rank != "oracle" and self.conseil_role_id:
                conseil_role = guild.get_role(self.conseil_role_id)
                if conseil_role and conseil_role in user.roles:
                    await user.remove_roles(conseil_role, reason=f"Rétrogradé d'Oracle par {interaction.user}")
            
            # Send DM to user
            try:
                await user.send(f"🔄 Votre rang a été modifié sur **{guild.name}** ! Vous êtes maintenant **{target_rank_data['name']}**.")
            except discord.Forbidden:
                pass
            
            await interaction.response.send_message(f"✅ {user.mention} a été défini au rang **{target_rank_data['name']}**.")
            
        except discord.Forbidden:
            await interaction.response.send_message("❌ Je n'ai pas les permissions nécessaires pour modifier les rôles.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Une erreur s'est produite : {str(e)}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(RankManagement(bot))
