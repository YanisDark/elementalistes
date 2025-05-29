# modules/temporary_channels.py
import discord
from discord.ext import commands, tasks
import aiosqlite
import os
from dotenv import load_dotenv
import asyncio
import logging
import json
from .rate_limiter import get_rate_limiter, safe_api_call

load_dotenv()

# Get the rate limiter instance
rate_limiter = get_rate_limiter()

class TemporaryChannels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.creer_vocal_id = int(os.getenv('CREER_VOCAL_CHANNEL_ID', 0))
        self.vocal_category_id = int(os.getenv('VOCAL_CATEGORY_ID', 0))
        self.db_path = 'temporary_channels.db'
        self.control_messages = {}  # Store message references
        
    async def cog_load(self):
        await self.init_db()
        self.cleanup_task.start()
        self.rate_limit_cleanup.start()
        
    async def cog_unload(self):
        self.cleanup_task.cancel()
        self.rate_limit_cleanup.cancel()
        
    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS temp_channels (
                    channel_id INTEGER PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    channel_type TEXT DEFAULT 'open',
                    soundboards_enabled BOOLEAN DEFAULT 1
                )
            ''')
            
            await db.execute('''
                CREATE TABLE IF NOT EXISTS channel_whitelist (
                    channel_id INTEGER,
                    user_id INTEGER,
                    PRIMARY KEY (channel_id, user_id),
                    FOREIGN KEY (channel_id) REFERENCES temp_channels (channel_id) ON DELETE CASCADE
                )
            ''')
            
            await db.execute('''
                CREATE TABLE IF NOT EXISTS channel_blacklist (
                    channel_id INTEGER,
                    user_id INTEGER,
                    PRIMARY KEY (channel_id, user_id),
                    FOREIGN KEY (channel_id) REFERENCES temp_channels (channel_id) ON DELETE CASCADE
                )
            ''')
            
            await db.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    channel_type TEXT DEFAULT 'open',
                    soundboards_enabled BOOLEAN DEFAULT 1,
                    whitelist_json TEXT DEFAULT '[]',
                    blacklist_json TEXT DEFAULT '[]'
                )
            ''')
            
            await db.commit()
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if after.channel and after.channel.id == self.creer_vocal_id:
            await self.create_temp_channel(member)
        
        if before.channel and await self.is_temp_channel(before.channel.id):
            await self.handle_leave_temp_channel(before.channel, member)
    
    async def create_temp_channel(self, member):
        guild = member.guild
        category = discord.utils.get(guild.categories, id=self.vocal_category_id) if self.vocal_category_id else None
        
        if not category:
            creer_channel = guild.get_channel(self.creer_vocal_id)
            if creer_channel:
                category = creer_channel.category
        
        if not category:
            logging.error("No valid category found for temporary channels")
            return
        
        temp_count = 0
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT COUNT(*) FROM temp_channels') as cursor:
                row = await cursor.fetchone()
                temp_count = row[0] if row else 0
        
        channel_name = f"🌀 Portail #{temp_count + 1}"
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(connect=True, view_channel=True),
            member: discord.PermissionOverwrite(
                manage_channels=True,
                manage_permissions=True,
                connect=True,
                view_channel=True,
                use_soundboard=True
            )
        }
        
        # Create voice channel with rate limiting
        channel = await rate_limiter.execute_request(
            category.create_voice_channel(
                name=channel_name,
                overwrites=overwrites
            ),
            route=f'POST /guilds/{guild.id}/channels',
            major_params={'guild_id': guild.id}
        )
        
        if not channel:
            logging.error("Failed to create temporary voice channel")
            return
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                'INSERT INTO temp_channels (channel_id, owner_id) VALUES (?, ?)',
                (channel.id, member.id)
            )
            await db.commit()
        
        # Move member with rate limiting
        await rate_limiter.execute_request(
            member.move_to(channel),
            route=f'PATCH /guilds/{guild.id}/members/{member.id}',
            major_params={'guild_id': guild.id}
        )
        
        # Wait a moment for the channel to be fully created
        await asyncio.sleep(1)
        await self.send_control_embed(channel, member.id)
    
    async def send_control_embed(self, channel, owner_id):
        owner = self.bot.get_user(owner_id)
        
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT channel_type, soundboards_enabled FROM temp_channels WHERE channel_id = ?',
                (channel.id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return
                channel_type, soundboards = row
            
            async with db.execute(
                'SELECT user_id FROM channel_whitelist WHERE channel_id = ?',
                (channel.id,)
            ) as cursor:
                whitelist_ids = [row[0] for row in await cursor.fetchall()]
            
            async with db.execute(
                'SELECT user_id FROM channel_blacklist WHERE channel_id = ?',
                (channel.id,)
            ) as cursor:
                blacklist_ids = [row[0] for row in await cursor.fetchall()]
        
        embed = discord.Embed(
            title="⚙️ Configuration du Canal Temporaire",
            description=f"Bienvenue **{owner.display_name if owner else 'Inconnu'}** ! Vous êtes maintenant propriétaire de ce canal vocal.\nUtilisez les boutons ci-dessous pour personnaliser votre expérience.",
            color=0x5865F2
        )
        
        # Current state display
        state_emojis = {
            'open': '🟢',
            'fermé': '🔒', 
            'privé': '🔴'
        }
        
        embed.add_field(
            name="📊 État Actuel",
            value=f"{state_emojis[channel_type]} **{channel_type.title()}**\n🎵 Soundboards: {'✅ Activés' if soundboards else '❌ Désactivés'}",
            inline=True
        )
        
        embed.add_field(
            name="👥 Membres Gérés",
            value=f"📝 Liste Blanche: **{len(whitelist_ids)}** membres\n🚫 Liste Noire: **{len(blacklist_ids)}** membres",
            inline=True
        )
        
        embed.add_field(
            name="",
            value="",
            inline=False
        )
        
        # Shorter explanations in paragraph form
        embed.add_field(
            name="🌍 Mode Ouvert",
            value="Tout le monde peut voir et rejoindre le canal sans restriction.",
            inline=True
        )
        
        embed.add_field(
            name="🔒 Mode Fermé", 
            value="Le canal reste visible mais seuls les membres de la liste blanche peuvent le rejoindre.",
            inline=True
        )
        
        embed.add_field(
            name="🔴 Mode Privé",
            value="Le canal devient invisible et inaccessible au public. Seule la liste blanche peut le voir et s'y connecter.",
            inline=True
        )
        
        embed.add_field(
            name="📝 Liste Blanche",
            value="Donne l'accès au canal même en mode fermé ou privé.",
            inline=True
        )
        
        embed.add_field(
            name="🚫 Liste Noire",
            value="Bloque l'accès au canal et éjecte automatiquement les membres concernés si présents.",
            inline=True
        )
        
        embed.add_field(
            name="🎵 Soundboards",
            value="Active ou désactive l'utilisation des effets sonores Discord pour tous les membres du canal.",
            inline=True
        )
        
        if whitelist_ids or blacklist_ids:
            lists_value = ""
            if whitelist_ids:
                whitelist_names = []
                for user_id in whitelist_ids[:5]:
                    user = self.bot.get_user(user_id)
                    whitelist_names.append(f"• {user.display_name if user else f'ID: {user_id}'}")
                lists_value += f"📝 **Liste Blanche:**\n" + "\n".join(whitelist_names)
                if len(whitelist_ids) > 5:
                    lists_value += f"\n*... et {len(whitelist_ids) - 5} autres*"
                lists_value += "\n\n"
            
            if blacklist_ids:
                blacklist_names = []
                for user_id in blacklist_ids[:5]:
                    user = self.bot.get_user(user_id)
                    blacklist_names.append(f"• {user.display_name if user else f'ID: {user_id}'}")
                lists_value += f"🚫 **Liste Noire:**\n" + "\n".join(blacklist_names)
                if len(blacklist_ids) > 5:
                    lists_value += f"\n*... et {len(blacklist_ids) - 5} autres*"
            
            embed.add_field(
                name="👤 Membres dans les Listes",
                value=lists_value,
                inline=False
            )
        
        embed.add_field(
            name="💾 Paramètres Sauvegardés",
            value="• **Sauvegarder** : Enregistre la configuration actuelle\n• **Utiliser paramètres** : Applique votre configuration sauvegardée\n• **Transférer** : Donne la propriété à un autre membre",
            inline=False
        )
        
        embed.set_footer(text="💡 Astuce: Vous pouvez mentionner plusieurs utilisateurs d'un coup pour les listes !")
        
        view = ChannelControlView(self, channel.id, owner_id, channel_type, soundboards)
        
        try:
            # Check if we already have a control message for this channel
            if channel.id in self.control_messages:
                try:
                    await rate_limiter.safe_edit(self.control_messages[channel.id], embed=embed, view=view)
                    return
                except (discord.NotFound, discord.HTTPException):
                    # Message was deleted, remove from cache
                    del self.control_messages[channel.id]
            
            # Send new message
            message = await rate_limiter.safe_send(channel, embed=embed, view=view)
            if message:
                self.control_messages[channel.id] = message
            
        except discord.Forbidden:
            # Fallback to COMMANDES_ADMIN if can't send to voice channel
            admin_channel_id = int(os.getenv('COMMANDES_ADMIN_CHANNEL_ID', 0))
            if admin_channel_id:
                admin_channel = channel.guild.get_channel(admin_channel_id)
                if admin_channel:
                    embed.add_field(name="🎯 Canal Concerné", value=f"{channel.name} ({channel.id})", inline=False)
                    message = await rate_limiter.safe_send(admin_channel, embed=embed, view=view)
                    if message:
                        self.control_messages[channel.id] = message
            logging.error(f"Cannot send message to voice channel {channel.id}")
        except Exception as e:
            logging.error(f"Error sending control embed: {e}")
    
    async def is_temp_channel(self, channel_id):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT 1 FROM temp_channels WHERE channel_id = ?',
                (channel_id,)
            ) as cursor:
                return await cursor.fetchone() is not None
    
    async def handle_leave_temp_channel(self, channel, member):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                'SELECT owner_id FROM temp_channels WHERE channel_id = ?',
                (channel.id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return
                owner_id = row[0]
        
        if member.id == owner_id and len(channel.members) > 0:
            embed = discord.Embed(
                title="👑 Canal Sans Propriétaire",
                description=f"Le propriétaire du canal **{channel.name}** a quitté.\nSi vous êtes dans ce canal, vous pouvez en devenir le nouveau propriétaire !",
                color=0xf39c12
            )
            embed.add_field(
                name="ℹ️ Comment procéder ?",
                value="• Assurez-vous d'être connecté au canal vocal\n• Cliquez sur le bouton ci-dessous\n• Vous obtiendrez tous les droits de gestion",
                inline=False
            )
            view = ClaimOwnershipView(self, channel.id)
            try:
                await rate_limiter.safe_send(channel, embed=embed, view=view)
            except discord.Forbidden:
                admin_channel_id = int(os.getenv('COMMANDES_ADMIN_CHANNEL_ID', 0))
                if admin_channel_id:
                    admin_channel = channel.guild.get_channel(admin_channel_id)
                    if admin_channel:
                        embed.add_field(name="🎯 Canal", value=f"{channel.name} ({channel.id})", inline=False)
                        await rate_limiter.safe_send(admin_channel, embed=embed, view=view)
        elif len(channel.members) == 0:
            await self.delete_temp_channel(channel.id)
    
    async def delete_temp_channel(self, channel_id):
        # Remove from control messages cache
        if channel_id in self.control_messages:
            del self.control_messages[channel_id]
            
        channel = self.bot.get_channel(channel_id)
        if channel:
            try:
                await rate_limiter.safe_channel_delete(channel)
            except (discord.NotFound, discord.Forbidden):
                pass
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('DELETE FROM temp_channels WHERE channel_id = ?', (channel_id,))
            await db.commit()
    
    @tasks.loop(minutes=5)
    async def cleanup_task(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute('SELECT channel_id FROM temp_channels') as cursor:
                channel_ids = [row[0] for row in await cursor.fetchall()]
        
        for channel_id in channel_ids:
            channel = self.bot.get_channel(channel_id)
            if not channel or len(channel.members) == 0:
                await self.delete_temp_channel(channel_id)

    @tasks.loop(minutes=30)
    async def rate_limit_cleanup(self):
        """Clean up expired rate limit buckets"""
        await rate_limiter.cleanup_expired_buckets()

    @cleanup_task.before_loop
    async def before_cleanup_task(self):
        await self.bot.wait_until_ready()

    @rate_limit_cleanup.before_loop
    async def before_rate_limit_cleanup(self):
        await self.bot.wait_until_ready()

class ChannelControlView(discord.ui.View):
    def __init__(self, cog, channel_id, owner_id, current_type, soundboards_enabled):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel_id = channel_id
        self.owner_id = owner_id
        self.current_type = current_type
        self.soundboards_enabled = soundboards_enabled
        
        # Update button styles based on current state
        self.update_button_styles()
    
    def update_button_styles(self):
        # Clear existing items and re-add with correct styles
        self.clear_items()
        
        # Channel type buttons
        self.add_item(discord.ui.Button(
            label="🌍 Ouvert",
            style=discord.ButtonStyle.green if self.current_type == 'open' else discord.ButtonStyle.secondary,
            custom_id="open",
            row=0
        ))
        
        self.add_item(discord.ui.Button(
            label="🔒 Fermé",
            style=discord.ButtonStyle.green if self.current_type == 'fermé' else discord.ButtonStyle.secondary,
            custom_id="fermé",
            row=0
        ))
        
        self.add_item(discord.ui.Button(
            label="🔴 Privé",
            style=discord.ButtonStyle.green if self.current_type == 'privé' else discord.ButtonStyle.secondary,
            custom_id="privé",
            row=0
        ))
        
        # Management buttons
        self.add_item(discord.ui.Button(
            label="📝 Liste Blanche",
            style=discord.ButtonStyle.primary,
            custom_id="whitelist",
            row=1
        ))
        
        self.add_item(discord.ui.Button(
            label="🚫 Liste Noire",
            style=discord.ButtonStyle.danger,
            custom_id="blacklist",
            row=1
        ))
        
        self.add_item(discord.ui.Button(
            label="🗑️ Retirer des listes",
            style=discord.ButtonStyle.secondary,
            custom_id="remove_from_lists",
            row=1
        ))
        
        # Settings buttons
        self.add_item(discord.ui.Button(
            label="🎵 Soundboards",
            style=discord.ButtonStyle.green if self.soundboards_enabled else discord.ButtonStyle.secondary,
            custom_id="soundboards",
            row=2
        ))
        
        self.add_item(discord.ui.Button(
            label="👑 Transférer",
            style=discord.ButtonStyle.danger,
            custom_id="transfer",
            row=2
        ))
        
        self.add_item(discord.ui.Button(
            label="💾 Sauvegarder",
            style=discord.ButtonStyle.secondary,
            custom_id="save",
            row=3
        ))
        
        self.add_item(discord.ui.Button(
            label="📋 Utiliser paramètres",
            style=discord.ButtonStyle.secondary,
            custom_id="load",
            row=3
        ))
        
        # Set callbacks for all buttons
        for item in self.children:
            if item.custom_id == "open":
                item.callback = self.open_channel
            elif item.custom_id == "fermé":
                item.callback = self.close_channel
            elif item.custom_id == "privé":
                item.callback = self.private_channel
            elif item.custom_id == "whitelist":
                item.callback = self.whitelist
            elif item.custom_id == "blacklist":
                item.callback = self.blacklist
            elif item.custom_id == "remove_from_lists":
                item.callback = self.remove_from_lists
            elif item.custom_id == "transfer":
                item.callback = self.transfer_ownership
            elif item.custom_id == "soundboards":
                item.callback = self.toggle_soundboards
            elif item.custom_id == "save":
                item.callback = self.save_settings
            elif item.custom_id == "load":
                item.callback = self.load_settings
    
    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Vous n'êtes pas le propriétaire de ce canal.", ephemeral=True)
            return False
        return True
    
    async def open_channel(self, interaction):
        await self.set_channel_type(interaction, 'open')
    
    async def close_channel(self, interaction):
        await self.set_channel_type(interaction, 'fermé')
    
    async def private_channel(self, interaction):
        await self.set_channel_type(interaction, 'privé')
    
    async def whitelist(self, interaction):
        await interaction.response.send_message(
            "📝 **Liste Blanche**\n\nMentionnez les utilisateurs ou donnez leurs IDs (séparés par des espaces) à ajouter à la liste blanche:\n\n*Exemple: @User1 @User2 123456789*",
            ephemeral=True
        )
        
        def check(m):
            return m.author.id == interaction.user.id and m.channel == interaction.channel
        
        try:
            message = await self.cog.bot.wait_for('message', check=check, timeout=60.0)
            await self.process_user_list(interaction, message.content, 'whitelist')
            try:
                await rate_limiter.safe_delete(message)
            except discord.NotFound:
                pass
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Temps écoulé.", ephemeral=True)
    
    async def blacklist(self, interaction):
        await interaction.response.send_message(
            "🚫 **Liste Noire**\n\nMentionnez les utilisateurs ou donnez leurs IDs (séparés par des espaces) à ajouter à la liste noire:\n\n*Exemple: @User1 @User2 123456789*",
            ephemeral=True
        )
        
        def check(m):
            return m.author.id == interaction.user.id and m.channel == interaction.channel
        
        try:
            message = await self.cog.bot.wait_for('message', check=check, timeout=60.0)
            await self.process_user_list(interaction, message.content, 'blacklist')
            try:
                await rate_limiter.safe_delete(message)
            except discord.NotFound:
                pass
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Temps écoulé.", ephemeral=True)
    
    async def remove_from_lists(self, interaction):
        await interaction.response.send_message(
            "🗑️ **Retirer des Listes**\n\nMentionnez les utilisateurs ou donnez leurs IDs (séparés par des espaces) à retirer des listes blanche et noire:\n\n*Exemple: @User1 @User2 123456789*",
            ephemeral=True
        )
        
        def check(m):
            return m.author.id == interaction.user.id and m.channel == interaction.channel
        
        try:
            message = await self.cog.bot.wait_for('message', check=check, timeout=60.0)
            await self.process_user_removal(interaction, message.content)
            try:
                await rate_limiter.safe_delete(message)
            except discord.NotFound:
                pass
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Temps écoulé.", ephemeral=True)
    
    async def transfer_ownership(self, interaction):
        await interaction.response.send_message(
            "👑 **Transfert de Propriété**\n\nMentionnez l'utilisateur ou donnez son ID pour transférer la propriété du canal:\n\n*Exemple: @NewOwner ou 123456789*\n\n⚠️ Cette action est irréversible !",
            ephemeral=True
        )
        
        def check(m):
            return m.author.id == interaction.user.id and m.channel == interaction.channel
        
        try:
            message = await self.cog.bot.wait_for('message', check=check, timeout=60.0)
            await self.transfer_ownership_process(interaction, message.content)
            try:
                await rate_limiter.safe_delete(message)
            except discord.NotFound:
                pass
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Temps écoulé.", ephemeral=True)
    
    async def toggle_soundboards(self, interaction):
        async with aiosqlite.connect(self.cog.db_path) as db:
            async with db.execute(
                'SELECT soundboards_enabled FROM temp_channels WHERE channel_id = ?',
                (self.channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return
                current_state = bool(row[0])
            
            new_state = not current_state
            await db.execute(
                'UPDATE temp_channels SET soundboards_enabled = ? WHERE channel_id = ?',
                (new_state, self.channel_id)
            )
            await db.commit()
        
        channel = self.cog.bot.get_channel(self.channel_id)
        if channel:
            # Apply soundboard permissions to all non-owner members
            for member in channel.members:
                if member.id == self.owner_id:
                    continue
                overwrites = channel.overwrites_for(member)
                if new_state:
                    # Enabled: Set to None (neutral/default permission)
                    overwrites.use_soundboard = None
                else:
                    # Disabled: Explicitly deny
                    overwrites.use_soundboard = False
                await rate_limiter.safe_channel_edit(channel, overwrites={
                    **channel.overwrites,
                    member: overwrites
                })
        
        self.soundboards_enabled = new_state
        await interaction.response.send_message(
            f"🎵 Soundboards {'✅ activés' if new_state else '❌ désactivés'}.",
            ephemeral=True
        )
        await self.refresh_embed()
    
    async def save_settings(self, interaction):
        async with aiosqlite.connect(self.cog.db_path) as db:
            async with db.execute(
                'SELECT channel_type, soundboards_enabled FROM temp_channels WHERE channel_id = ?',
                (self.channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return
                channel_type, soundboards = row
            
            async with db.execute(
                'SELECT user_id FROM channel_whitelist WHERE channel_id = ?',
                (self.channel_id,)
            ) as cursor:
                whitelist = [str(row[0]) for row in await cursor.fetchall()]
            
            async with db.execute(
                'SELECT user_id FROM channel_blacklist WHERE channel_id = ?',
                (self.channel_id,)
            ) as cursor:
                blacklist = [str(row[0]) for row in await cursor.fetchall()]
            
            await db.execute(
                '''INSERT OR REPLACE INTO user_settings 
                   (user_id, channel_type, soundboards_enabled, whitelist_json, blacklist_json)
                   VALUES (?, ?, ?, ?, ?)''',
                (self.owner_id, channel_type, soundboards, json.dumps(whitelist), json.dumps(blacklist))
            )
            await db.commit()
        
        await interaction.response.send_message("💾 Paramètres sauvegardés avec succès !", ephemeral=True)
    
    async def load_settings(self, interaction):
        async with aiosqlite.connect(self.cog.db_path) as db:
            async with db.execute(
                'SELECT channel_type, soundboards_enabled, whitelist_json, blacklist_json FROM user_settings WHERE user_id = ?',
                (self.owner_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    await interaction.response.send_message("❌ Aucun paramètre sauvegardé trouvé.", ephemeral=True)
                    return
                
                channel_type, soundboards, whitelist_json, blacklist_json = row
            
            await db.execute(
                'UPDATE temp_channels SET channel_type = ?, soundboards_enabled = ? WHERE channel_id = ?',
                (channel_type, soundboards, self.channel_id)
            )
            
            await db.execute('DELETE FROM channel_whitelist WHERE channel_id = ?', (self.channel_id,))
            await db.execute('DELETE FROM channel_blacklist WHERE channel_id = ?', (self.channel_id,))
            
            whitelist = json.loads(whitelist_json)
            blacklist = json.loads(blacklist_json)
            
            for user_id in whitelist:
                await db.execute(
                    'INSERT INTO channel_whitelist (channel_id, user_id) VALUES (?, ?)',
                    (self.channel_id, int(user_id))
                )
            
            for user_id in blacklist:
                await db.execute(
                    'INSERT INTO channel_blacklist (channel_id, user_id) VALUES (?, ?)',
                    (self.channel_id, int(user_id))
                )
            
            await db.commit()
        
        self.current_type = channel_type
        self.soundboards_enabled = soundboards
        await self.apply_channel_type(channel_type)
        await self.apply_soundboard_permissions()
        await interaction.response.send_message("📋 Paramètres appliqués avec succès !", ephemeral=True)
        await self.refresh_embed()
    
    async def set_channel_type(self, interaction, channel_type):
        async with aiosqlite.connect(self.cog.db_path) as db:
            await db.execute(
                'UPDATE temp_channels SET channel_type = ? WHERE channel_id = ?',
                (channel_type, self.channel_id)
            )
            await db.commit()
        
        self.current_type = channel_type
        await self.apply_channel_type(channel_type)
        
        type_messages = {
            'open': "🌍 Canal configuré en mode **Ouvert** - Accessible à tous",
            'fermé': "🔒 Canal configuré en mode **Fermé** - Visible mais accès restreint",
            'privé': "🔴 Canal configuré en mode **Privé** - Invisible au public"
        }
        
        await interaction.response.send_message(type_messages[channel_type], ephemeral=True)
        await self.refresh_embed()
    
    async def apply_channel_type(self, channel_type):
        channel = self.cog.bot.get_channel(self.channel_id)
        if not channel:
            return
        
        async with aiosqlite.connect(self.cog.db_path) as db:
            async with db.execute(
                'SELECT user_id FROM channel_whitelist WHERE channel_id = ?',
                (self.channel_id,)
            ) as cursor:
                whitelist_ids = [row[0] for row in await cursor.fetchall()]
            
            async with db.execute(
                'SELECT user_id FROM channel_blacklist WHERE channel_id = ?',
                (self.channel_id,)
            ) as cursor:
                blacklist_ids = [row[0] for row in await cursor.fetchall()]
        
        guild = channel.guild
        owner = guild.get_member(self.owner_id)
        
        overwrites = {
            owner: discord.PermissionOverwrite(
                manage_channels=True,
                manage_permissions=True,
                connect=True,
                view_channel=True,
                use_soundboard=True
            )
        }
        
        if channel_type == 'open':
            overwrites[guild.default_role] = discord.PermissionOverwrite(
                connect=True,
                view_channel=True
            )
        elif channel_type == 'fermé':
            overwrites[guild.default_role] = discord.PermissionOverwrite(
                connect=False,
                view_channel=True
            )
        elif channel_type == 'privé':
            overwrites[guild.default_role] = discord.PermissionOverwrite(
                connect=False,
                view_channel=False
            )
        
        for user_id in whitelist_ids:
            member = guild.get_member(user_id)
            if member:
                overwrites[member] = discord.PermissionOverwrite(
                    connect=True,
                    view_channel=True
                )
        
        for user_id in blacklist_ids:
            member = guild.get_member(user_id)
            if member:
                overwrites[member] = discord.PermissionOverwrite(
                    connect=False,
                    view_channel=False
                )
                if member in channel.members:
                    try:
                        await rate_limiter.execute_request(
                            member.move_to(None),
                            route=f'PATCH /guilds/{guild.id}/members/{member.id}',
                            major_params={'guild_id': guild.id}
                        )
                    except discord.HTTPException:
                        pass
        
        await rate_limiter.safe_channel_edit(channel, overwrites=overwrites)
        await self.apply_soundboard_permissions()
    
    async def apply_soundboard_permissions(self):
        channel = self.cog.bot.get_channel(self.channel_id)
        if not channel:
            return
            
        # Apply soundboard permissions based on current setting
        for member in channel.members:
            if member.id == self.owner_id:
                continue
            overwrites = channel.overwrites_for(member)
            if self.soundboards_enabled:
                # Enabled: Set to None (neutral/default permission)
                overwrites.use_soundboard = None
            else:
                # Disabled: Explicitly deny
                overwrites.use_soundboard = False
            await rate_limiter.execute_request(
                channel.set_permissions(member, overwrite=overwrites),
                route=f'PUT /channels/{channel.id}/permissions/{member.id}',
                major_params={'channel_id': channel.id}
            )
    
    async def process_user_list(self, interaction, content, list_type):
        user_ids = []
        parts = content.split()
        
        for part in parts:
            if part.startswith('<@') and part.endswith('>'):
                user_id = part[2:-1]
                if user_id.startswith('!'):
                    user_id = user_id[1:]
                try:
                    user_ids.append(int(user_id))
                except ValueError:
                    continue
            else:
                try:
                    user_ids.append(int(part))
                except ValueError:
                    continue
        
        if not user_ids:
            await interaction.followup.send("❌ Aucun utilisateur valide trouvé.", ephemeral=True)
            return
        
        # Check for mutual exclusion and remove conflicts
        conflicting_users = []
        async with aiosqlite.connect(self.cog.db_path) as db:
            opposite_table = 'channel_blacklist' if list_type == 'whitelist' else 'channel_whitelist'
            
            for user_id in user_ids:
                # Check if user is in the opposite list
                async with db.execute(
                    f'SELECT 1 FROM {opposite_table} WHERE channel_id = ? AND user_id = ?',
                    (self.channel_id, user_id)
                ) as cursor:
                    if await cursor.fetchone():
                        # Remove from opposite list
                        await db.execute(
                            f'DELETE FROM {opposite_table} WHERE channel_id = ? AND user_id = ?',
                            (self.channel_id, user_id)
                        )
                        conflicting_users.append(user_id)
                
                # Add to requested list
                table = f'channel_{list_type}'
                await db.execute(
                    f'INSERT OR IGNORE INTO {table} (channel_id, user_id) VALUES (?, ?)',
                    (self.channel_id, user_id)
                )
            
            await db.commit()
        
        async with aiosqlite.connect(self.cog.db_path) as db:
            async with db.execute(
                'SELECT channel_type FROM temp_channels WHERE channel_id = ?',
                (self.channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    await self.apply_channel_type(row[0])
        
        list_names = {'whitelist': 'liste blanche', 'blacklist': 'liste noire'}
        opposite_names = {'whitelist': 'liste noire', 'blacklist': 'liste blanche'}
        
        response = f"✅ {len(user_ids)} utilisateur(s) ajouté(s) à la {list_names[list_type]}."
        if conflicting_users:
            response += f"\n⚠️ {len(conflicting_users)} utilisateur(s) retiré(s) de la {opposite_names[list_type]} pour éviter les conflits."
        
        await interaction.followup.send(response, ephemeral=True)
        await self.refresh_embed()
    
    async def process_user_removal(self, interaction, content):
        user_ids = []
        parts = content.split()
        
        for part in parts:
            if part.startswith('<@') and part.endswith('>'):
                user_id = part[2:-1]
                if user_id.startswith('!'):
                    user_id = user_id[1:]
                try:
                    user_ids.append(int(user_id))
                except ValueError:
                    continue
            else:
                try:
                    user_ids.append(int(part))
                except ValueError:
                    continue
        
        if not user_ids:
            await interaction.followup.send("❌ Aucun utilisateur valide trouvé.", ephemeral=True)
            return
        
        removed_count = 0
        async with aiosqlite.connect(self.cog.db_path) as db:
            for user_id in user_ids:
                # Remove from both lists
                whitelist_cursor = await db.execute(
                    'DELETE FROM channel_whitelist WHERE channel_id = ? AND user_id = ?',
                    (self.channel_id, user_id)
                )
                blacklist_cursor = await db.execute(
                    'DELETE FROM channel_blacklist WHERE channel_id = ? AND user_id = ?',
                    (self.channel_id, user_id)
                )
                
                if whitelist_cursor.rowcount > 0 or blacklist_cursor.rowcount > 0:
                    removed_count += 1
            
            await db.commit()
        
        # Reapply channel permissions
        async with aiosqlite.connect(self.cog.db_path) as db:
            async with db.execute(
                'SELECT channel_type FROM temp_channels WHERE channel_id = ?',
                (self.channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    await self.apply_channel_type(row[0])
        
        if removed_count > 0:
            await interaction.followup.send(f"✅ {removed_count} utilisateur(s) retiré(s) des listes.", ephemeral=True)
        else:
            await interaction.followup.send("ℹ️ Aucun utilisateur n'a été trouvé dans les listes.", ephemeral=True)
        
        await self.refresh_embed()
    
    async def transfer_ownership_process(self, interaction, content):
        user_id = None
        content = content.strip()
        
        if content.startswith('<@') and content.endswith('>'):
            user_id_str = content[2:-1]
            if user_id_str.startswith('!'):
                user_id_str = user_id_str[1:]
            try:
                user_id = int(user_id_str)
            except ValueError:
                pass
        else:
            try:
                user_id = int(content)
            except ValueError:
                pass
        
        if not user_id:
            await interaction.followup.send("❌ Utilisateur non valide.", ephemeral=True)
            return
        
        guild = interaction.guild
        new_owner = guild.get_member(user_id)
        if not new_owner:
            await interaction.followup.send("❌ Utilisateur introuvable sur le serveur.", ephemeral=True)
            return
        
        async with aiosqlite.connect(self.cog.db_path) as db:
            await db.execute(
                'UPDATE temp_channels SET owner_id = ? WHERE channel_id = ?',
                (user_id, self.channel_id)
            )
            await db.commit()
        
        channel = self.cog.bot.get_channel(self.channel_id)
        if channel:
            old_owner = guild.get_member(self.owner_id)
            
            if old_owner:
                await rate_limiter.execute_request(
                    channel.set_permissions(old_owner, overwrite=None),
                    route=f'PUT /channels/{channel.id}/permissions/{old_owner.id}',
                    major_params={'channel_id': channel.id}
                )
            
            overwrites = discord.PermissionOverwrite(
                manage_channels=True,
                manage_permissions=True,
                connect=True,
                view_channel=True,
                use_soundboard=True
            )
            await rate_limiter.execute_request(
                channel.set_permissions(new_owner, overwrite=overwrites),
                route=f'PUT /channels/{channel.id}/permissions/{new_owner.id}',
                major_params={'channel_id': channel.id}
            )
        
        self.owner_id = user_id
        await interaction.followup.send(f"👑 Propriété transférée avec succès à **{new_owner.display_name}** !", ephemeral=True)
        await self.refresh_embed()
    
    async def refresh_embed(self):
        channel = self.cog.bot.get_channel(self.channel_id)
        if channel:
            # Update button styles before refreshing
            self.update_button_styles()
            await self.cog.send_control_embed(channel, self.owner_id)

class ClaimOwnershipView(discord.ui.View):
    def __init__(self, cog, channel_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.channel_id = channel_id
    
    @discord.ui.button(label="👑 Revendiquer la propriété", style=discord.ButtonStyle.primary)
    async def claim_ownership(self, interaction, button):
        channel = self.cog.bot.get_channel(self.channel_id)
        if not channel or interaction.user not in channel.members:
            await interaction.response.send_message("❌ Vous devez être dans le canal vocal pour le revendiquer.", ephemeral=True)
            return
        
        async with aiosqlite.connect(self.cog.db_path) as db:
            await db.execute(
                'UPDATE temp_channels SET owner_id = ? WHERE channel_id = ?',
                (interaction.user.id, self.channel_id)
            )
            await db.commit()
        
        overwrites = discord.PermissionOverwrite(
            manage_channels=True,
            manage_permissions=True,
            connect=True,
            view_channel=True,
            use_soundboard=True
        )
        await rate_limiter.execute_request(
            channel.set_permissions(interaction.user, overwrite=overwrites),
            route=f'PUT /channels/{channel.id}/permissions/{interaction.user.id}',
            major_params={'channel_id': channel.id}
        )
        
        await interaction.response.send_message(f"🎉 **{interaction.user.display_name}** est maintenant propriétaire du canal !")
        await self.cog.send_control_embed(channel, interaction.user.id)
        self.stop()

async def setup(bot):
    await bot.add_cog(TemporaryChannels(bot))
