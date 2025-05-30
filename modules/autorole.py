# modules/autorole.py
import discord
from discord.ext import commands
import os
import logging
from .rate_limiter import get_rate_limiter

# Get the rate limiter instance
rate_limiter = get_rate_limiter()

class AutoRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.member_role_id = None
        self.separator_role_id = None
        
        # Chargement sécurisé des IDs de rôles
        try:
            if os.getenv('MEMBER_ROLE_ID'):
                self.member_role_id = int(os.getenv('MEMBER_ROLE_ID'))
            if os.getenv('SEPARATOR_ROLE_ID'):
                self.separator_role_id = int(os.getenv('SEPARATOR_ROLE_ID'))
        except ValueError as e:
            logging.error(f"Erreur lors du chargement des IDs de rôles: {e}")
        
    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Attribue automatiquement les rôles lors de l'arrivée d'un membre"""
        try:
            guild = member.guild
            roles_to_add = []
            
            # Vérification et ajout du rôle membre
            if self.member_role_id:
                member_role = guild.get_role(self.member_role_id)
                if member_role:
                    roles_to_add.append(member_role)
                else:
                    logging.warning(f"Rôle membre introuvable (ID: {self.member_role_id})")
            
            # Vérification et ajout du rôle séparateur
            if self.separator_role_id:
                separator_role = guild.get_role(self.separator_role_id)
                if separator_role:
                    roles_to_add.append(separator_role)
                else:
                    logging.warning(f"Rôle séparateur introuvable (ID: {self.separator_role_id})")
            
            # Attribution des rôles avec rate limiting
            if roles_to_add:
                await rate_limiter.safe_member_edit(
                    member, 
                    roles=member.roles + roles_to_add,
                    reason="Attribution automatique des rôles"
                )
                role_names = [role.name for role in roles_to_add]
                logging.info(f"Rôles attribués à {member.display_name} ({member.id}): {', '.join(role_names)}")
            else:
                logging.warning(f"Aucun rôle à attribuer pour {member.display_name}")
            
        except discord.Forbidden:
            logging.error(f"Permission insuffisante pour attribuer les rôles à {member.display_name}")
        except discord.HTTPException as e:
            logging.error(f"Erreur HTTP lors de l'attribution des rôles à {member.display_name}: {e}")
        except Exception as e:
            logging.error(f"Erreur inattendue lors de l'attribution des rôles à {member.display_name}: {e}")

async def setup(bot):
    await bot.add_cog(AutoRole(bot))
