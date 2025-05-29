# modules/moderation.py
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import re
import asyncio
import os
import aiosqlite

class ModerationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.paris_tz = pytz.timezone('Europe/Paris')
        self.db_path = "moderation.db"
        self.cleanup_sanctions.start()
        
    async def cog_load(self):
        await self.init_db()
    
    def cog_unload(self):
        self.cleanup_sanctions.cancel()
    
    async def init_db(self):
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
        await self.cleanup_expired_sanctions()
    
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
    
    async def send_dm_notification(self, user, action, reason, duration=None, end_time=None):
        """Send DM notification to user in French"""
        try:
            action_messages = {
                "warn": f"⚠️ Vous avez reçu un avertissement sur le serveur Les Élémentalistes.\n**Raison :** {reason}\n\n*Les avertissements expirent automatiquement après 3 mois. 3 avertissements actifs résultent en un bannissement automatique.*",
                "mute": f"🔇 Vous avez été mis en sourdine sur le serveur Les Élémentalistes.\n**Raison :** {reason}",
                "tempban": f"🔨 Vous avez été banni temporairement du serveur Les Élémentalistes.\n**Raison :** {reason}",
                "ban": f"🔨 Vous avez été banni définitivement du serveur Les Élémentalistes.\n**Raison :** {reason}",
                "kick": f"👋 Vous avez été expulsé du serveur Les Élémentalistes.\n**Raison :** {reason}"
            }
            
            message = action_messages.get(action, f"Action de modération : {action}\n**Raison :** {reason}")
            
            if duration and end_time:
                duration_str = self.format_duration(duration)
                end_time_paris = end_time.astimezone(self.paris_tz)
                message += f"\n**Durée :** {duration_str}\n**Fin :** {end_time_paris.strftime('%d/%m/%Y à %H:%M')} (heure de Paris)"
            
            await user.send(message)
        except discord.Forbidden:
            pass  # User has DMs disabled
    
    @commands.slash_command(name="warn", description="Avertir un utilisateur")
    async def warn(self, ctx, user: discord.Member, *, reason: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        
        required_roles = []
        if admin_role and admin_role != 'your_admin_role_id':
            required_roles.append(int(admin_role))
        if moderator_role and moderator_role != 'your_moderator_role_id':
            required_roles.append(int(moderator_role))
        
        if not self.has_permission(ctx.author, required_roles):
            await ctx.respond("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if user.bot:
            await ctx.respond("❌ Impossible d'avertir un bot.", ephemeral=True)
            return
        
        # Send DM before applying punishment
        await self.send_dm_notification(user, "warn", reason)
        
        # Add to database
        sanction_id = await self.add_sanction(user.id, ctx.author.id, ctx.guild.id, "warn", reason)
        
        # Check warn count
        warn_count = await self.get_active_warns(user.id, ctx.guild.id)
        
        if warn_count >= 3:
            # Auto ban
            try:
                await user.ban(reason=f"3 avertissements atteints - Dernier avertissement: {reason}")
                await self.add_sanction(user.id, self.bot.user.id, ctx.guild.id, "ban", "3 avertissements atteints")
                await ctx.respond(f"⚠️ {user.mention} a été averti (ID: {sanction_id}) et **banni automatiquement** pour avoir atteint 3 avertissements.")
            except discord.Forbidden:
                await ctx.respond(f"⚠️ {user.mention} a été averti (ID: {sanction_id}) mais je n'ai pas pu le bannir automatiquement.")
        else:
            await ctx.respond(f"⚠️ {user.mention} a été averti (ID: {sanction_id}). **{warn_count}/3 avertissements actifs**.")
    
    @commands.slash_command(name="mute", description="Mettre en sourdine un utilisateur")
    async def mute(self, ctx, user: discord.Member, duration: str, *, reason: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        oracle_role = os.getenv('ORACLE_ROLE_ID')
        animator_role = os.getenv('ANIMATOR_ROLE_ID')
        
        required_roles = []
        for role in [admin_role, moderator_role, oracle_role, animator_role]:
            if role and not role.startswith('your_'):
                required_roles.append(int(role))
        
        if not self.has_permission(ctx.author, required_roles):
            await ctx.respond("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        duration_seconds = self.parse_duration(duration)
        if not duration_seconds:
            await ctx.respond("❌ Format de durée invalide. Exemples: 1h30m, 2d, 30s", ephemeral=True)
            return
        
        if user.bot:
            await ctx.respond("❌ Impossible de mettre en sourdine un bot.", ephemeral=True)
            return
        
        end_time = datetime.now() + timedelta(seconds=duration_seconds)
        
        # Send DM before applying punishment
        await self.send_dm_notification(user, "mute", reason, duration_seconds, end_time)
        
        # Apply Discord timeout
        try:
            await user.timeout(until=discord.utils.utcnow() + timedelta(seconds=duration_seconds), reason=reason)
            sanction_id = await self.add_sanction(user.id, ctx.author.id, ctx.guild.id, "mute", reason, duration_seconds)
            await ctx.respond(f"🔇 {user.mention} a été mis en sourdine pour {self.format_duration(duration_seconds)} (ID: {sanction_id}).")
        except discord.Forbidden:
            await ctx.respond("❌ Je n'ai pas la permission de mettre cet utilisateur en sourdine.")
    
    @commands.slash_command(name="timeout", description="Mettre en timeout un utilisateur")
    async def timeout(self, ctx, user: discord.Member, duration: str, *, reason: str):
        await self.mute(ctx, user, duration, reason=reason)
    
    @commands.slash_command(name="tempban", description="Bannir temporairement un utilisateur")
    async def tempban(self, ctx, user: discord.Member, duration: str, *, reason: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        
        required_roles = []
        if admin_role and admin_role != 'your_admin_role_id':
            required_roles.append(int(admin_role))
        if moderator_role and moderator_role != 'your_moderator_role_id':
            required_roles.append(int(moderator_role))
        
        if not self.has_permission(ctx.author, required_roles):
            await ctx.respond("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        duration_seconds = self.parse_duration(duration)
        if not duration_seconds:
            await ctx.respond("❌ Format de durée invalide. Exemples: 1h30m, 2d, 30s", ephemeral=True)
            return
        
        if user.bot:
            await ctx.respond("❌ Impossible de bannir un bot.", ephemeral=True)
            return
        
        end_time = datetime.now() + timedelta(seconds=duration_seconds)
        
        # Send DM before applying punishment
        await self.send_dm_notification(user, "tempban", reason, duration_seconds, end_time)
        
        try:
            await user.ban(reason=reason)
            sanction_id = await self.add_sanction(user.id, ctx.author.id, ctx.guild.id, "tempban", reason, duration_seconds)
            await ctx.respond(f"🔨 {user.mention} a été banni temporairement pour {self.format_duration(duration_seconds)} (ID: {sanction_id}).")
            
            # Schedule unban
            asyncio.create_task(self._schedule_unban(ctx.guild, user, duration_seconds))
            
        except discord.Forbidden:
            await ctx.respond("❌ Je n'ai pas la permission de bannir cet utilisateur.")
    
    async def _schedule_unban(self, guild, user, duration_seconds):
        await asyncio.sleep(duration_seconds)
        try:
            await guild.unban(user, reason="Fin du bannissement temporaire")
        except:
            pass
    
    @commands.slash_command(name="ban", description="Bannir définitivement un utilisateur")
    async def ban(self, ctx, user: discord.Member, *, reason: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        
        required_roles = []
        if admin_role and admin_role != 'your_admin_role_id':
            required_roles.append(int(admin_role))
        if moderator_role and moderator_role != 'your_moderator_role_id':
            required_roles.append(int(moderator_role))
        
        if not self.has_permission(ctx.author, required_roles):
            await ctx.respond("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if user.bot:
            await ctx.respond("❌ Impossible de bannir un bot.", ephemeral=True)
            return
        
        # Send DM before applying punishment
        await self.send_dm_notification(user, "ban", reason)
        
        try:
            await user.ban(reason=reason)
            sanction_id = await self.add_sanction(user.id, ctx.author.id, ctx.guild.id, "ban", reason)
            await ctx.respond(f"🔨 {user.mention} a été banni définitivement (ID: {sanction_id}).")
        except discord.Forbidden:
            await ctx.respond("❌ Je n'ai pas la permission de bannir cet utilisateur.")
    
    @commands.slash_command(name="kick", description="Expulser un utilisateur")
    async def kick(self, ctx, user: discord.Member, *, reason: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        
        required_roles = []
        if admin_role and admin_role != 'your_admin_role_id':
            required_roles.append(int(admin_role))
        if moderator_role and moderator_role != 'your_moderator_role_id':
            required_roles.append(int(moderator_role))
        
        if not self.has_permission(ctx.author, required_roles):
            await ctx.respond("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        if user.bot:
            await ctx.respond("❌ Impossible d'expulser un bot.", ephemeral=True)
            return
        
        # Send DM before applying punishment
        await self.send_dm_notification(user, "kick", reason)
        
        try:
            await user.kick(reason=reason)
            sanction_id = await self.add_sanction(user.id, ctx.author.id, ctx.guild.id, "kick", reason)
            await ctx.respond(f"👋 {user.mention} a été expulsé (ID: {sanction_id}).")
        except discord.Forbidden:
            await ctx.respond("❌ Je n'ai pas la permission d'expulser cet utilisateur.")
    
    @commands.slash_command(name="unmute", description="Lever la sourdine d'un utilisateur")
    async def unmute(self, ctx, user: discord.Member):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        oracle_role = os.getenv('ORACLE_ROLE_ID')
        animator_role = os.getenv('ANIMATOR_ROLE_ID')
        
        required_roles = []
        for role in [admin_role, moderator_role, oracle_role, animator_role]:
            if role and not role.startswith('your_'):
                required_roles.append(int(role))
        
        if not self.has_permission(ctx.author, required_roles):
            await ctx.respond("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        try:
            await user.timeout(until=None)
            await ctx.respond(f"🔊 {user.mention} n'est plus en sourdine.")
        except discord.Forbidden:
            await ctx.respond("❌ Je n'ai pas la permission de lever la sourdine de cet utilisateur.")
    
    @commands.slash_command(name="untimeout", description="Lever le timeout d'un utilisateur")
    async def untimeout(self, ctx, user: discord.Member):
        await self.unmute(ctx, user)
    
    @commands.slash_command(name="unban", description="Débannir un utilisateur")
    async def unban(self, ctx, user_id: str):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        
        if not admin_role or admin_role == 'your_admin_role_id':
            await ctx.respond("❌ Rôle admin non configuré.", ephemeral=True)
            return
        
        if not self.has_permission(ctx.author, [int(admin_role)]):
            await ctx.respond("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        try:
            user_id = int(user_id)
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"Débanni par {ctx.author}")
            await ctx.respond(f"✅ {user.mention} a été débanni.")
        except ValueError:
            await ctx.respond("❌ ID utilisateur invalide.")
        except discord.NotFound:
            await ctx.respond("❌ Utilisateur non trouvé ou non banni.")
        except discord.Forbidden:
            await ctx.respond("❌ Je n'ai pas la permission de débannir cet utilisateur.")

    sanctions = discord.SlashCommandGroup("sanctions", "Gestion des sanctions")
    
    @sanctions.command(name="list", description="Afficher les sanctions d'un utilisateur")
    async def sanctions_list(self, ctx, user: discord.Member):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        moderator_role = os.getenv('MODERATOR_ROLE_ID')
        oracle_role = os.getenv('ORACLE_ROLE_ID')
        animator_role = os.getenv('ANIMATOR_ROLE_ID')
        
        required_roles = []
        for role in [admin_role, moderator_role, oracle_role, animator_role]:
            if role and not role.startswith('your_'):
                required_roles.append(int(role))
        
        if not self.has_permission(ctx.author, required_roles):
            await ctx.respond("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        sanctions = await self.get_user_sanctions(user.id, ctx.guild.id, active_only=False)
        view = SanctionsView(sanctions, user)
        await ctx.respond(embed=view.get_embed(), view=view)
    
    @sanctions.command(name="remove", description="Supprimer une sanction par son ID")
    async def sanctions_remove(self, ctx, user: discord.Member, sanction_id: int):
        admin_role = os.getenv('ADMIN_ROLE_ID')
        
        if not admin_role or admin_role == 'your_admin_role_id':
            await ctx.respond("❌ Rôle admin non configuré.", ephemeral=True)
            return
        
        if not self.has_permission(ctx.author, [int(admin_role)]):
            await ctx.respond("❌ Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
            return
        
        # Verify sanction exists and belongs to the user
        sanctions = await self.get_user_sanctions(user.id, ctx.guild.id, active_only=False)
        sanction_found = any(sanction[0] == sanction_id for sanction in sanctions)
        
        if not sanction_found:
            await ctx.respond(f"❌ Aucune sanction trouvée avec l'ID {sanction_id} pour {user.mention}.", ephemeral=True)
            return
        
        await self.remove_sanction(sanction_id)
        await ctx.respond(f"✅ Sanction ID {sanction_id} supprimée pour {user.mention}.")

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

def setup(bot):
    bot.add_cog(ModerationCog(bot))
