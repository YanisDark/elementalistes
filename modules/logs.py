# modules/logs.py
import discord
from discord.ext import commands
import asyncio
from datetime import datetime, timedelta
import os
import aiohttp
import aiofiles
import pytz
import aiosqlite
from typing import Optional, Union

class LogsModule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logs_channel_id = int(os.getenv('LOGS_STAFF_CHANNEL_ID'))
        self.admin_role_id = int(os.getenv('ADMIN_ROLE_ID'))
        self.timezone = pytz.timezone('Europe/Paris')
        self.media_folder = "saved_media"
        self.db_path = "data/stealth.db"
        self.stealth_users = set()
        self.ensure_folders()
        asyncio.create_task(self.init_database())
        
    def ensure_folders(self):
        """Crée les dossiers nécessaires s'ils n'existent pas"""
        os.makedirs(self.media_folder, exist_ok=True)
        os.makedirs("data", exist_ok=True)
            
    async def init_database(self):
        """Initialise la base de données stealth"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS stealth_users (
                    user_id INTEGER PRIMARY KEY,
                    enabled BOOLEAN DEFAULT TRUE
                )
            ''')
            await db.commit()
            
            # Charger les utilisateurs stealth
            async with db.execute('SELECT user_id FROM stealth_users WHERE enabled = TRUE') as cursor:
                rows = await cursor.fetchall()
                self.stealth_users = {row[0] for row in rows}
                
    async def toggle_stealth(self, user_id: int, enabled: bool):
        """Active/désactive le mode stealth pour un utilisateur"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT OR REPLACE INTO stealth_users (user_id, enabled) 
                VALUES (?, ?)
            ''', (user_id, enabled))
            await db.commit()
            
        if enabled:
            self.stealth_users.add(user_id)
        else:
            self.stealth_users.discard(user_id)
            
    def is_stealth(self, user_id: int) -> bool:
        """Vérifie si un utilisateur est en mode stealth"""
        return user_id in self.stealth_users
        
    def get_paris_time(self):
        """Retourne l'heure actuelle en timezone Paris"""
        return datetime.now(self.timezone)
        
    async def save_attachment(self, attachment: discord.Attachment, message_id: int, author_id: int) -> str:
        """Sauvegarde un fichier joint et retourne le chemin"""
        try:
            timestamp = self.get_paris_time().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{author_id}_{message_id}_{attachment.filename}"
            filepath = os.path.join(self.media_folder, filename)
            
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status == 200:
                        async with aiofiles.open(filepath, 'wb') as f:
                            async for chunk in resp.content.iter_chunked(8192):
                                await f.write(chunk)
                        return filepath
        except Exception as e:
            print(f"Erreur lors de la sauvegarde de {attachment.filename}: {e}")
        return None
        
    async def send_log(self, embed: discord.Embed, file: discord.File = None):
        """Envoie un log dans le canal staff"""
        try:
            channel = self.bot.get_channel(self.logs_channel_id)
            if channel:
                if file:
                    await channel.send(embed=embed, file=file)
                else:
                    await channel.send(embed=embed)
        except Exception as e:
            print(f"Erreur lors de l'envoi du log: {e}")

    def create_base_embed(self, title: str, color: discord.Color, user: discord.Member = None) -> discord.Embed:
        """Crée un embed de base avec le timestamp Paris"""
        paris_time = self.get_paris_time()
        embed = discord.Embed(
            title=title,
            color=color,
            timestamp=paris_time
        )
        if user:
            embed.set_author(name=f"{user.display_name} ({user.id})", icon_url=user.display_avatar.url)
        embed.set_footer(text="Les Élémentalistes • Logs", icon_url=self.bot.user.display_avatar.url)
        return embed

    # === COMMANDES ===
    
    @discord.app_commands.command(name="stealth", description="Active/désactive le mode stealth pour éviter les logs")
    @discord.app_commands.describe(mode="Activer ou désactiver le mode stealth")
    @discord.app_commands.choices(mode=[
        discord.app_commands.Choice(name="Activer", value="on"),
        discord.app_commands.Choice(name="Désactiver", value="off")
    ])
    async def stealth_command(self, interaction: discord.Interaction, mode: str):
        # Vérifier si l'utilisateur a le rôle SEIGNEUR (ADMIN)
        if not any(role.id == self.admin_role_id for role in interaction.user.roles):
            await interaction.response.send_message("❌ Vous n'avez pas les permissions nécessaires.", ephemeral=True)
            return
            
        enabled = mode == "on"
        await self.toggle_stealth(interaction.user.id, enabled)
        
        status = "activé" if enabled else "désactivé"
        emoji = "👻" if enabled else "👁️"
        await interaction.response.send_message(f"{emoji} Mode stealth **{status}** pour vous.", ephemeral=True)

    # === ÉVÉNEMENTS DE MESSAGES ===
    
    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.guild is None or message.author.bot or self.is_stealth(message.author.id):
            return
            
        embed = self.create_base_embed("📋 Message Supprimé", discord.Color.red(), message.author)
        embed.add_field(name="📍 Salon", value=f"<#{message.channel.id}>", inline=True)
        
        created_paris = message.created_at.replace(tzinfo=pytz.UTC).astimezone(self.timezone)
        embed.add_field(name="📅 Envoyé le", value=f"<t:{int(created_paris.timestamp())}:F>", inline=True)
        embed.add_field(name="🆔 ID Message", value=message.id, inline=True)
        
        content = message.content[:1024] if message.content else "*(Aucun contenu textuel)*"
        embed.add_field(name="💬 Contenu", value=content, inline=False)
        
        # Sauvegarder et gérer les médias
        if message.attachments:
            saved_files = []
            attachments_info = []
            
            for att in message.attachments:
                saved_path = await self.save_attachment(att, message.id, message.author.id)
                if saved_path:
                    saved_files.append((saved_path, att.filename))
                    attachments_info.append(f"• {att.filename} (`{att.size} octets`) - **Sauvegardé**")
                else:
                    attachments_info.append(f"• {att.filename} (`{att.size} octets`) - *Échec sauvegarde*")
                    
            embed.add_field(name="📎 Pièces jointes", value="\n".join(attachments_info[:10]), inline=False)
            
            # Envoyer l'embed principal
            await self.send_log(embed)
            
            # Envoyer les images sauvegardées séparément
            for saved_file, original_name in saved_files:
                if any(saved_file.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                    try:
                        image_embed = discord.Embed(
                            title="📎 Image du message supprimé",
                            color=discord.Color.red(),
                            timestamp=self.get_paris_time()
                        )
                        image_embed.add_field(name="📁 Fichier", value=original_name, inline=True)
                        image_embed.add_field(name="👤 Auteur", value=f"{message.author.mention}", inline=True)
                        image_embed.set_footer(text="Les Élémentalistes • Logs", icon_url=self.bot.user.display_avatar.url)
                        
                        file = discord.File(saved_file, filename=os.path.basename(saved_file))
                        image_embed.set_image(url=f"attachment://{os.path.basename(saved_file)}")
                        await self.send_log(image_embed, file)
                    except Exception as e:
                        print(f"Erreur lors de l'envoi de l'image {original_name}: {e}")
        else:
            await self.send_log(embed)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages):
        if not messages or messages[0].guild is None:
            return
            
        # Filtrer les messages non-stealth
        non_stealth_messages = [msg for msg in messages if not self.is_stealth(msg.author.id)]
        if not non_stealth_messages:
            return
            
        embed = self.create_base_embed("🗑️ Suppression en Masse", discord.Color.dark_red())
        embed.add_field(name="📍 Salon", value=f"<#{messages[0].channel.id}>", inline=True)
        embed.add_field(name="📊 Nombre", value=len(non_stealth_messages), inline=True)
        
        current_time = self.get_paris_time()
        embed.add_field(name="📅 Supprimés le", value=f"<t:{int(current_time.timestamp())}:F>", inline=True)
        
        authors = list(set([msg.author.mention for msg in non_stealth_messages if not msg.author.bot]))[:10]
        if authors:
            embed.add_field(name="👥 Auteurs", value="\n".join(authors), inline=False)
            
        # Compter et sauvegarder les médias
        total_attachments = 0
        saved_media = []
        
        for msg in non_stealth_messages:
            for att in msg.attachments:
                total_attachments += 1
                saved_path = await self.save_attachment(att, msg.id, msg.author.id)
                if saved_path:
                    saved_media.append((saved_path, att.filename, msg.author))
                    
        if total_attachments > 0:
            embed.add_field(name="📎 Médias supprimés", value=f"{total_attachments} fichier(s) - {len(saved_media)} sauvegardé(s)", inline=False)
            
        await self.send_log(embed)
        
        # Envoyer les médias sauvegardés
        for saved_file, original_name, author in saved_media[:5]:  # Limiter à 5 pour éviter le spam
            if any(saved_file.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                try:
                    image_embed = discord.Embed(
                        title="📎 Média de la suppression en masse",
                        color=discord.Color.dark_red(),
                        timestamp=self.get_paris_time()
                    )
                    image_embed.add_field(name="📁 Fichier", value=original_name, inline=True)
                    image_embed.add_field(name="👤 Auteur", value=author.mention, inline=True)
                    image_embed.set_footer(text="Les Élémentalistes • Logs", icon_url=self.bot.user.display_avatar.url)
                    
                    file = discord.File(saved_file, filename=os.path.basename(saved_file))
                    image_embed.set_image(url=f"attachment://{os.path.basename(saved_file)}")
                    await self.send_log(image_embed, file)
                except Exception as e:
                    print(f"Erreur lors de l'envoi du média {original_name}: {e}")

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if (before.guild is None or before.author.bot or 
            before.content == after.content or self.is_stealth(before.author.id)):
            return
            
        embed = self.create_base_embed("✏️ Message Modifié", discord.Color.orange(), before.author)
        embed.add_field(name="📍 Salon", value=f"<#{before.channel.id}>", inline=True)
        embed.add_field(name="🔗 Lien", value=f"[Aller au message]({after.jump_url})", inline=True)
        
        current_time = self.get_paris_time()
        embed.add_field(name="📅 Modifié le", value=f"<t:{int(current_time.timestamp())}:F>", inline=True)
        
        old_content = before.content[:512] if before.content else "*(Aucun contenu)*"
        new_content = after.content[:512] if after.content else "*(Aucun contenu)*"
        
        embed.add_field(name="📝 Avant", value=old_content, inline=False)
        embed.add_field(name="📝 Après", value=new_content, inline=False)
        
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        if self.is_stealth(invite.inviter.id if invite.inviter else 0):
            return
            
        embed = self.create_base_embed("📨 Invitation Créée", discord.Color.green(), invite.inviter)
        embed.add_field(name="🔗 Code", value=invite.code, inline=True)
        embed.add_field(name="📍 Salon", value=f"<#{invite.channel.id}>", inline=True)
        
        if invite.max_age:
            expire_time = self.get_paris_time() + timedelta(seconds=invite.max_age)
            embed.add_field(name="⏱️ Expire", value=f"<t:{int(expire_time.timestamp())}:R>", inline=True)
        else:
            embed.add_field(name="⏱️ Expire", value="Jamais", inline=True)
            
        embed.add_field(name="🔢 Utilisation max", value=invite.max_uses if invite.max_uses else "Illimitée", inline=True)
        
        await self.send_log(embed)

    # === ÉVÉNEMENTS DE MEMBRES ===
    
    @commands.Cog.listener()
    async def on_member_join(self, member):
        embed = self.create_base_embed("✅ Nouveau Membre", discord.Color.green(), member)
        
        created_paris = member.created_at.replace(tzinfo=pytz.UTC).astimezone(self.timezone)
        embed.add_field(name="📅 Compte créé", value=f"<t:{int(created_paris.timestamp())}:F>", inline=True)
        embed.add_field(name="🆔 ID", value=member.id, inline=True)
        embed.add_field(name="📊 Membres total", value=member.guild.member_count, inline=True)
        
        account_age = self.get_paris_time() - created_paris
        if account_age.days < 7:
            embed.add_field(name="⚠️ Attention", value=f"Compte récent ({account_age.days} jour(s))", inline=False)
        
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        embed = self.create_base_embed("❌ Membre Parti", discord.Color.red(), member)
        
        if member.joined_at:
            joined_paris = member.joined_at.replace(tzinfo=pytz.UTC).astimezone(self.timezone)
            embed.add_field(name="📅 Rejoint le", value=f"<t:{int(joined_paris.timestamp())}:F>", inline=True)
            
        embed.add_field(name="🆔 ID", value=member.id, inline=True)
        embed.add_field(name="📊 Membres restant", value=member.guild.member_count, inline=True)
        
        if member.roles[1:]:
            roles = ", ".join([role.mention for role in member.roles[1:][:10]])
            embed.add_field(name="🎭 Rôles", value=roles, inline=False)
            
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        embed = self.create_base_embed("🔨 Membre Banni", discord.Color.dark_red())
        embed.set_author(name=f"{user.display_name} ({user.id})", icon_url=user.display_avatar.url)
        embed.add_field(name="🆔 ID", value=user.id, inline=True)
        
        current_time = self.get_paris_time()
        embed.add_field(name="📅 Banni le", value=f"<t:{int(current_time.timestamp())}:F>", inline=True)
        
        try:
            ban = await guild.fetch_ban(user)
            if ban.reason:
                embed.add_field(name="📝 Raison", value=ban.reason, inline=False)
        except:
            pass
        
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild, user):
        embed = self.create_base_embed("🔓 Membre Débanni", discord.Color.green())
        embed.set_author(name=f"{user.display_name} ({user.id})", icon_url=user.display_avatar.url)
        embed.add_field(name="🆔 ID", value=user.id, inline=True)
        
        current_time = self.get_paris_time()
        embed.add_field(name="📅 Débanni le", value=f"<t:{int(current_time.timestamp())}:F>", inline=True)
        
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if self.is_stealth(after.id):
            return
            
        # Changement de pseudo
        if before.display_name != after.display_name:
            embed = self.create_base_embed("📝 Pseudo Modifié", discord.Color.blue(), after)
            embed.add_field(name="👤 Avant", value=before.display_name, inline=True)
            embed.add_field(name="👤 Après", value=after.display_name, inline=True)
            
            current_time = self.get_paris_time()
            embed.add_field(name="📅 Modifié le", value=f"<t:{int(current_time.timestamp())}:F>", inline=True)
            await self.send_log(embed)
            
        # Changement de rôles
        if before.roles != after.roles:
            added_roles = [role for role in after.roles if role not in before.roles]
            removed_roles = [role for role in before.roles if role not in after.roles]
            
            if added_roles:
                embed = self.create_base_embed("➕ Rôle Ajouté", discord.Color.green(), after)
                roles_text = ", ".join([role.mention for role in added_roles])
                embed.add_field(name="🎭 Rôle(s) ajouté(s)", value=roles_text, inline=False)
                await self.send_log(embed)
                
            if removed_roles:
                embed = self.create_base_embed("➖ Rôle Retiré", discord.Color.red(), after)
                roles_text = ", ".join([role.mention for role in removed_roles])
                embed.add_field(name="🎭 Rôle(s) retiré(s)", value=roles_text, inline=False)
                await self.send_log(embed)

    # === ÉVÉNEMENTS DE SERVEUR ===
    
    @commands.Cog.listener()
    async def on_guild_update(self, before, after):
        changes = []
        
        if before.name != after.name:
            changes.append(f"**Nom:** {before.name} → {after.name}")
        if before.description != after.description:
            changes.append(f"**Description:** {before.description or 'Aucune'} → {after.description or 'Aucune'}")
        if before.verification_level != after.verification_level:
            levels = {
                discord.VerificationLevel.none: "Aucune",
                discord.VerificationLevel.low: "Faible", 
                discord.VerificationLevel.medium: "Moyenne",
                discord.VerificationLevel.high: "Élevée",
                discord.VerificationLevel.highest: "Maximale"
            }
            before_level = levels.get(before.verification_level, str(before.verification_level))
            after_level = levels.get(after.verification_level, str(after.verification_level))
            changes.append(f"**Niveau de vérification:** {before_level} → {after_level}")
            
        if changes:
            embed = self.create_base_embed("🏰 Serveur Mis à Jour", discord.Color.blue())
            embed.add_field(name="📝 Modifications", value="\n".join(changes), inline=False)
            await self.send_log(embed)

    # === ÉVÉNEMENTS DE SALONS ===
    
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        embed = self.create_base_embed("📁 Salon Créé", discord.Color.green())
        embed.add_field(name="📍 Salon", value=f"<#{channel.id}>", inline=True)
        
        channel_types = {
            discord.ChannelType.text: "Textuel",
            discord.ChannelType.voice: "Vocal", 
            discord.ChannelType.category: "Catégorie",
            discord.ChannelType.news: "Annonces",
            discord.ChannelType.stage_voice: "Conférence",
            discord.ChannelType.forum: "Forum"
        }
        channel_type = channel_types.get(channel.type, str(channel.type))
        embed.add_field(name="🏷️ Type", value=channel_type, inline=True)
        embed.add_field(name="📂 Catégorie", value=channel.category.name if channel.category else "Aucune", inline=True)
        
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        embed = self.create_base_embed("🗑️ Salon Supprimé", discord.Color.red())
        embed.add_field(name="📍 Nom", value=channel.name, inline=True)
        
        channel_types = {
            discord.ChannelType.text: "Textuel",
            discord.ChannelType.voice: "Vocal",
            discord.ChannelType.category: "Catégorie", 
            discord.ChannelType.news: "Annonces",
            discord.ChannelType.stage_voice: "Conférence",
            discord.ChannelType.forum: "Forum"
        }
        channel_type = channel_types.get(channel.type, str(channel.type))
        embed.add_field(name="🏷️ Type", value=channel_type, inline=True)
        embed.add_field(name="🆔 ID", value=channel.id, inline=True)
        
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        changes = []
        
        if before.name != after.name:
            changes.append(f"**Nom:** {before.name} → {after.name}")
        if hasattr(before, 'topic') and before.topic != after.topic:
            changes.append(f"**Sujet:** {before.topic or 'Aucun'} → {after.topic or 'Aucun'}")
        if hasattr(before, 'slowmode_delay') and before.slowmode_delay != after.slowmode_delay:
            changes.append(f"**Mode lent:** {before.slowmode_delay}s → {after.slowmode_delay}s")
        if hasattr(before, 'nsfw') and before.nsfw != after.nsfw:
            changes.append(f"**NSFW:** {'Oui' if before.nsfw else 'Non'} → {'Oui' if after.nsfw else 'Non'}")
            
        if changes:
            embed = self.create_base_embed("📝 Salon Mis à Jour", discord.Color.blue())
            embed.add_field(name="📍 Salon", value=f"<#{after.id}>", inline=True)
            embed.add_field(name="📝 Modifications", value="\n".join(changes), inline=False)
            await self.send_log(embed)

    # === ÉVÉNEMENTS DE RÔLES ===
    
    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        embed = self.create_base_embed("🎭 Rôle Créé", discord.Color.green())
        embed.add_field(name="🏷️ Nom", value=role.name, inline=True)
        embed.add_field(name="🎨 Couleur", value=str(role.color), inline=True)
        embed.add_field(name="🆔 ID", value=role.id, inline=True)
        embed.add_field(name="📍 Position", value=role.position, inline=True)
        embed.add_field(name="👑 Hoiste", value="Oui" if role.hoist else "Non", inline=True)
        embed.add_field(name="🤖 Mentionnable", value="Oui" if role.mentionable else "Non", inline=True)
        
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        embed = self.create_base_embed("🗑️ Rôle Supprimé", discord.Color.red())
        embed.add_field(name="🏷️ Nom", value=role.name, inline=True)
        embed.add_field(name="🎨 Couleur", value=str(role.color), inline=True)
        embed.add_field(name="🆔 ID", value=role.id, inline=True)
        
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        changes = []
        
        if before.name != after.name:
            changes.append(f"**Nom:** {before.name} → {after.name}")
        if before.color != after.color:
            changes.append(f"**Couleur:** {before.color} → {after.color}")
        if before.hoist != after.hoist:
            changes.append(f"**Affiché séparément:** {'Oui' if before.hoist else 'Non'} → {'Oui' if after.hoist else 'Non'}")
        if before.mentionable != after.mentionable:
            changes.append(f"**Mentionnable:** {'Oui' if before.mentionable else 'Non'} → {'Oui' if after.mentionable else 'Non'}")
        if before.permissions != after.permissions:
            changes.append("**Permissions modifiées**")
            
        if changes:
            embed = self.create_base_embed("🎭 Rôle Mis à Jour", discord.Color.blue())
            embed.add_field(name="🏷️ Rôle", value=after.mention, inline=True)
            embed.add_field(name="📝 Modifications", value="\n".join(changes), inline=False)
            await self.send_log(embed)

    # === ÉVÉNEMENTS D'ÉMOJIS ===
    
    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        added_emojis = [emoji for emoji in after if emoji not in before]
        removed_emojis = [emoji for emoji in before if emoji not in after]
        
        for emoji in added_emojis:
            embed = self.create_base_embed("😀 Émoji Ajouté", discord.Color.green())
            embed.add_field(name="🏷️ Nom", value=emoji.name, inline=True)
            embed.add_field(name="🆔 ID", value=emoji.id, inline=True)
            embed.add_field(name="🔗 URL", value=f"[Lien]({emoji.url})", inline=True)
            embed.add_field(name="🔒 Animé", value="Oui" if emoji.animated else "Non", inline=True)
            embed.set_thumbnail(url=emoji.url)
            await self.send_log(embed)
            
        for emoji in removed_emojis:
            embed = self.create_base_embed("😢 Émoji Supprimé", discord.Color.red())
            embed.add_field(name="🏷️ Nom", value=emoji.name, inline=True)
            embed.add_field(name="🆔 ID", value=emoji.id, inline=True)
            embed.add_field(name="🔒 Animé", value="Oui" if emoji.animated else "Non", inline=True)
            await self.send_log(embed)

    # === ÉVÉNEMENTS VOCAUX ===
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if self.is_stealth(member.id):
            return
            
        # Membre rejoint un vocal
        if before.channel is None and after.channel is not None:
            embed = self.create_base_embed("🔊 Connexion Vocale", discord.Color.green(), member)
            embed.add_field(name="📍 Salon", value=f"<#{after.channel.id}>", inline=True)
            await self.send_log(embed)
            
        # Membre quitte un vocal
        elif before.channel is not None and after.channel is None:
            embed = self.create_base_embed("🔇 Déconnexion Vocale", discord.Color.red(), member)
            embed.add_field(name="📍 Salon", value=f"<#{before.channel.id}>", inline=True)
            await self.send_log(embed)
            
        # Membre déplacé
        elif before.channel != after.channel and before.channel is not None and after.channel is not None:
            embed = self.create_base_embed("↔️ Déplacement Vocal", discord.Color.blue(), member)
            embed.add_field(name="📍 De", value=f"<#{before.channel.id}>", inline=True)
            embed.add_field(name="📍 Vers", value=f"<#{after.channel.id}>", inline=True)
            await self.send_log(embed)
            
        # Membre mis en muet
        if before.mute != after.mute:
            if after.mute:
                embed = self.create_base_embed("🔇 Membre Muet", discord.Color.orange(), member)
            else:
                embed = self.create_base_embed("🔊 Membre Démute", discord.Color.green(), member)
            embed.add_field(name="📍 Salon", value=f"<#{after.channel.id}>" if after.channel else "Aucun", inline=True)
            await self.send_log(embed)
            
        # Membre mis en sourdine
        if before.deaf != after.deaf:
            if after.deaf:
                embed = self.create_base_embed("🔕 Membre Assourdi", discord.Color.orange(), member)
            else:
                embed = self.create_base_embed("🔔 Membre Désassourdi", discord.Color.green(), member)
            embed.add_field(name="📍 Salon", value=f"<#{after.channel.id}>" if after.channel else "Aucun", inline=True)
            await self.send_log(embed)

    # === ÉVÉNEMENTS DE FILS ===
    
    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        if thread.owner and self.is_stealth(thread.owner.id):
            return
            
        embed = self.create_base_embed("🧵 Fil de Discussion Créé", discord.Color.green())
        embed.add_field(name="🏷️ Nom", value=thread.name, inline=True)
        embed.add_field(name="📍 Parent", value=f"<#{thread.parent.id}>", inline=True)
        embed.add_field(name="🆔 ID", value=thread.id, inline=True)
        embed.add_field(name="👤 Créateur", value=thread.owner.mention if thread.owner else "Inconnu", inline=True)
        
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread):
        embed = self.create_base_embed("🗑️ Fil de Discussion Supprimé", discord.Color.red())
        embed.add_field(name="🏷️ Nom", value=thread.name, inline=True)
        embed.add_field(name="📍 Parent", value=f"<#{thread.parent.id}>", inline=True)
        embed.add_field(name="🆔 ID", value=thread.id, inline=True)
        
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_thread_update(self, before, after):
        changes = []
        
        if before.name != after.name:
            changes.append(f"**Nom:** {before.name} → {after.name}")
        if before.archived != after.archived:
            changes.append(f"**Archivé:** {'Oui' if before.archived else 'Non'} → {'Oui' if after.archived else 'Non'}")
        if before.locked != after.locked:
            changes.append(f"**Verrouillé:** {'Oui' if before.locked else 'Non'} → {'Oui' if after.locked else 'Non'}")
            
        if changes:
            embed = self.create_base_embed("🧵 Fil de Discussion Mis à Jour", discord.Color.blue())
            embed.add_field(name="🏷️ Fil", value=after.name, inline=True)
            embed.add_field(name="📍 Parent", value=f"<#{after.parent.id}>", inline=True)
            embed.add_field(name="📝 Modifications", value="\n".join(changes), inline=False)
            await self.send_log(embed)

async def setup(bot):
    await bot.add_cog(LogsModule(bot))
