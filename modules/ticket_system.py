# modules/ticket_system.py
import discord
from discord.ext import commands, tasks
import aiosqlite
import os
from dotenv import load_dotenv
import asyncio
import pytz
from datetime import datetime, timedelta
from typing import Dict, Set, Tuple, Optional, List

# Load env from parent directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Configuration
TICKET_CHANNEL_ID = 1377062346425368708
TICKET_CATEGORY_ID = 1345497226528423977
LOGS_CHANNEL_ID = 1345499403942629416

# Role IDs
GARDIEN_ROLE_ID = 1345472840979189851  # Gardien (Moderator)
SEIGNEUR_ROLE_ID = 1345472879168323625  # Seigneur (Admin)
ORACLE_ROLE_ID = 1345472801364246528    # Oracle

# Timezone configuration
PARIS_TZ = pytz.timezone('Europe/Paris')

# Rate limiting configuration
MAX_HISTORY_LIMIT = 300
API_DELAY = 0.7
MAX_RETRIES = 3
RETRY_DELAY = 5

async def safe_api_call(coro, delay=API_DELAY, max_retries=MAX_RETRIES):
    """Enhanced wrapper for API calls with comprehensive rate limit handling"""
    for attempt in range(max_retries):
        try:
            await asyncio.sleep(delay)
            return await coro
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limited
                retry_after = getattr(e, 'retry_after', 5)
                wait_time = retry_after + 1 + (attempt * 2)  # Exponential backoff
                print(f"Rate limited, waiting {wait_time}s (attempt {attempt + 1})")
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise
            elif e.status == 404:  # Not found - channel already deleted
                return None
            else:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
    return None

async def init_cleanup_db():
    """Initialize cleanup tracking database"""
    db_path = os.path.join(os.path.dirname(__file__), '..', 'cleanup.db')
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pending_cleanup (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_channel_id INTEGER,
                voice_channel_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                retry_count INTEGER DEFAULT 0,
                last_attempt TIMESTAMP,
                completed BOOLEAN DEFAULT FALSE
            )
        """)
        await db.commit()

async def schedule_cleanup(ticket_channel_id: int, voice_channel_id: int = None):
    """Schedule channels for cleanup"""
    db_path = os.path.join(os.path.dirname(__file__), '..', 'cleanup.db')
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            INSERT INTO pending_cleanup 
            (ticket_channel_id, voice_channel_id) 
            VALUES (?, ?)
        """, (ticket_channel_id, voice_channel_id))
        await db.commit()

async def mark_cleanup_completed(cleanup_id: int):
    """Mark cleanup as completed"""
    db_path = os.path.join(os.path.dirname(__file__), '..', 'cleanup.db')
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE pending_cleanup SET completed = TRUE WHERE id = ?",
            (cleanup_id,)
        )
        await db.commit()

async def update_cleanup_attempt(cleanup_id: int):
    """Update cleanup attempt counter"""
    db_path = os.path.join(os.path.dirname(__file__), '..', 'cleanup.db')
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            UPDATE pending_cleanup 
            SET retry_count = retry_count + 1, last_attempt = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (cleanup_id,))
        await db.commit()

async def get_pending_cleanups():
    """Get pending cleanups that need retry"""
    db_path = os.path.join(os.path.dirname(__file__), '..', 'cleanup.db')
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("""
            SELECT id, ticket_channel_id, voice_channel_id, retry_count
            FROM pending_cleanup 
            WHERE completed = FALSE 
            AND retry_count < 10
            AND (last_attempt IS NULL OR last_attempt < datetime('now', '-10 minutes'))
            ORDER BY created_at ASC
            LIMIT 5
        """)
        return await cursor.fetchall()

async def cleanup_old_records():
    """Clean up old completed records"""
    db_path = os.path.join(os.path.dirname(__file__), '..', 'cleanup.db')
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            DELETE FROM pending_cleanup 
            WHERE completed = TRUE 
            AND created_at < datetime('now', '-7 days')
        """)
        await db.execute("""
            DELETE FROM pending_cleanup 
            WHERE retry_count >= 10 
            AND created_at < datetime('now', '-1 day')
        """)
        await db.commit()

async def perform_cleanup(guild, cleanup_record):
    """Perform actual cleanup of channels"""
    cleanup_id, ticket_id, voice_id, retry_count = cleanup_record
    success = True
    
    try:
        await update_cleanup_attempt(cleanup_id)
        
        # Delete voice channel first (less important)
        if voice_id:
            voice_channel = guild.get_channel(voice_id)
            if voice_channel:
                result = await safe_api_call(voice_channel.delete(), delay=1.0)
                if result is None:
                    # Check if channel still exists after deletion attempt
                    voice_channel = guild.get_channel(voice_id)
                    if voice_channel:  # Still exists, deletion failed
                        success = False
        
        # Delete ticket text channel (most important)
        if ticket_id and success:
            ticket_channel = guild.get_channel(ticket_id)
            if ticket_channel:
                result = await safe_api_call(ticket_channel.delete(), delay=1.0)
                if result is None:
                    # Check if channel still exists after deletion attempt
                    ticket_channel = guild.get_channel(ticket_id)
                    if ticket_channel:  # Still exists, deletion failed
                        success = False
        
        if success:
            await mark_cleanup_completed(cleanup_id)
            return True
            
    except Exception as e:
        print(f"Cleanup failed for record {cleanup_id}: {e}")
        success = False
    
    return success

class TicketReasonModal(discord.ui.Modal):
    def __init__(self, ticket_type: str, view_instance):
        super().__init__(title=f"Raison du ticket - {ticket_type}")
        self.ticket_type = ticket_type
        self.view_instance = view_instance
        
        self.reason_input = discord.ui.TextInput(
            label="Expliquez votre demande en détail",
            placeholder="Décrivez précisément votre situation, votre demande ou le problème rencontré...",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason_input.value
        await self.view_instance.create_ticket_with_reason(interaction, self.ticket_type, reason)

class TicketButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='🚨 Signalement', style=discord.ButtonStyle.red, custom_id='ticket_report')
    async def report_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TicketReasonModal('Signalement', self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label='🤝 Partenariat', style=discord.ButtonStyle.green, custom_id='ticket_partnership')
    async def partnership_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TicketReasonModal('Partenariat', self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label='⚖️ Contestation', style=discord.ButtonStyle.blurple, custom_id='ticket_appeal')
    async def appeal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TicketReasonModal('Contestation de sanction', self)
        await interaction.response.send_modal(modal)

    async def create_ticket_with_reason(self, interaction: discord.Interaction, ticket_type: str, reason: str):
        guild = interaction.guild
        category = guild.get_channel(TICKET_CATEGORY_ID)
        
        # Check existing ticket first
        existing_ticket = discord.utils.get(category.channels, topic=f"ticket-{interaction.user.id}")
        if existing_ticket:
            await interaction.response.send_message("❌ **Vous avez déjà un ticket ouvert !**\n\nVeuillez finaliser votre ticket actuel avant d'en créer un nouveau.", ephemeral=True)
            return

        # Defer response immediately
        await interaction.response.defer(ephemeral=True)

        try:
            ticket_number = await get_next_ticket_number()
            
            # Get roles once and cache them
            roles = {
                'oracle': guild.get_role(ORACLE_ROLE_ID),
                'gardien': guild.get_role(GARDIEN_ROLE_ID),
                'seigneur': guild.get_role(SEIGNEUR_ROLE_ID)
            }
            
            # Build minimal overwrites based on ticket type
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            
            # Add staff permissions based on ticket type
            if ticket_type == 'Signalement':
                for role in roles.values():
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            else:  # Partenariat or Contestation - only seigneur
                if roles['seigneur']:
                    overwrites[roles['seigneur']] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            # Create channel
            ticket_channel = await safe_api_call(category.create_text_channel(
                name=f"ticket-{ticket_number}",
                topic=f"ticket-{interaction.user.id}",
                overwrites=overwrites
            ))

            # Build embed and ping text
            paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
            
            embed_data = {
                'Signalement': {
                    'title': "🚨 Nouveau Signalement",
                    'color': discord.Color.red(),
                    'ping': f"<@&{ORACLE_ROLE_ID}> <@&{GARDIEN_ROLE_ID}>"
                },
                'Partenariat': {
                    'title': "🤝 Demande de Partenariat",
                    'color': discord.Color.green(),
                    'ping': f"<@&{SEIGNEUR_ROLE_ID}>"
                },
                'Contestation de sanction': {
                    'title': "⚖️ Contestation de Sanction",
                    'color': discord.Color.blurple(),
                    'ping': f"<@&{SEIGNEUR_ROLE_ID}>"
                }
            }
            
            data = embed_data[ticket_type]
            embed = discord.Embed(
                title=data['title'],
                description=f"**Utilisateur :** {interaction.user.mention}\n**Date :** {paris_time}\n**Type :** {ticket_type}\n\n**Détails :**\n```{reason}```",
                color=data['color']
            )
            embed.set_footer(text=f"Ticket #{ticket_number} • {guild.name}")

            view = TicketManagementView()
            await safe_api_call(ticket_channel.send(data['ping'], embed=embed, view=view))
            
            await interaction.followup.send(f"✅ **Votre ticket a été créé avec succès !**\n\n📍 **Lien :** {ticket_channel.mention}\n🎫 **Numéro :** #{ticket_number}\n\nUn membre du staff vous contactera sous peu.", ephemeral=True)

        except Exception as e:
            print(f"Erreur lors de la création du ticket: {e}")
            try:
                await interaction.followup.send("❌ **Une erreur s'est produite lors de la création du ticket.**\n\nVeuillez réessayer ou contacter un administrateur.", ephemeral=True)
            except:
                pass

class TicketManagementView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='✋ Prendre en charge', style=discord.ButtonStyle.secondary, custom_id='take_charge')
    async def take_charge(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
            await interaction.response.send_message("❌ Vous n'avez pas les permissions nécessaires.", ephemeral=True)
            return

        if "|taken_charge" in interaction.channel.topic:
            await interaction.response.send_message("❌ Ce ticket a déjà été pris en charge.", ephemeral=True)
            return

        # Respond immediately
        paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
        await interaction.response.send_message(f"✅ **{interaction.user.mention} prend ce ticket en charge** ({paris_time})")
        
        # Update topic in background
        try:
            await safe_api_call(interaction.channel.edit(topic=f"{interaction.channel.topic}|taken_charge"))
        except Exception as e:
            print(f"Erreur lors de la mise à jour du topic: {e}")

    @discord.ui.button(label='🔊 Créer le vocal', style=discord.ButtonStyle.secondary, custom_id='create_voice')
    async def create_voice(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
            await interaction.response.send_message("❌ Vous n'avez pas les permissions nécessaires.", ephemeral=True)
            return

        if "|voice-" in interaction.channel.topic:
            await interaction.response.send_message("❌ Un salon vocal a déjà été créé pour ce ticket.", ephemeral=True)
            return

        # Respond immediately
        await interaction.response.send_message("🔊 **Création du salon vocal en cours...**")
        
        # Do the work in background
        asyncio.create_task(self._create_voice_channel(interaction))

    async def _create_voice_channel(self, interaction: discord.Interaction):
        """Background task to create voice channel"""
        try:
            ticket_channel = interaction.channel
            category = ticket_channel.category
            
            # Simplified voice overwrites
            voice_overwrites = {
                ticket_channel.guild.default_role: discord.PermissionOverwrite(view_channel=False)
            }
            
            # Copy permissions from text channel
            for target, overwrite in ticket_channel.overwrites.items():
                if overwrite.read_messages:
                    voice_overwrites[target] = discord.PermissionOverwrite(
                        connect=True, speak=True, view_channel=True
                    )
            
            # Create voice channel
            voice_channel = await safe_api_call(category.create_voice_channel(
                name=ticket_channel.name,
                overwrites=voice_overwrites
            ))
            
            if voice_channel:
                # Update topic
                await safe_api_call(ticket_channel.edit(topic=f"{ticket_channel.topic}|voice-{voice_channel.id}"))
                
                paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
                await safe_api_call(ticket_channel.send(f"🔊 **Salon vocal créé:** {voice_channel.mention}\n📅 **Créé par:** {interaction.user.mention} ({paris_time})"))
            else:
                await safe_api_call(ticket_channel.send("❌ Une erreur s'est produite lors de la création du salon vocal."))
            
        except Exception as e:
            print(f"Erreur lors de la création du vocal: {e}")
            try:
                await safe_api_call(ticket_channel.send("❌ Une erreur s'est produite lors de la création du salon vocal."))
            except:
                pass

    @discord.ui.button(label='🔒 Clore le ticket', style=discord.ButtonStyle.danger, custom_id='close_ticket')
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
            await interaction.response.send_message("❌ Vous n'avez pas les permissions nécessaires.", ephemeral=True)
            return

        # Respond immediately
        paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
        await interaction.response.send_message(f"🔒 **Fermeture du ticket dans 10 secondes...**\n\n📝 Les logs seront sauvegardés automatiquement.\n⏰ Fermé par {interaction.user.mention} le {paris_time}")
        
        # Get associated voice channel info
        voice_id = None
        
        if "|voice-" in interaction.channel.topic:
            try:
                voice_id = int(interaction.channel.topic.split("|voice-")[1].split("|")[0])
            except:
                pass

        # Schedule cleanup and do initial save
        await schedule_cleanup(interaction.channel.id, voice_id)
        
        # Do initial cleanup attempt in background
        asyncio.create_task(self._save_logs_and_cleanup(interaction.channel, interaction.user, voice_id))

    async def _save_logs_and_cleanup(self, channel, closer, voice_id):
        """Background task to save logs and attempt cleanup"""
        # Save logs first (most important)
        logs_channel = channel.guild.get_channel(LOGS_CHANNEL_ID)
        if logs_channel:
            try:
                # Fetch limited messages
                messages = []
                async for message in channel.history(limit=MAX_HISTORY_LIMIT, oldest_first=True):
                    paris_timestamp = message.created_at.astimezone(PARIS_TZ).strftime("%d/%m/%Y %H:%M:%S")
                    content = message.content if message.content else "[Embed/Attachment]"
                    messages.append(f"[{paris_timestamp}] {message.author}: {content[:100]}...")
                
                # Single log message
                log_content = "\n".join(messages)
                if len(log_content) > 4000:
                    log_content = log_content[:4000] + "...\n[Log tronqué]"
                
                embed = discord.Embed(
                    title=f"📝 Logs du ticket {channel.name}",
                    description=f"```{log_content}```",
                    color=discord.Color.blue(),
                    timestamp=datetime.now(PARIS_TZ)
                )
                embed.set_footer(text=f"Fermé par {closer}", icon_url=closer.display_avatar.url)
                await safe_api_call(logs_channel.send(embed=embed))
                
            except Exception as e:
                print(f"Erreur lors de la sauvegarde des logs: {e}")

        # Wait then attempt cleanup (the background task will retry if it fails)
        await asyncio.sleep(10)
        
        # Attempt immediate cleanup - if it fails, the scheduled cleanup will retry
        cleanup_record = (None, channel.id, voice_id, 0)
        success = await perform_cleanup(channel.guild, cleanup_record)
        
        if not success:
            print(f"Initial cleanup failed for ticket {channel.id}, will retry via background task")

async def get_next_ticket_number():
    db_path = os.path.join(os.path.dirname(__file__), '..', 'tickets.db')
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ticket_counter (
                id INTEGER PRIMARY KEY,
                counter INTEGER DEFAULT 0
            )
        """)
        
        await db.execute("""
            INSERT INTO ticket_counter (id, counter) VALUES (1, 1)
            ON CONFLICT(id) DO UPDATE SET counter = counter + 1
        """)
        await db.commit()
        
        cursor = await db.execute("SELECT counter FROM ticket_counter WHERE id = 1")
        result = await cursor.fetchone()
        return result[0] if result else 1

async def create_staff_ticket(guild, member, staff_member, reason=None):
    category = guild.get_channel(TICKET_CATEGORY_ID)
    
    existing_ticket = discord.utils.get(category.channels, topic=f"ticket-{member.id}")
    if existing_ticket:
        return existing_ticket, False

    ticket_number = await get_next_ticket_number()
    
    # Minimal overwrites
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        staff_member: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    
    # Add staff roles
    for role_id in [GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID]:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    # Create channel
    ticket_channel = await safe_api_call(category.create_text_channel(
        name=f"ticket-{ticket_number}",
        topic=f"ticket-{member.id}",
        overwrites=overwrites
    ))

    if not ticket_channel:
        return None, False

    paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
    
    embed = discord.Embed(
        title="👤 Entretien Privé avec le Staff",
        description=f"**Initié par :** {staff_member.mention}\n**Concerné :** {member.mention}\n**Date :** {paris_time}\n\n**Motif :**\n```{reason if reason else 'Entretien général demandé par le staff'}```",
        color=discord.Color.purple()
    )
    embed.set_footer(text=f"Ticket #{ticket_number}")

    view = TicketManagementView()
    await safe_api_call(ticket_channel.send(f"{member.mention}", embed=embed, view=view))
    return ticket_channel, True

async def setup_ticket_system(bot):
    # Initialize databases
    await init_cleanup_db()
    
    bot.add_view(TicketButtons())
    bot.add_view(TicketManagementView())
    
    channel = bot.get_channel(TICKET_CHANNEL_ID)
    if channel:
        # Look for existing message
        existing_message = None
        async for message in channel.history(limit=10):
            if message.author == bot.user and message.embeds:
                if "Système de Tickets" in message.embeds[0].title:
                    existing_message = message
                    break
        
        if existing_message:
            try:
                view = TicketButtons()
                await safe_api_call(existing_message.edit(view=view))
                return
            except:
                await safe_api_call(existing_message.delete())
        
        # Create new message
        embed = discord.Embed(
            title="🎫 Système de Tickets - Les Élémentalistes",
            description="**Besoin d'aide ou avez-vous une demande spécifique ?**\n\nUtilisez notre système de tickets pour contacter le staff de manière privée et organisée.",
            color=discord.Color.blue()
        )
        embed.add_field(name="🚨 Signalement", value="Pour signaler un utilisateur ou comportement", inline=True)
        embed.add_field(name="🤝 Partenariat", value="Pour proposer un partenariat", inline=True)
        embed.add_field(name="⚖️ Contestation", value="Pour contester une sanction", inline=True)
        
        view = TicketButtons()
        await safe_api_call(channel.send(embed=embed, view=view))

class TicketCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cleanup_task.start()

    def cog_unload(self):
        self.cleanup_task.cancel()

    @tasks.loop(minutes=10)
    async def cleanup_task(self):
        """Background task to retry failed cleanups"""
        try:
            await cleanup_old_records()
            pending_cleanups = await get_pending_cleanups()
            
            for cleanup_record in pending_cleanups:
                # Get guild (assuming single guild bot)
                guild = self.bot.get_guild(int(os.getenv('GUILD_ID')))
                if guild:
                    success = await perform_cleanup(guild, cleanup_record)
                    if success:
                        print(f"Successfully cleaned up channels for record {cleanup_record[0]}")
                    else:
                        print(f"Cleanup retry failed for record {cleanup_record[0]}")
                
                # Rate limit between cleanup attempts
                await asyncio.sleep(2)
                
        except Exception as e:
            print(f"Error in cleanup task: {e}")

    @cleanup_task.before_loop
    async def before_cleanup_task(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        await setup_ticket_system(self.bot)

    @commands.command(name='ticket')
    @commands.has_any_role(GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID)
    async def force_ticket(self, ctx, member: discord.Member, *, reason=None):
        ticket_channel, created = await create_staff_ticket(ctx.guild, member, ctx.author, reason)
        if created:
            await ctx.send(f"✅ **Ticket créé:** {ticket_channel.mention}")
        else:
            await ctx.send(f"❌ **Ticket existant:** {ticket_channel.mention}")

    @commands.command(name='ticketadd')
    @commands.has_any_role(GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID)
    async def add_user_to_ticket(self, ctx, member: discord.Member):
        if not ctx.channel.name.startswith('ticket-'):
            await ctx.send("❌ **Commande uniquement dans un ticket.**")
            return
        
        await safe_api_call(ctx.channel.set_permissions(member, 
            overwrite=discord.PermissionOverwrite(read_messages=True, send_messages=True)))
        
        # Update voice channel if exists
        if "|voice-" in ctx.channel.topic:
            try:
                voice_id = int(ctx.channel.topic.split("|voice-")[1].split("|")[0])
                voice_channel = ctx.guild.get_channel(voice_id)
                if voice_channel:
                    await safe_api_call(voice_channel.set_permissions(member,
                        overwrite=discord.PermissionOverwrite(connect=True, speak=True, view_channel=True)))
            except:
                pass
        
        await ctx.send(f"✅ **{member.mention} ajouté au ticket**")

    @commands.command(name='ticketremove')
    @commands.has_any_role(GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID)
    async def remove_user_from_ticket(self, ctx, member: discord.Member):
        if not ctx.channel.name.startswith('ticket-'):
            await ctx.send("❌ **Commande uniquement dans un ticket.**")
            return
        
        ticket_owner_id = int(ctx.channel.topic.split("|")[0].replace('ticket-', ''))
        if member.id == ticket_owner_id:
            await ctx.send("❌ **Impossible de retirer le propriétaire.**")
            return
        
        await safe_api_call(ctx.channel.set_permissions(member, overwrite=None))
        
        if "|voice-" in ctx.channel.topic:
            try:
                voice_id = int(ctx.channel.topic.split("|voice-")[1].split("|")[0])
                voice_channel = ctx.guild.get_channel(voice_id)
                if voice_channel:
                    await safe_api_call(voice_channel.set_permissions(member, overwrite=None))
            except:
                pass
        
        await ctx.send(f"✅ **{member.mention} retiré du ticket**")

    @commands.command(name='cleanup_status')
    @commands.has_any_role(GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID)
    async def cleanup_status(self, ctx):
        """Check cleanup status"""
        try:
            pending_cleanups = await get_pending_cleanups()
            if pending_cleanups:
                embed = discord.Embed(
                    title="🧹 État du Nettoyage",
                    description=f"**{len(pending_cleanups)} nettoyages en attente**",
                    color=discord.Color.orange()
                )
                for cleanup in pending_cleanups[:5]:
                    embed.add_field(
                        name=f"Ticket {cleanup[1]}", 
                        value=f"Tentatives: {cleanup[3]}", 
                        inline=True
                    )
            else:
                embed = discord.Embed(
                    title="🧹 État du Nettoyage",
                    description="✅ **Aucun nettoyage en attente**",
                    color=discord.Color.green()
                )
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"❌ Erreur lors de la vérification: {e}")

    @discord.app_commands.command(name="ticket", description="Créer un ticket d'entretien")
    async def slash_ticket(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
            await interaction.response.send_message("❌ Permissions insuffisantes.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
            
        try:
            ticket_channel, created = await create_staff_ticket(interaction.guild, member, interaction.user, reason)
            if created:
                await interaction.followup.send(f"✅ **Ticket créé:** {ticket_channel.mention}")
            else:
                await interaction.followup.send(f"❌ **Ticket existant:** {ticket_channel.mention}")
        except Exception as e:
            try:
                await interaction.followup.send("❌ Erreur lors de la création du ticket.")
            except:
                pass

    @discord.app_commands.command(name="ticketadd", description="Ajouter un utilisateur au ticket")
    async def slash_add_user(self, interaction: discord.Interaction, member: discord.Member):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
            await interaction.response.send_message("❌ Permissions insuffisantes.", ephemeral=True)
            return

        if not interaction.channel.name.startswith('ticket-'):
            await interaction.response.send_message("❌ Commande uniquement dans un ticket.", ephemeral=True)
            return
        
        await safe_api_call(interaction.channel.set_permissions(member, 
            overwrite=discord.PermissionOverwrite(read_messages=True, send_messages=True)))
        
        await interaction.response.send_message(f"✅ **{member.mention} ajouté au ticket**")

    @discord.app_commands.command(name="ticketremove", description="Retirer un utilisateur du ticket")
    async def slash_remove_user(self, interaction: discord.Interaction, member: discord.Member):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {GARDIEN_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
            await interaction.response.send_message("❌ Permissions insuffisantes.", ephemeral=True)
            return

        if not interaction.channel.name.startswith('ticket-'):
            await interaction.response.send_message("❌ Commande uniquement dans un ticket.", ephemeral=True)
            return
        
        ticket_owner_id = int(interaction.channel.topic.split("|")[0].replace('ticket-', ''))
        if member.id == ticket_owner_id:
            await interaction.response.send_message("❌ Impossible de retirer le propriétaire.", ephemeral=True)
            return
        
        await safe_api_call(interaction.channel.set_permissions(member, overwrite=None))
        await interaction.response.send_message(f"✅ **{member.mention} retiré du ticket**")

async def setup(bot):
    await bot.add_cog(TicketCog(bot))
