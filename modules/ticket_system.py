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
from .rate_limiter import get_rate_limiter, safe_api_call

# Load env from parent directory
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# Configuration
TICKET_CHANNEL_ID = 1377062346425368708
TICKET_CATEGORY_ID = 1345497226528423977
LOGS_CHANNEL_ID = 1345499403942629416

# Role IDs
MODERATOR_ROLE_ID = 1345472840979189851  # Gardien (Moderator)
SEIGNEUR_ROLE_ID = 1345472879168323625  # Seigneur (Admin)
ORACLE_ROLE_ID = 1345472801364246528    # Oracle

# Timezone configuration
PARIS_TZ = pytz.timezone('Europe/Paris')

# Get the rate limiter instance
rate_limiter = get_rate_limiter()

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
                try:
                    await rate_limiter.safe_channel_delete(voice_channel)
                except discord.NotFound:
                    pass  # Already deleted
                except Exception:
                    success = False
        
        # Delete ticket text channel (most important)
        if ticket_id and success:
            ticket_channel = guild.get_channel(ticket_id)
            if ticket_channel:
                try:
                    await rate_limiter.safe_channel_delete(ticket_channel)
                except discord.NotFound:
                    pass  # Already deleted
                except Exception:
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
                'gardien': guild.get_role(MODERATOR_ROLE_ID),
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

            # Create channel with rate limiting
            ticket_channel = await rate_limiter.safe_channel_create(
                guild,
                name=f"ticket-{ticket_number}",
                category=category,
                topic=f"ticket-{interaction.user.id}",
                overwrites=overwrites
            )

            # Build embed and ping text
            paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
            
            embed_data = {
                'Signalement': {
                    'title': "🚨 Nouveau Signalement",
                    'color': discord.Color.red(),
                    'ping': f"<@&{ORACLE_ROLE_ID}> <@&{MODERATOR_ROLE_ID}>"
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
            await rate_limiter.safe_send(ticket_channel, data['ping'], embed=embed, view=view)
            
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
        if not user_roles & {MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
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
            await rate_limiter.safe_channel_edit(interaction.channel, topic=f"{interaction.channel.topic}|taken_charge")
        except Exception as e:
            print(f"Erreur lors de la mise à jour du topic: {e}")

    @discord.ui.button(label='🔊 Créer le vocal', style=discord.ButtonStyle.secondary, custom_id='create_voice')
    async def create_voice(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
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
            
            # Create voice channel with rate limiting
            voice_channel = await rate_limiter.execute_request(
                ticket_channel.guild.create_voice_channel(
                    ticket_channel.name,
                    category=category,
                    overwrites=voice_overwrites
                ),
                route=f'POST /guilds/{ticket_channel.guild.id}/channels',
                major_params={'guild_id': ticket_channel.guild.id}
            )
            
            if voice_channel:
                # Update topic
                await rate_limiter.safe_channel_edit(ticket_channel, topic=f"{ticket_channel.topic}|voice-{voice_channel.id}")
                
                paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
                await rate_limiter.safe_send(ticket_channel, f"🔊 **Salon vocal créé:** {voice_channel.mention}\n📅 **Créé par:** {interaction.user.mention} ({paris_time})")
            else:
                await rate_limiter.safe_send(ticket_channel, "❌ Une erreur s'est produite lors de la création du salon vocal.")
            
        except Exception as e:
            print(f"Erreur lors de la création du vocal: {e}")
            try:
                await rate_limiter.safe_send(ticket_channel, "❌ Une erreur s'est produite lors de la création du salon vocal.")
            except:
                pass

    @discord.ui.button(label='🔒 Clore le ticket', style=discord.ButtonStyle.danger, custom_id='close_ticket')
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
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
                async for message in channel.history(limit=300, oldest_first=True):
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
                await rate_limiter.safe_send(logs_channel, embed=embed)
                
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

async def send_ticket_dm(member: discord.Member, ticket_channel: discord.TextChannel):
    """Send DM notification to user about new ticket"""
    try:
        message = f"Bonjour, un membre du staff souhaite discuter avec vous. Voici le lien vers votre ticket: {ticket_channel.jump_url}"
        await member.send(message)
        return True
    except discord.Forbidden:
        return False
    except discord.HTTPException:
        return False
    except Exception:
        return False

async def create_staff_ticket(guild, member, staff_member, reason=None, notify_user=False):
    category = guild.get_channel(TICKET_CATEGORY_ID)
    
    existing_ticket = discord.utils.get(category.channels, topic=f"ticket-{member.id}")
    if existing_ticket:
        return existing_ticket, False, False

    ticket_number = await get_next_ticket_number()
    
    # Minimal overwrites
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        staff_member: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    
    # Add staff roles
    for role_id in [MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID]:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    # Create channel with rate limiting
    ticket_channel = await rate_limiter.safe_channel_create(
        guild,
        name=f"ticket-{ticket_number}",
        category=category,
        topic=f"ticket-{member.id}",
        overwrites=overwrites
    )

    if not ticket_channel:
        return None, False, False

    paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
    
    embed = discord.Embed(
        title="👤 Entretien Privé avec le Staff",
        description=f"**Initié par :** {staff_member.mention}\n**Concerné :** {member.mention}\n**Date :** {paris_time}\n\n**Motif :**\n```{reason if reason else 'Entretien général demandé par le staff'}```",
        color=discord.Color.purple()
    )
    embed.set_footer(text=f"Ticket #{ticket_number}")

    view = TicketManagementView()
    await rate_limiter.safe_send(ticket_channel, f"{member.mention}", embed=embed, view=view)
    
    # Send DM if requested
    dm_sent = False
    if notify_user:
        dm_sent = await send_ticket_dm(member, ticket_channel)
    
    return ticket_channel, True, dm_sent

async def setup_ticket_system(bot):
    # Initialize databases
    await init_cleanup_db()
    
    bot.add_view(TicketButtons())
    bot.add_view(TicketManagementView())
    
    channel = bot.get_channel(TICKET_CHANNEL_ID)
    if channel:
        # Create embed first
        embed = discord.Embed(
            title="🎫 Support - Les Élémentalistes",
            description="**Besoin d'aide ?**\n\nCréez un ticket afin de contacter le staff. Il existe trois types de tickets selon votre situation :",
            color=discord.Color.blue()
        )
        
        # Signalement section
        signalement_text = "**Pour signaler :**\n- Un membre qui enfreint les règles\n- Du contenu inapproprié sur le serveur\n- Du harcèlement ou de la toxicité\n- Tout comportement suspect"
        embed.add_field(name="🚨 Signalement", value=signalement_text, inline=False)
        
        # Partenariat section
        partenariat_text = "**Pour proposer :**\n- Un partenariat avec Les Élémentalistes\n- Une collaboration lors d'un évent\n- Un échange de publicité"
        embed.add_field(name="🤝 Partenariat", value=partenariat_text, inline=False)
        
        # Contestation section
        contestation_text = "**Pour contester :**\n- Un avertissement/warn reçu\n- Un timeout/mute appliqué\n- Une exclusion du serveur\n- Toute sanction ou comportement staff jugé injuste"
        embed.add_field(name="⚖️ Contestation de Sanction", value=contestation_text, inline=False)
        
        # Important section
        important_text = "- **Un seul ticket à la fois** par personne\n- **Soyez précis** dans votre demande\n- **Restez patient**, le staff vous répondra dès que possible\n- **Soyez respectueux** envers les membres du staff"
        embed.add_field(name="ℹ️ Important", value=important_text, inline=False)
        
        embed.set_footer(text="Les Élémentalistes • Tickets")
        view = TicketButtons()
        
        # Look for existing message
        existing_message = None
        async for message in channel.history(limit=10):
            if message.author == bot.user and message.embeds:
                if "🎫 Support - Les Élémentalistes" in message.embeds[0].title:
                    existing_message = message
                    break
        
        if existing_message:
            try:
                await rate_limiter.safe_edit(existing_message, embed=embed, view=view)
                return
            except:
                await rate_limiter.safe_delete(existing_message)
        
        # Create new message
        await rate_limiter.safe_send(channel, embed=embed, view=view)



class TicketCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cleanup_task.start()
        self.rate_limit_cleanup.start()

    def cog_unload(self):
        self.cleanup_task.cancel()
        self.rate_limit_cleanup.cancel()

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

    @commands.Cog.listener()
    async def on_ready(self):
        await setup_ticket_system(self.bot)

    @commands.command(name='ticket')
    @commands.has_any_role(MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID)
    async def force_ticket(self, ctx, member: discord.Member, notifier: bool = False, *, reason=None):
        ticket_channel, created, dm_sent = await create_staff_ticket(ctx.guild, member, ctx.author, reason, notifier)
        if created:
            dm_status = " (MP envoyé)" if dm_sent else " (MP non envoyé)" if notifier else ""
            await rate_limiter.safe_send(ctx, f"✅ **Ticket créé:** {ticket_channel.mention}{dm_status}")
        else:
            await rate_limiter.safe_send(ctx, f"❌ **Ticket existant:** {ticket_channel.mention}")

    @commands.command(name='ticketadd')
    @commands.has_any_role(MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID)
    async def add_user_to_ticket(self, ctx, member: discord.Member, notifier: bool = False):
        if not ctx.channel.name.startswith('ticket-'):
            await rate_limiter.safe_send(ctx, "❌ **Commande uniquement dans un ticket.**")
            return
        
        await rate_limiter.safe_channel_edit(ctx.channel, overwrites={
            **ctx.channel.overwrites,
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        })
        
        # Update voice channel if exists
        if "|voice-" in ctx.channel.topic:
            try:
                voice_id = int(ctx.channel.topic.split("|voice-")[1].split("|")[0])
                voice_channel = ctx.guild.get_channel(voice_id)
                if voice_channel:
                    await rate_limiter.safe_channel_edit(voice_channel, overwrites={
                        **voice_channel.overwrites,
                        member: discord.PermissionOverwrite(connect=True, speak=True, view_channel=True)
                    })
            except:
                pass
        
        # Send DM if requested
        dm_status = ""
        if notifier:
            dm_sent = await send_ticket_dm(member, ctx.channel)
            dm_status = " (MP envoyé)" if dm_sent else " (MP non envoyé)"
        
        await rate_limiter.safe_send(ctx, f"✅ **{member.mention} ajouté au ticket**{dm_status}")

    @commands.command(name='ticketremove')
    @commands.has_any_role(MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID)
    async def remove_user_from_ticket(self, ctx, member: discord.Member):
        if not ctx.channel.name.startswith('ticket-'):
            await rate_limiter.safe_send(ctx, "❌ **Commande uniquement dans un ticket.**")
            return
        
        ticket_owner_id = int(ctx.channel.topic.split("|")[0].replace('ticket-', ''))
        if member.id == ticket_owner_id:
            await rate_limiter.safe_send(ctx, "❌ **Impossible de retirer le propriétaire.**")
            return
        
        new_overwrites = ctx.channel.overwrites.copy()
        if member in new_overwrites:
            del new_overwrites[member]
        
        await rate_limiter.safe_channel_edit(ctx.channel, overwrites=new_overwrites)
        
        if "|voice-" in ctx.channel.topic:
            try:
                voice_id = int(ctx.channel.topic.split("|voice-")[1].split("|")[0])
                voice_channel = ctx.guild.get_channel(voice_id)
                if voice_channel:
                    voice_overwrites = voice_channel.overwrites.copy()
                    if member in voice_overwrites:
                        del voice_overwrites[member]
                    await rate_limiter.safe_channel_edit(voice_channel, overwrites=voice_overwrites)
            except:
                pass
        
        await rate_limiter.safe_send(ctx, f"✅ **{member.mention} retiré du ticket**")

    @commands.command(name='rate_limit_stats')
    @commands.has_any_role(MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID)
    async def rate_limit_stats(self, ctx):
        """Check rate limiter statistics"""
        try:
            metrics = rate_limiter.get_metrics()
            embed = discord.Embed(
                title="📊 Statistiques Rate Limiter",
                color=discord.Color.blue()
            )
            embed.add_field(name="Requêtes totales", value=metrics['total_requests'], inline=True)
            embed.add_field(name="Rate limited", value=f"{metrics['rate_limited_requests']} ({metrics['rate_limit_percentage']}%)", inline=True)
            embed.add_field(name="Échecs", value=metrics['failed_requests'], inline=True)
            embed.add_field(name="Tentatives retry", value=metrics['retry_attempts'], inline=True)
            embed.add_field(name="Req/min moyenne", value=metrics['requests_per_minute'], inline=True)
            embed.add_field(name="Buckets actifs", value=metrics['active_buckets'], inline=True)
            embed.add_field(name="Temps moyen", value=f"{metrics['average_request_time']}s", inline=True)
            embed.add_field(name="Global rate limited", value="✅" if metrics['global_rate_limited'] else "❌", inline=True)
            
            await rate_limiter.safe_send(ctx, embed=embed)
        except Exception as e:
            await rate_limiter.safe_send(ctx, f"❌ Erreur lors de la récupération des stats: {e}")

    @commands.command(name='cleanup_status')
    @commands.has_any_role(MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID)
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
            await rate_limiter.safe_send(ctx, embed=embed)
        except Exception as e:
            await rate_limiter.safe_send(ctx, f"❌ Erreur lors de la vérification: {e}")

    @discord.app_commands.command(name="ticket", description="Créer un ticket d'entretien")
    @discord.app_commands.describe(
        member="Utilisateur concerné",
        reason="Motif du ticket",
        notifier="Envoyer un MP à l'utilisateur"
    )
    async def slash_ticket(self, interaction: discord.Interaction, member: discord.Member, reason: str = None, notifier: bool = False):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
            await interaction.response.send_message("❌ Permissions insuffisantes.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
            
        try:
            ticket_channel, created, dm_sent = await create_staff_ticket(interaction.guild, member, interaction.user, reason, notifier)
            if created:
                dm_status = " (MP envoyé)" if dm_sent else " (MP non envoyé)" if notifier else ""
                await interaction.followup.send(f"✅ **Ticket créé:** {ticket_channel.mention}{dm_status}")
            else:
                await interaction.followup.send(f"❌ **Ticket existant:** {ticket_channel.mention}")
        except Exception as e:
            try:
                await interaction.followup.send("❌ Erreur lors de la création du ticket.")
            except:
                pass

    @discord.app_commands.command(name="ticketadd", description="Ajouter un utilisateur au ticket")
    @discord.app_commands.describe(
        member="Utilisateur à ajouter",
        notifier="Envoyer un MP à l'utilisateur"
    )
    async def slash_add_user(self, interaction: discord.Interaction, member: discord.Member, notifier: bool = False):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
            await interaction.response.send_message("❌ Permissions insuffisantes.", ephemeral=True)
            return

        if not interaction.channel.name.startswith('ticket-'):
            await interaction.response.send_message("❌ Commande uniquement dans un ticket.", ephemeral=True)
            return
        
        await rate_limiter.safe_channel_edit(interaction.channel, overwrites={
            **interaction.channel.overwrites,
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        })
        
        # Send DM if requested
        dm_status = ""
        if notifier:
            dm_sent = await send_ticket_dm(member, interaction.channel)
            dm_status = " (MP envoyé)" if dm_sent else " (MP non envoyé)"
        
        await interaction.response.send_message(f"✅ **{member.mention} ajouté au ticket**{dm_status}")

    @discord.app_commands.command(name="ticketremove", description="Retirer un utilisateur du ticket")
    async def slash_remove_user(self, interaction: discord.Interaction, member: discord.Member):
        user_roles = {role.id for role in interaction.user.roles}
        if not user_roles & {MODERATOR_ROLE_ID, SEIGNEUR_ROLE_ID, ORACLE_ROLE_ID}:
            await interaction.response.send_message("❌ Permissions insuffisantes.", ephemeral=True)
            return

        if not interaction.channel.name.startswith('ticket-'):
            await interaction.response.send_message("❌ Commande uniquement dans un ticket.", ephemeral=True)
            return
        
        ticket_owner_id = int(interaction.channel.topic.split("|")[0].replace('ticket-', ''))
        if member.id == ticket_owner_id:
            await interaction.response.send_message("❌ Impossible de retirer le propriétaire.", ephemeral=True)
            return
        
        new_overwrites = interaction.channel.overwrites.copy()
        if member in new_overwrites:
            del new_overwrites[member]
        
        await rate_limiter.safe_channel_edit(interaction.channel, overwrites=new_overwrites)
        await interaction.response.send_message(f"✅ **{member.mention} retiré du ticket**")

async def setup(bot):
    await bot.add_cog(TicketCog(bot))
