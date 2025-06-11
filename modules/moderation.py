# modules/moderation.py
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import re
import asyncio
import os
import aiosqlite
import random
from typing import Optional, Union
from .rate_limiter import get_rate_limiter

class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.paris_tz = pytz.timezone('Europe/Paris')
        self.db_path = "moderation.db"
        self.rate_limiter = get_rate_limiter()
        
    async def setup_database(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sanctions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    moderator_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    duration INTEGER,
                    expires_at DATETIME,
                    active BOOLEAN DEFAULT 1
                )
            """)
            await db.commit()
    
    async def send_moderation_feedback(self, interaction: discord.Interaction, message: str):
        """Send moderation feedback to COMMANDES_CHANNEL_ID and handle ephemeral messages"""
        commandes_channel_id = os.getenv('COMMANDES_CHANNEL_ID')
        
        if not commandes_channel_id or commandes_channel_id.startswith('commandes_channel_id'):
            # Fallback to normal behavior if channel not configured
            if hasattr(interaction, 'followup') and interaction.response.is_done():
                await interaction.followup.send(message)
            else:
                await interaction.response.send_message(message)
            return
        
        commandes_channel = self.bot.get_channel(int(commandes_channel_id))
        if not commandes_channel:
            # Fallback if channel not found
            if hasattr(interaction, 'followup') and interaction.response.is_done():
                await interaction.followup.send(message)
            else:
                await interaction.response.send_message(message)
            return
        
        # Send feedback to commandes channel
        await commandes_channel.send(message)
        
        # If command was executed outside of commandes channel, send ephemeral message
        if interaction.channel.id != int(commandes_channel_id):
            ephemeral_message = f"✅ Commande exécutée avec succès. Détails dans {commandes_channel.mention}"
            if hasattr(interaction, 'followup') and interaction.response.is_done():
                await interaction.followup.send(ephemeral_message, ephemeral=True)
            else:
                await interaction.response.send_message(ephemeral_message, ephemeral=True)
    
    async def add_sanction(self, user_id, moderator_id, guild_id, sanction_type, reason, duration=None):
        expires_at = None
        if duration:
            expires_at = datetime.now() + timedelta(seconds=duration)
        elif sanction_type == "warn":
            expires_at = datetime.now() + timedelta(days=90)  # 3 months
            
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO sanctions (user_id, moderator_id, guild_id, type, reason, duration, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, moderator_id, guild_id, sanction_type, reason, duration, expires_at))
            await db.commit()
            return cursor.lastrowid
    
    async def get_user_sanctions(self, user_id, guild_id, active_only=True):
        query = "SELECT * FROM sanctions WHERE user_id = ? AND guild_id = ?"
        params = [user_id, guild_id]
        
        if active_only:
            query += " AND active = 1"
            
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                return await cursor.fetchall()
    
    async def remove_sanction(self, sanction_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE sanctions SET active = 0 WHERE id = ?", (sanction_id,))
            await db.commit()
    
    async def get_active_warns(self, user_id, guild_id):
        now = datetime.now()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT COUNT(*) FROM sanctions 
                WHERE user_id = ? AND guild_id = ? AND type = 'warn' AND active = 1 
                AND (expires_at IS NULL OR expires_at > ?)
            """, (user_id, guild_id, now)) as cursor:
                result = await cursor.fetchone()
                return result[0] if result else 0
    
    async def cleanup_expired_sanctions(self):
        now = datetime.now()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE sanctions SET active = 0 
                WHERE expires_at IS NOT NULL AND expires_at <= ? AND active = 1
            """, (now,))
            await db.commit()
    
    @tasks.loop(hours=1)
    async def cleanup_sanctions(self):
        try:
            await self.cleanup_expired_sanctions()
        except Exception as e:
            print(f"Error in cleanup_sanctions: {e}")
    
    @cleanup_sanctions.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()
    
    def parse_duration(self, duration_str):
        """Parse duration string like '1h30m', '2d', '30s' into seconds"""
        duration_str = duration_str.lower().strip()
        
        pattern = r'(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?'
        match = re.match(pattern, duration_str)
        
        if not match:
            return None
            
        days, hours, minutes, seconds = match.groups()
        
        total_seconds = 0
        if days:
            total_seconds += int(days) * 86400
        if hours:
            total_seconds += int(hours) * 3600
        if minutes:
            total_seconds += int(minutes) * 60
        if seconds:
            total_seconds += int(seconds)
            
        return total_seconds if total_seconds > 0 else None
    
    def format_duration(self, seconds):
        """Format seconds into human readable duration in French"""
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        
        parts = []
        if days:
            parts.append(f"{days} jour{'s' if days > 1 else ''}")
        if hours:
            parts.append(f"{hours} heure{'s' if hours > 1 else ''}")
        if minutes:
            parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
        if seconds:
            parts.append(f"{seconds} seconde{'s' if seconds > 1 else ''}")
            
        return " et ".join(parts) if parts else "0 seconde"
    
    def has_permission(self, member, required_roles):
        """Check if member has any of the required roles"""
        member_roles = [role.id for role in member.roles]
        return any(role_id in member_roles for role_id in required_roles if role_id)
    
    def can_punish_target(self, moderator, target):
        """Check if moderator can apply punishment to target based on role hierarchy"""
        # If moderator has admin permissions, they can punish anyone
        if moderator.guild_permissions.administrator:
            return True
        
        # Get role IDs
        conseil_role = os.getenv('CONSEIL_ROLE_ID')
        admin_role = os.getenv('ADMIN_ROLE_ID')
        
        moderator_roles = [role.id for role in moderator.roles]
        target_roles = [role.id for role in target.roles]
        
        # If moderator has admin role (Seigneurs), they can punish anyone
        if admin_role and admin_role != 'your_admin_role_id':
            admin_role_id = int(admin_role)
            if admin_role_id in moderator_roles:
                return True
        
        # If both have CONSEIL_ROLE_ID, moderator cannot punish target
        if conseil_role and not conseil_role.startswith('conseil_role_id'):
            conseil_role_id = int(conseil_role)
            if conseil_role_id in moderator_roles and conseil_role_id in target_roles:
                return False
        
        return True
    
    def extract_message_id_from_link(self, message_link):
        """Extract message ID from Discord message link"""
        # Discord message link format: https://discord.com/channels/guild_id/channel_id/message_id
        pattern = r'https://discord\.com/channels/\d+/\d+/(\d+)'
        match = re.search(pattern, message_link)
        return int(match.group(1)) if match else None
    
    async def get_user_safe(self, user_input: Union[discord.Member, discord.User, int, str]):
        """Safely get user object from various input types"""
        if isinstance(user_input, (discord.Member, discord.User)):
            return user_input
        
        try:
            user_id = int(user_input)
            return await self.bot.fetch_user(user_id)
        except (ValueError, discord.NotFound):
            return None
    
    async def send_dm_notification(self, user, action, reason=None, duration=None, end_time=None, is_lifted=False, warn_count=None):
        """Send DM notification to user in French"""
        try:
            # Check if user is on the server
            guild = self.bot.get_guild(int(os.getenv('GUILD_ID')))
            member = guild.get_member(user.id) if guild else None
            
            # For banned users or users not on server, try to fetch user object
            if not member and hasattr(user, 'id'):
                try:
                    user = await self.bot.fetch_user(user.id)
                except discord.NotFound:
                    return  # User doesn't exist
            
            if is_lifted:
                action_messages = {
                    "unmute": "🔊 Votre mise en sourdine sur le serveur Les Élémentalistes a été levée par un modérateur. Vous pouvez désormais participer aux conversations de nouveau.",
                    "untimeout": "🔊 Votre timeout sur le serveur Les Élémentalistes a été levé par un modérateur. Vous pouvez désormais participer aux conversations de nouveau.",
                    "unban": "🎉 Vous avez été débanni du serveur Les Élémentalistes par un administrateur. Vous êtes maintenant libre de rejoindre le serveur de nouveau."
                }
                
                citations = [
                    "*« Chaque fin est un nouveau commencement. »*",
                    "*« Les erreurs d'hier sont les leçons d'aujourd'hui. »*",
                    "*« Le pardon est la clé de la liberté. »*",
                    "*« Une seconde chance est un cadeau précieux. »*",
                    "*« La rédemption est toujours possible. »*"
                ]
                
                message = action_messages.get(action, f"Votre sanction sur le serveur Les Élémentalistes a été levée.")
                message += f"\n\n{random.choice(citations)}"
                
            else:
                action_messages = {
                    "warn": f"⚠️ Vous avez reçu un avertissement sur le serveur Les Élémentalistes pour la raison suivante : **{reason}**. Les avertissements expirent automatiquement après 3 mois, mais sachez que 3 avertissements actifs résultent en un bannissement automatique.",
                    "mute": f"🔇 Vous avez été mis en timeout sur le serveur Les Élémentalistes pour la raison suivante : **{reason}**. Pendant cette période, vous ne pourrez pas envoyer de messages dans les canaux du serveur.",
                    "timeout": f"🔇 Vous avez été mis en timeout sur le serveur Les Élémentalistes pour la raison suivante : **{reason}**. Pendant cette période, vous ne pourrez pas envoyer de messages dans les canaux du serveur.",
                    "ban": f"🔨 Vous avez été banni définitivement du serveur Les Élémentalistes pour la raison suivante : **{reason}**. Cette décision a été prise suite à un comportement inapproprié récurrent ou grave.\n\n📢 Si vous pensez que cette sanction est injuste, vous pouvez rejoindre notre serveur de réclamation : https://discord.gg/VxHWtNTFTu",
                    "kick": f"👋 Vous avez été expulsé du serveur Les Élémentalistes pour la raison suivante : **{reason}**. Vous pouvez rejoindre le serveur immédiatement si vous le souhaitez."
                }
                
                # Snarky citations based on punishment severity
                warn_citations = [
                    "*« Félicitations, vous venez de (re)découvrir que les règles ne sont pas optionnelles. »*",
                    "*« Apparemment, lire le règlement était trop compliqué. »*",
                    "*« Voilà ce qui arrive quand on teste les limites... spoiler : elles existent. »*",
                    "*« Peut-être qu'un petit rappel vous aidera à mieux vous comporter. »*",
                    "*« Les avertissements, c'est comme les Pokemon : attrapez-les tous ! (Mais pas vraiment.) »*",
                    "*« Première leçon gratuite : respecter les règles. »*"
                ]
                
                timeout_citations = [
                    "*« Le silence est d'or, et vous venez de gagner le jackpot. »*",
                    "*« Parfois, il vaut mieux se taire... voilà votre chance de l'apprendre. »*",
                    "*« On vous offre une pause forcée pour réfléchir à vos choix de vie. »*",
                    "*« Considérez ceci comme un stage de méditation obligatoire. »*",
                    "*« Votre droit de parole a temporairement expiré. »*",
                    "*« Temps de réflexion accordé gracieusement par la modération. »*",
                    "*« Une petite pause s'impose, visiblement. »*"
                ]
                
                kick_citations = [
                    "*« Au revoir ! Fermez-bien la porte derrière vous, s'il vous plaît. »*",
                    "*« Vous êtes libre de revenir... après avoir appris les bonnes manières. »*",
                    "*« Expulsé ! Comme au football, mais sans le carton rouge. »*",
                    "*« Prenez l'air, ça vous fera du bien. Au serveur aussi. »*",
                    "*« Désolé, mais votre comportement n'est pas compatible avec notre serveur. »*",
                    "*« Direction la sortie ! Revenez quand vous serez plus sage. »*",
                    "*« Sortie express accordée ! Profitez-en pour réfléchir. »*"
                ]
                
                ban_citations = [
                    "*« Félicitations ! Vous venez de remporter un bannissement permanent. Quel talent ! »*",
                    "*« Votre comportement était si remarquable qu'on a décidé de vous offrir une sortie définitive. »*",
                    "*« Bannissement permanent : parce que certaines personnes ne méritent pas de troisième chance. »*",
                    "*« Au revoir et... eh bien, juste au revoir en fait. »*",
                    "*« Vous avez réussi l'exploit de vous faire bannir définitivement. Bravo ! »*",
                    "*« Succès déverrouillé : bannissement permanent ! Quelle prouesse ! »*",
                    "*« Votre comportement était tellement exceptionnel qu'on vous accorde un bannissement d'honneur. »*"
                ]
                
                citations_map = {
                    "warn": warn_citations,
                    "mute": timeout_citations,
                    "timeout": timeout_citations,
                    "kick": kick_citations,
                    "ban": ban_citations
                }
                
                citations = citations_map.get(action, warn_citations)
                message = action_messages.get(action, f"Action de modération sur le serveur Les Élémentalistes pour la raison suivante : **{reason}**.")
                
                if action == "warn" and warn_count is not None:
                    message += f" Vous avez maintenant **{warn_count}/3 avertissements actifs**."
                
                if duration and end_time:
                    duration_str = self.format_duration(duration)
                    end_time_paris = end_time.astimezone(self.paris_tz)
                    message += f" Cette sanction durera {duration_str} et prendra fin le {end_time_paris.strftime('%d/%m/%Y à %H:%M')} (heure de Paris)."
                
                message += f"\n\n{random.choice(citations)}"
            
            await user.send(message)
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            pass  # User has DMs disabled or doesn't exist

    # Slash commands
    @discord.app_commands.command(name="warn", description="Avertir un utilisateur")
    @discord.app_commands.describe(
        user="L'utilisateur à avertir",
        reason="Raison de l'avertissement"
    )
    async def warn_slash(self, interaction: discord.Interaction, user: discord.Member, reason: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        
        required_roles = []
        if admin_role and admin_role != 'your_admin_role_id':
            required_roles.append(int(admin_role))
        if moderator_role and moderator_role != 'your_moderator_role_id':
            required_roles.append(int(moderator_role))
        
        if not self.has_permission(interaction.user, required_roles):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if not self.can_punish_target(interaction.user, user):
            await interaction.response.send_message("❌ Vous ne pouvez pas appliquer de sanction à ce membre.", ephemeral=True)
            return
        
        if user.bot:
            await interaction.response.send_message("❌ Impossible d'avertir un bot.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Add to database first to get the new count
        sanction_id = await self.add_sanction(user.id, interaction.user.id, interaction.guild.id, "warn", reason)
        
        # Check warn count after adding the new warning
        warn_count = await self.get_active_warns(user.id, interaction.guild.id)
        
        # Send DM with warning count
        await self.send_dm_notification(user, "warn", reason, warn_count=warn_count)
        
        if warn_count >= 3:
            # Auto ban
            try:
                await self.rate_limiter.safe_ban(interaction.guild, user, reason=f"3 avertissements atteints - Dernier avertissement: {reason}")
                await self.add_sanction(user.id, self.bot.user.id, interaction.guild.id, "ban", "3 avertissements atteints")
                await self.send_moderation_feedback(interaction, f"⚠️ {user.mention} a été averti pour **{reason}** (ID: {sanction_id}) et **banni automatiquement** pour avoir atteint 3 avertissements.")
            except discord.Forbidden:
                await self.send_moderation_feedback(interaction, f"⚠️ {user.mention} a été averti pour **{reason}** (ID: {sanction_id}) mais je n'ai pas pu le bannir automatiquement.")
        else:
            await self.send_moderation_feedback(interaction, f"⚠️ {user.mention} a été averti pour **{reason}** (ID: {sanction_id}). **{warn_count}/3 avertissements actifs**.")
    
    @discord.app_commands.command(name="mute", description="Mettre en timeout un utilisateur")
    @discord.app_commands.describe(
        user="L'utilisateur à mettre en timeout",
        duration="Durée (ex: 1h30m, 2d, 30s)",
        reason="Raison du timeout"
    )
    async def mute_slash(self, interaction: discord.Interaction, user: discord.Member, duration: str, reason: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        oracle_role = os.getenv('ORACLE_ROLE_ID')
        
        required_roles = []
        for role in [admin_role, moderator_role, oracle_role]:
            if role and not role.startswith('your_'):
                required_roles.append(int(role))
        
        if not self.has_permission(interaction.user, required_roles):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if not self.can_punish_target(interaction.user, user):
            await interaction.response.send_message("❌ Vous ne pouvez pas appliquer de sanction à ce membre.", ephemeral=True)
            return
        
        duration_seconds = self.parse_duration(duration)
        if not duration_seconds:
            await interaction.response.send_message("❌ Format de durée invalide. Exemples: 1h30m, 2d, 30s", ephemeral=True)
            return
        
        if user.bot:
            await interaction.response.send_message("❌ Impossible de mettre en timeout un bot.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        end_time = datetime.now() + timedelta(seconds=duration_seconds)
        
        # Send DM before applying punishment
        await self.send_dm_notification(user, "mute", reason, duration_seconds, end_time)
        
        # Apply Discord timeout using rate limiter
        try:
            timeout_until = discord.utils.utcnow() + timedelta(seconds=duration_seconds)
            await self.rate_limiter.safe_member_edit(user, timed_out_until=timeout_until, reason=reason)
            sanction_id = await self.add_sanction(user.id, interaction.user.id, interaction.guild.id, "mute", reason, duration_seconds)
            await self.send_moderation_feedback(interaction, f"🔇 {user.mention} a été mis en timeout pour {self.format_duration(duration_seconds)} pour **{reason}** (ID: {sanction_id}).")
        except discord.Forbidden:
            await self.send_moderation_feedback(interaction, "❌ Je n'ai pas la permission de mettre cet utilisateur en timeout.")
    
    @discord.app_commands.command(name="timeout", description="Mettre en timeout un utilisateur")
    @discord.app_commands.describe(
        user="L'utilisateur à mettre en timeout",
        duration="Durée (ex: 1h30m, 2d, 30s)",
        reason="Raison du timeout"
    )
    async def timeout_slash(self, interaction: discord.Interaction, user: discord.Member, duration: str, reason: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        oracle_role = os.getenv('ORACLE_ROLE_ID')
        
        required_roles = []
        for role in [admin_role, moderator_role, oracle_role]:
            if role and not role.startswith('your_'):
                required_roles.append(int(role))
        
        if not self.has_permission(interaction.user, required_roles):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if not self.can_punish_target(interaction.user, user):
            await interaction.response.send_message("❌ Vous ne pouvez pas appliquer de sanction à ce membre.", ephemeral=True)
            return
        
        duration_seconds = self.parse_duration(duration)
        if not duration_seconds:
            await interaction.response.send_message("❌ Format de durée invalide. Exemples: 1h30m, 2d, 30s", ephemeral=True)
            return
        
        if user.bot:
            await interaction.response.send_message("❌ Impossible de mettre en timeout un bot.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        end_time = datetime.now() + timedelta(seconds=duration_seconds)
        
        # Send DM before applying punishment
        await self.send_dm_notification(user, "timeout", reason, duration_seconds, end_time)
        
        # Apply Discord timeout using rate limiter
        try:
            timeout_until = discord.utils.utcnow() + timedelta(seconds=duration_seconds)
            await self.rate_limiter.safe_member_edit(user, timed_out_until=timeout_until, reason=reason)
            sanction_id = await self.add_sanction(user.id, interaction.user.id, interaction.guild.id, "timeout", reason, duration_seconds)
            await self.send_moderation_feedback(interaction, f"🔇 {user.mention} a été mis en timeout pour {self.format_duration(duration_seconds)} pour **{reason}** (ID: {sanction_id}).")
        except discord.Forbidden:
            await self.send_moderation_feedback(interaction, "❌ Je n'ai pas la permission de mettre cet utilisateur en timeout.")
    
    @discord.app_commands.command(name="ban", description="Bannir définitivement un utilisateur")
    @discord.app_commands.describe(
        user="L'utilisateur à bannir",
        reason="Raison du bannissement"
    )
    async def ban_slash(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User], reason: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        
        required_roles = []
        if admin_role and admin_role != 'your_admin_role_id':
            required_roles.append(int(admin_role))
        if moderator_role and moderator_role != 'your_moderator_role_id':
            required_roles.append(int(moderator_role))
        
        if not self.has_permission(interaction.user, required_roles):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Only check punishment hierarchy if user is a member (not just User object)
        if isinstance(user, discord.Member) and not self.can_punish_target(interaction.user, user):
            await interaction.response.send_message("❌ Vous ne pouvez pas appliquer de sanction à ce membre.", ephemeral=True)
            return
        
        if user.bot:
            await interaction.response.send_message("❌ Impossible de bannir un bot.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Send DM before applying punishment
        await self.send_dm_notification(user, "ban", reason)
        
        try:
            await self.rate_limiter.safe_ban(interaction.guild, user, reason=reason)
            sanction_id = await self.add_sanction(user.id, interaction.user.id, interaction.guild.id, "ban", reason)
            await self.send_moderation_feedback(interaction, f"🔨 {user.mention} a été banni définitivement pour **{reason}** (ID: {sanction_id}).")
        except discord.Forbidden:
            await self.send_moderation_feedback(interaction, "❌ Je n'ai pas la permission de bannir cet utilisateur.")
        except discord.NotFound:
            await self.send_moderation_feedback(interaction, "❌ Utilisateur non trouvé.")
    
    @discord.app_commands.command(name="kick", description="Expulser un utilisateur")
    @discord.app_commands.describe(
        user="L'utilisateur à expulser",
        reason="Raison de l'expulsion"
    )
    async def kick_slash(self, interaction: discord.Interaction, user: discord.Member, reason: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        oracle_role = os.getenv('ORACLE_ROLE_ID')
        animator_role = os.getenv('ANIMATOR_ROLE_ID')
        mineur_role = os.getenv('MINEUR_ROLE_ID')
        
        # Check if user has mineur role
        user_roles = [role.id for role in user.roles]
        has_mineur_role = mineur_role and mineur_role != 'your_mineur_role_id' and int(mineur_role) in user_roles
        
        # Build required roles list
        required_roles = []
        if admin_role and admin_role != 'your_admin_role_id':
            required_roles.append(int(admin_role))
        if moderator_role and moderator_role != 'your_moderator_role_id':
            required_roles.append(int(moderator_role))
        
        # Allow Oracle and Animator roles only if target has MINEUR_ROLE_ID
        if has_mineur_role:
            if oracle_role and oracle_role != 'your_oracle_role_id':
                required_roles.append(int(oracle_role))
            if animator_role and animator_role != 'your_animator_role_id':
                required_roles.append(int(animator_role))
        
        if not self.has_permission(interaction.user, required_roles):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if not self.can_punish_target(interaction.user, user):
            await interaction.response.send_message("❌ Vous ne pouvez pas appliquer de sanction à ce membre.", ephemeral=True)
            return
        
        if user.bot:
            await interaction.response.send_message("❌ Impossible d'expulser un bot.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Send DM before applying punishment
        await self.send_dm_notification(user, "kick", reason)
        
        try:
            await self.rate_limiter.safe_kick(user, reason=reason)
            sanction_id = await self.add_sanction(user.id, interaction.user.id, interaction.guild.id, "kick", reason)
            await self.send_moderation_feedback(interaction, f"👋 {user.mention} a été expulsé pour **{reason}** (ID: {sanction_id}).")
        except discord.Forbidden:
            await self.send_moderation_feedback(interaction, "❌ Je n'ai pas la permission d'expulser cet utilisateur.")
    
    @discord.app_commands.command(name="unmute", description="Lever le timeout d'un utilisateur")
    @discord.app_commands.describe(
        user="L'utilisateur dont lever le timeout"
    )
    async def unmute_slash(self, interaction: discord.Interaction, user: discord.Member):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        oracle_role = os.getenv('ORACLE_ROLE_ID')
        
        required_roles = []
        for role in [admin_role, moderator_role, oracle_role]:
            if role and not role.startswith('your_'):
                required_roles.append(int(role))
        
        if not self.has_permission(interaction.user, required_roles):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        try:
            await self.rate_limiter.safe_member_edit(user, timed_out_until=None, reason=f"Timeout levé par {interaction.user}")
            await self.send_dm_notification(user, "unmute", is_lifted=True)
            await self.send_moderation_feedback(interaction, f"🔊 {user.mention} n'est plus en timeout.")
        except discord.Forbidden:
            await self.send_moderation_feedback(interaction, "❌ Je n'ai pas la permission de lever le timeout de cet utilisateur.")
    
    @discord.app_commands.command(name="untimeout", description="Lever le timeout d'un utilisateur")
    @discord.app_commands.describe(
        user="L'utilisateur dont lever le timeout"
    )
    async def untimeout_slash(self, interaction: discord.Interaction, user: discord.Member):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        oracle_role = os.getenv('ORACLE_ROLE_ID')
        
        required_roles = []
        for role in [admin_role, moderator_role, oracle_role]:
            if role and not role.startswith('your_'):
                required_roles.append(int(role))
        
        if not self.has_permission(interaction.user, required_roles):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        try:
            await self.rate_limiter.safe_member_edit(user, timed_out_until=None, reason=f"Timeout levé par {interaction.user}")
            await self.send_dm_notification(user, "untimeout", is_lifted=True)
            await self.send_moderation_feedback(interaction, f"🔊 {user.mention} n'est plus en timeout.")
        except discord.Forbidden:
            await self.send_moderation_feedback(interaction, "❌ Je n'ai pas la permission de lever le timeout de cet utilisateur.")
    
    @discord.app_commands.command(name="unban", description="Débannir un utilisateur")
    @discord.app_commands.describe(
        user_id="L'ID de l'utilisateur à débannir"
    )
    async def unban_slash(self, interaction: discord.Interaction, user_id: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        
        if not admin_role or admin_role == 'your_admin_role_id':
            await interaction.response.send_message("❌ Rôle admin non configuré.", ephemeral=True)
            return
        
        if not self.has_permission(interaction.user, [int(admin_role)]):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        try:
            user_id_int = int(user_id)
            user = await self.bot.fetch_user(user_id_int)
            await self.rate_limiter.safe_unban(interaction.guild, user)
            await self.send_dm_notification(user, "unban", is_lifted=True)
            await self.send_moderation_feedback(interaction, f"✅ {user.mention} a été débanni.")
        except ValueError:
            await self.send_moderation_feedback(interaction, "❌ ID utilisateur invalide.")
        except discord.NotFound:
            await self.send_moderation_feedback(interaction, "❌ Utilisateur non trouvé ou non banni.")
        except discord.Forbidden:
            await self.send_moderation_feedback(interaction, "❌ Je n'ai pas la permission de débannir cet utilisateur.")

    @discord.app_commands.command(name="clear_conversation", description="Supprimer les messages entre deux messages spécifiés")
    @discord.app_commands.describe(
        debut="Lien ou ID du premier message",
        fin="Lien ou ID du dernier message"
    )
    async def clear_conversation_slash(self, interaction: discord.Interaction, debut: str, fin: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        
        required_roles = []
        if admin_role and admin_role != 'your_admin_role_id':
            required_roles.append(int(admin_role))
        if moderator_role and moderator_role != 'your_moderator_role_id':
            required_roles.append(int(moderator_role))
        
        if not self.has_permission(interaction.user, required_roles):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Extract message IDs
        start_id = self.extract_message_id_from_link(debut)
        if not start_id:
            try:
                start_id = int(debut)
            except ValueError:
                await self.send_moderation_feedback(interaction, "❌ Format du message de début invalide.")
                return
        
        end_id = self.extract_message_id_from_link(fin)
        if not end_id:
            try:
                end_id = int(fin)
            except ValueError:
                await self.send_moderation_feedback(interaction, "❌ Format du message de fin invalide.")
                return
        
        # Ensure start_id is smaller than end_id
        if start_id > end_id:
            start_id, end_id = end_id, start_id
        
        try:
            # Get the start and end messages to verify they exist
            start_message = await interaction.channel.fetch_message(start_id)
            end_message = await interaction.channel.fetch_message(end_id)
            
            # Check if messages are older than 14 days
            fourteen_days_ago = discord.utils.utcnow() - timedelta(days=14)
            if start_message.created_at < fourteen_days_ago or end_message.created_at < fourteen_days_ago:
                await self.send_moderation_feedback(interaction, "❌ Impossible de supprimer des messages plus anciens que 14 jours.")
                return
            
            # Collect messages to delete
            messages_to_delete = []
            
            # Use history to get messages between the two IDs
            async for message in interaction.channel.history(limit=None, after=start_message.created_at - timedelta(seconds=1), before=end_message.created_at + timedelta(seconds=1)):
                if start_id <= message.id <= end_id:
                    messages_to_delete.append(message)
            
            if len(messages_to_delete) > 100:
                await self.send_moderation_feedback(interaction, "❌ Impossible de supprimer plus de 100 messages à la fois.")
                return
            
            if not messages_to_delete:
                await self.send_moderation_feedback(interaction, "❌ Aucun message trouvé dans cette plage.")
                return
            
            # Delete messages using rate limiter
            deleted_count = 0
            for message in messages_to_delete:
                try:
                    await self.rate_limiter.safe_delete(message)
                    deleted_count += 1
                except discord.NotFound:
                    pass  # Message already deleted
                except discord.Forbidden:
                    await self.send_moderation_feedback(interaction, f"❌ Permission insuffisante pour supprimer certains messages. {deleted_count} messages supprimés.")
                    return
            
            await self.send_moderation_feedback(interaction, f"✅ {deleted_count} messages supprimés avec succès.")
            
        except discord.NotFound:
            await self.send_moderation_feedback(interaction, "❌ Un ou plusieurs messages n'ont pas été trouvés.")
        except discord.Forbidden:
            await self.send_moderation_feedback(interaction, "❌ Permission insuffisante pour accéder aux messages.")

    @discord.app_commands.command(name="mass_clear", description="Supprimer un nombre spécifié de messages récents")
    @discord.app_commands.describe(
        quantite="Nombre de messages à supprimer (max 100)"
    )
    async def mass_clear_slash(self, interaction: discord.Interaction, quantite: int):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        
        required_roles = []
        if admin_role and admin_role != 'your_admin_role_id':
            required_roles.append(int(admin_role))
        if moderator_role and moderator_role != 'your_moderator_role_id':
            required_roles.append(int(moderator_role))
        
        if not self.has_permission(interaction.user, required_roles):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if quantite <= 0:
            await interaction.response.send_message("❌ La quantité doit être un nombre positif.", ephemeral=True)
            return
        
        if quantite > 100:
            await interaction.response.send_message("❌ Impossible de supprimer plus de 100 messages à la fois.", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Check for 14-day limit
        fourteen_days_ago = discord.utils.utcnow() - timedelta(days=14)
        
        try:
            # Get messages to delete
            messages_to_delete = []
            async for message in interaction.channel.history(limit=quantite):
                if message.created_at < fourteen_days_ago:
                    break  # Stop if we hit the 14-day limit
                messages_to_delete.append(message)
            
            if not messages_to_delete:
                await self.send_moderation_feedback(interaction, "❌ Aucun message récent à supprimer (limite de 14 jours).")
                return
            
            # Delete messages using rate limiter
            deleted_count = 0
            for message in messages_to_delete:
                try:
                    await self.rate_limiter.safe_delete(message)
                    deleted_count += 1
                except discord.NotFound:
                    pass  # Message already deleted
                except discord.Forbidden:
                    await self.send_moderation_feedback(interaction, f"❌ Permission insuffisante pour supprimer certains messages. {deleted_count} messages supprimés.")
                    return
            
            await self.send_moderation_feedback(interaction, f"✅ {deleted_count} messages supprimés avec succès.")
            
        except discord.Forbidden:
            await self.send_moderation_feedback(interaction, "❌ Permission insuffisante pour supprimer les messages.")

    @discord.app_commands.command(name="sanctions", description="Afficher les sanctions d'un utilisateur")
    @discord.app_commands.describe(
        user="L'utilisateur dont afficher les sanctions"
    )
    async def sanctions_slash(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User]):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        oracle_role = os.getenv('ORACLE_ROLE_ID')
        
        required_roles = []
        for role in [admin_role, moderator_role, oracle_role]:
            if role and not role.startswith('your_'):
                required_roles.append(int(role))
        
        if not self.has_permission(interaction.user, required_roles):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        sanctions = await self.get_user_sanctions(user.id, interaction.guild.id, active_only=False)
        view = SanctionsView(sanctions, user)
        await interaction.response.send_message(embed=view.get_embed(), view=view)
    
    @discord.app_commands.command(name="remove_sanction", description="Supprimer une sanction par son ID")
    @discord.app_commands.describe(
        user="L'utilisateur concerné",
        sanction_id="L'ID de la sanction à supprimer"
    )
    async def remove_sanction_slash(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User], sanction_id: int):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        
        if not admin_role or admin_role == 'your_admin_role_id':
            await interaction.response.send_message("❌ Rôle admin non configuré.", ephemeral=True)
            return
        
        if not self.has_permission(interaction.user, [int(admin_role)]):
            await interaction.response.send_message("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Verify sanction exists and belongs to the user
        sanctions = await self.get_user_sanctions(user.id, interaction.guild.id, active_only=False)
        sanction_found = any(sanction[0] == sanction_id for sanction in sanctions)
        
        if not sanction_found:
            await interaction.response.send_message(f"❌ Aucune sanction trouvée avec l'ID {sanction_id} pour {user.mention}.", ephemeral=True)
            return
        
        await self.remove_sanction(sanction_id)
        await self.send_moderation_feedback(interaction, f"✅ Sanction ID {sanction_id} supprimée pour {user.mention}.")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.setup_database()
        # Start cleanup task after database is ready
        if not self.cleanup_sanctions.is_running():
            self.cleanup_sanctions.start()

class SanctionsView(discord.ui.View):
    def __init__(self, sanctions, user, per_page=5):
        super().__init__(timeout=300)
        self.sanctions = sanctions
        self.user = user
        self.per_page = per_page
        self.current_page = 0
        self.max_pages = (len(sanctions) - 1) // per_page + 1 if sanctions else 1
        
        if self.max_pages <= 1:
            self.remove_item(self.previous_button)
            self.remove_item(self.next_button)
    
    def get_embed(self):
        embed = discord.Embed(
            title=f"📋 Sanctions de {self.user.display_name}",
            color=0xff6b6b,
            timestamp=datetime.now()
        )
        
        if not self.sanctions:
            embed.description = "Aucune sanction trouvée."
            return embed
        
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_sanctions = self.sanctions[start:end]
        
        paris_tz = pytz.timezone('Europe/Paris')
        
        for sanction in page_sanctions:
            sanction_id, user_id, mod_id, guild_id, sanction_type, reason, timestamp, duration, expires_at, active = sanction
            
            status = "🟢 Active" if active else "🔴 Inactive"
            
            try:
                timestamp_dt = datetime.fromisoformat(timestamp)
                timestamp_paris = timestamp_dt.replace(tzinfo=pytz.UTC).astimezone(paris_tz)
                timestamp_str = timestamp_paris.strftime('%d/%m/%Y à %H:%M')
            except:
                timestamp_str = timestamp
            
            value = f"**Type:** {sanction_type.title()}\n"
            value += f"**Raison:** {reason}\n"
            value += f"**Date:** {timestamp_str}\n"
            value += f"**Statut:** {status}"
            
            if expires_at:
                try:
                    expires_dt = datetime.fromisoformat(expires_at)
                    expires_paris = expires_dt.replace(tzinfo=pytz.UTC).astimezone(paris_tz)
                    value += f"\n**Expire:** {expires_paris.strftime('%d/%m/%Y à %H:%M')}"
                except:
                    value += f"\n**Expire:** {expires_at}"
            
            embed.add_field(
                name=f"ID: {sanction_id}",
                value=value,
                inline=False
            )
        
        embed.set_footer(text=f"Page {self.current_page + 1}/{self.max_pages}")
        return embed
    
    @discord.ui.button(label="◀️", style=discord.ButtonStyle.gray)
    async def previous_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()
    
    @discord.ui.button(label="▶️", style=discord.ButtonStyle.gray)
    async def next_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.current_page < self.max_pages - 1:
            self.current_page += 1
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()

async def setup(bot):
    await bot.add_cog(ModerationCog(bot))
