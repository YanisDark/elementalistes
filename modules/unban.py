# modules/unban.py
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
GUILD_ID = int(os.getenv('GUILD_ID'))
UNBAN_GUILD_ID = int(os.getenv('UNBAN_GUILD_ID'))
UNBAN_CHANNEL_TICKET_ID = int(os.getenv('UNBAN_CHANNEL_TICKET_ID', '0'))
CATEGORY_UNBAN = int(os.getenv('CATEGORY_UNBAN', '0'))
MODERATOR_ROLE_ID = int(os.getenv('MODERATOR_ROLE_ID'))
ORACLE_ROLE_ID = int(os.getenv('ORACLE_ROLE_ID'))
STAFF_ROLE_UNBAN = int(os.getenv('STAFF_ROLE_UNBAN'))
ORACLE_ROLE_UNBAN = int(os.getenv('ORACLE_ROLE_UNBAN'))

# Timezone configuration
PARIS_TZ = pytz.timezone('Europe/Paris')

# Get the rate limiter instance
rate_limiter = get_rate_limiter()

async def init_unban_db():
    """Initialize unban tracking database"""
    db_path = os.path.join(os.path.dirname(__file__), '..', 'unban.db')
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS unban_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                staff_id INTEGER,
                reason TEXT
            )
        """)
        await db.commit()

async def get_next_unban_ticket_number():
    db_path = os.path.join(os.path.dirname(__file__), '..', 'unban.db')
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS unban_counter (
                id INTEGER PRIMARY KEY,
                counter INTEGER DEFAULT 0
            )
        """)
        
        await db.execute("""
            INSERT INTO unban_counter (id, counter) VALUES (1, 1)
            ON CONFLICT(id) DO UPDATE SET counter = counter + 1
        """)
        await db.commit()
        
        cursor = await db.execute("SELECT counter FROM unban_counter WHERE id = 1")
        result = await cursor.fetchone()
        return result[0] if result else 1

async def log_unban_ticket(user_id: int, channel_id: int, status: str, staff_id: int = None, reason: str = None):
    """Log unban ticket activity"""
    db_path = os.path.join(os.path.dirname(__file__), '..', 'unban.db')
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            INSERT INTO unban_tickets (user_id, channel_id, status, staff_id, reason)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, channel_id, status, staff_id, reason))
        await db.commit()

async def check_user_banned(guild: discord.Guild, user_id: int) -> bool:
    """Check if user is banned from main guild"""
    try:
        ban = await guild.fetch_ban(discord.Object(user_id))
        return True
    except discord.NotFound:
        return False
    except discord.Forbidden:
        return False
    except Exception:
        return False

async def check_user_permissions(main_guild: discord.Guild, user_id: int) -> tuple[bool, bool]:
    """Check if user has mod permissions or oracle role in main guild"""
    try:
        main_member = main_guild.get_member(user_id)
        if not main_member:
            return False, False
        
        has_mod_perms = False
        has_oracle_role = False
        
        # Check for moderator role
        if MODERATOR_ROLE_ID in [role.id for role in main_member.roles]:
            has_mod_perms = True
        
        # Check for oracle role
        if ORACLE_ROLE_ID in [role.id for role in main_member.roles]:
            has_oracle_role = True
        
        # Check for ban permissions
        if main_member.guild_permissions.ban_members:
            has_mod_perms = True
            
        return has_mod_perms, has_oracle_role
    except Exception:
        return False, False

async def verify_and_remove_user(member: discord.Member, main_guild: discord.Guild, reason: str, is_ban: bool = False):
    """Verify user should be removed and send appropriate message before removal"""
    try:
        # Double check permissions one more time
        has_mod_perms, has_oracle_role = await check_user_permissions(main_guild, member.id)
        
        # If they have any staff permissions, don't remove them
        if has_mod_perms or has_oracle_role:
            print(f"Annulation de la suppression de {member} - permissions staff détectées")
            return False
        
        # Double check ban status if it's not a rejection
        if not is_ban:
            is_banned = await check_user_banned(main_guild, member.id)
            if is_banned:
                print(f"Annulation du kick de {member} - toujours banni")
                return False
        
        # Create appropriate message
        if is_ban:
            message = f"""🚫 **Serveur d'Appel Les Élémentalistes**

Votre demande d'unban a été **rejetée** par l'équipe de modération.

**Raison :** {reason}

Vous avez été banni du serveur d'appel et ne pourrez plus faire de nouvelles demandes d'unban. Cette décision est définitive.

Si vous pensez qu'il y a eu une erreur, vous pouvez contacter directement un administrateur du serveur principal."""
        else:
            message = f"""ℹ️ **Serveur d'Appel Les Élémentalistes**

Vous avez été retiré du serveur d'appel.

**Raison :** {reason}

Ceci n'est pas une sanction, mais simplement car votre présence sur ce serveur n'est plus nécessaire. Ce serveur est uniquement destiné aux personnes bannies du serveur principal qui souhaitent faire appel de leur bannissement.

Vous pouvez rejoindre directement le serveur principal Les Élémentalistes."""
        
        # Send DM before action
        try:
            await member.send(message)
            dm_sent = True
        except:
            dm_sent = False
        
        # Perform action
        if is_ban:
            await member.ban(reason=reason)
            action = "banni"
        else:
            await member.kick(reason=reason)
            action = "retiré"
        
        print(f"Membre {member} {action} du serveur d'unban: {reason} (DM: {'✅' if dm_sent else '❌'})")
        return True
        
    except Exception as e:
        print(f"Erreur lors de la suppression de {member}: {e}")
        return False

class UnbanReasonModal(discord.ui.Modal):
    def __init__(self, view_instance):
        super().__init__(title="Demande d'Unban")
        self.view_instance = view_instance
        
        self.reason_input = discord.ui.TextInput(
            label="Expliquez pourquoi vous devriez être unban",
            placeholder="Décrivez votre situation et pourquoi vous méritez une seconde chance...",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason_input.value
        await self.view_instance.create_unban_ticket(interaction, reason)

class UnbanRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='📝 Faire une demande d\'unban', style=discord.ButtonStyle.primary, custom_id='create_unban_ticket')
    async def create_unban_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if we're in the unban server
        if interaction.guild.id != UNBAN_GUILD_ID:
            await interaction.response.send_message("❌ **Ce système n'est pas disponible sur ce serveur.**", ephemeral=True)
            return
            
        # Check if user already has a ticket
        category = interaction.guild.get_channel(CATEGORY_UNBAN)
        if category:
            for channel in category.channels:
                if isinstance(channel, discord.TextChannel) and channel.name.startswith('unban-') and f"unban-{interaction.user.id}" in channel.topic:
                    await interaction.response.send_message("❌ **Vous avez déjà une demande d'unban en cours !**\n\nVeuillez attendre la réponse du staff.", ephemeral=True)
                    return

        modal = UnbanReasonModal(self)
        await interaction.response.send_modal(modal)

    async def create_unban_ticket(self, interaction: discord.Interaction, reason: str):
        await interaction.response.defer(ephemeral=True)

        try:
            # Get main guild to check ban status
            main_guild = interaction.client.get_guild(GUILD_ID)
            if not main_guild:
                await interaction.followup.send("❌ **Erreur de configuration du serveur.**", ephemeral=True)
                return

            # Double check if user has staff permissions (shouldn't be making unban requests)
            has_mod_perms, has_oracle_role = await check_user_permissions(main_guild, interaction.user.id)
            if has_mod_perms or has_oracle_role:
                await interaction.followup.send("❌ **Vous êtes membre du staff et ne devriez pas faire de demande d'unban.**", ephemeral=True)
                return

            # Check if user is actually banned
            is_banned = await check_user_banned(main_guild, interaction.user.id)
            if not is_banned:
                await interaction.followup.send("❌ **Vous n'êtes pas banni du serveur principal.**\n\nVous n'avez pas besoin de faire de demande d'unban.", ephemeral=True)
                # Remove user with verification
                await verify_and_remove_user(
                    interaction.user, 
                    main_guild, 
                    "Pas banni du serveur principal - demande d'unban non nécessaire",
                    is_ban=False
                )
                return

            ticket_number = await get_next_unban_ticket_number()
            category = interaction.guild.get_channel(CATEGORY_UNBAN)
            
            # Create ticket channel
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            
            # Add staff permissions
            staff_role = interaction.guild.get_role(STAFF_ROLE_UNBAN)
            oracle_role = interaction.guild.get_role(ORACLE_ROLE_UNBAN)
            
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            if oracle_role:
                overwrites[oracle_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            ticket_channel = await rate_limiter.safe_channel_create(
                interaction.guild,
                name=f"unban-{ticket_number}",
                category=category,
                topic=f"unban-{interaction.user.id}",
                overwrites=overwrites
            )

            paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
            
            embed = discord.Embed(
                title="🔓 Nouvelle Demande d'Unban",
                description=f"**Utilisateur :** {interaction.user.mention}\n**Date :** {paris_time}\n\n**Justification :**\n```{reason}```",
                color=discord.Color.orange()
            )
            embed.set_footer(text=f"Demande d'unban #{ticket_number} • {interaction.guild.name}")

            view = UnbanManagementView()
            ping_message = f"<@&{STAFF_ROLE_UNBAN}>"
            await rate_limiter.safe_send(ticket_channel, ping_message, embed=embed, view=view)
            
            # Log the ticket
            await log_unban_ticket(interaction.user.id, ticket_channel.id, 'created')
            
            await interaction.followup.send(f"✅ **Votre demande d'unban a été créée !**\n\n📍 **Lien :** {ticket_channel.mention}\n🎫 **Numéro :** #{ticket_number}\n\nUn membre du staff examinera votre demande.", ephemeral=True)

        except Exception as e:
            print(f"Erreur lors de la création du ticket d'unban: {e}")
            try:
                await interaction.followup.send("❌ **Une erreur s'est produite lors de la création de votre demande.**\n\nVeuillez réessayer ou contacter un administrateur.", ephemeral=True)
            except:
                pass

class UnbanManagementView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='✅ Accepter la demande d\'unban', style=discord.ButtonStyle.success, custom_id='accept_unban')
    async def accept_unban(self, interaction: discord.Interaction, button: discord.ui.Button):
        if STAFF_ROLE_UNBAN not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("❌ Vous n'avez pas les permissions nécessaires.", ephemeral=True)
            return

        # Get user from channel topic
        try:
            user_id = int(interaction.channel.topic.split('unban-')[1])
            user = interaction.guild.get_member(user_id)
            if not user:
                user = await interaction.client.fetch_user(user_id)
        except:
            await interaction.response.send_message("❌ Impossible de récupérer l'utilisateur.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            # Get main guild
            main_guild = interaction.client.get_guild(GUILD_ID)
            if not main_guild:
                await interaction.followup.send("❌ Impossible d'accéder au serveur principal.")
                return

            # Unban from main guild first
            try:
                await main_guild.unban(user, reason=f"Demande d'unban acceptée par {interaction.user}")
                unban_success = True
            except discord.NotFound:
                unban_success = False
                await interaction.followup.send("⚠️ L'utilisateur n'était pas banni.")
            except Exception as e:
                unban_success = False
                await interaction.followup.send(f"❌ Erreur lors de l'unban: {e}")

            if unban_success:
                # Send custom acceptance message
                acceptance_message = f"""✅ **Excellente nouvelle !**

Votre demande d'unban pour le serveur **Les Élémentalistes** a été **acceptée** par l'équipe de modération !

🎉 Vous pouvez maintenant rejoindre le serveur principal. Nous espérons que vous respecterez les règles et contribuerez positivement à notre communauté.

**Merci de votre patience et bienvenue de retour !**

Vous allez être automatiquement retiré du serveur d'appel. discord.gg/elementalistes"""

                # Send DM
                try:
                    await user.send(acceptance_message)
                    dm_sent = True
                except:
                    dm_sent = False

                # Remove from unban server
                if isinstance(user, discord.Member):
                    try:
                        await user.kick(reason="Unban accordé - peut rejoindre le serveur principal")
                    except:
                        pass

                # Log the action
                await log_unban_ticket(user.id, interaction.channel.id, 'accepted', interaction.user.id)

                paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
                embed = discord.Embed(
                    title="✅ Demande d'Unban Acceptée",
                    description=f"**Staff :** {interaction.user.mention}\n**Date :** {paris_time}\n**DM envoyé :** {'✅' if dm_sent else '❌'}",
                    color=discord.Color.green()
                )
                await interaction.followup.send(embed=embed)

                # Close ticket after delay
                await asyncio.sleep(30)
                try:
                    await interaction.channel.delete()
                except:
                    pass

        except Exception as e:
            print(f"Erreur lors de l'acceptation de l'unban: {e}")
            await interaction.followup.send("❌ Une erreur s'est produite.")

    @discord.ui.button(label='❌ Rejeter la demande d\'unban', style=discord.ButtonStyle.danger, custom_id='reject_unban')
    async def reject_unban(self, interaction: discord.Interaction, button: discord.ui.Button):
        if STAFF_ROLE_UNBAN not in [role.id for role in interaction.user.roles]:
            await interaction.response.send_message("❌ Vous n'avez pas les permissions nécessaires.", ephemeral=True)
            return

        # Get user from channel topic
        try:
            user_id = int(interaction.channel.topic.split('unban-')[1])
            user = interaction.guild.get_member(user_id)
            if not user:
                user = await interaction.client.fetch_user(user_id)
        except:
            await interaction.response.send_message("❌ Impossible de récupérer l'utilisateur.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            # Get main guild for verification
            main_guild = interaction.client.get_guild(GUILD_ID)
            if not main_guild:
                await interaction.followup.send("❌ Impossible d'accéder au serveur principal.")
                return

            # Ban from unban server with verification
            ban_success = False
            dm_sent = False
            
            if isinstance(user, discord.Member):
                ban_success = await verify_and_remove_user(
                    user,
                    main_guild,
                    f"Demande d'unban rejetée par {interaction.user}",
                    is_ban=True
                )
                dm_sent = ban_success  # If ban succeeded, DM was sent in verify_and_remove_user

            # Log the action
            await log_unban_ticket(user.id, interaction.channel.id, 'rejected', interaction.user.id)

            paris_time = datetime.now(PARIS_TZ).strftime("%d/%m/%Y à %H:%M")
            embed = discord.Embed(
                title="❌ Demande d'Unban Rejetée",
                description=f"**Staff :** {interaction.user.mention}\n**Date :** {paris_time}\n**DM envoyé :** {'✅' if dm_sent else '❌'}\n**Ban appliqué :** {'✅' if ban_success else '❌'}",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed)

            # Close ticket after delay
            await asyncio.sleep(30)
            try:
                await interaction.channel.delete()
            except:
                pass

        except Exception as e:
            print(f"Erreur lors du rejet de l'unban: {e}")
            await interaction.followup.send("❌ Une erreur s'est produite.")

async def setup_unban_system(bot):
    """Setup the unban ticket system"""
    await init_unban_db()
    
    bot.add_view(UnbanRequestView())
    bot.add_view(UnbanManagementView())
    
    channel = bot.get_channel(UNBAN_CHANNEL_TICKET_ID)
    if channel and channel.guild.id == UNBAN_GUILD_ID:
        # Look for existing message
        existing_message = None
        async for message in channel.history(limit=10):
            if message.author == bot.user and message.embeds:
                if "Demandes d'Unban" in message.embeds[0].title:
                    existing_message = message
                    break
        
        if existing_message:
            try:
                view = UnbanRequestView()
                await rate_limiter.safe_edit(existing_message, view=view)
                return
            except:
                await rate_limiter.safe_delete(existing_message)
        
        # Create new message
        embed = discord.Embed(
            title="🔓 Demandes d'Unban - Les Élémentalistes",
            description="**Vous êtes banni du serveur principal ?**\n\nUtilisez ce système pour faire appel de votre bannissement. Votre demande sera examinée par notre équipe de modération.",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="📋 Instructions", 
            value="• Soyez honnête et respectueux\n• Expliquez clairement votre situation\n• Reconnaissez vos erreurs si nécessaire\n• Une seule demande par personne", 
            inline=False
        )
        embed.add_field(
            name="⚠️ Important", 
            value="Si votre demande est rejetée, vous ne pourrez plus en faire d'autres.", 
            inline=False
        )
        
        view = UnbanRequestView()
        await rate_limiter.safe_send(channel, embed=embed, view=view)

class UnbanCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.member_check_task.start()

    def cog_unload(self):
        self.member_check_task.cancel()

    @tasks.loop(minutes=5)
    async def member_check_task(self):
        """Check every 5 minutes if members should still be in the unban server"""
        try:
            unban_guild = self.bot.get_guild(UNBAN_GUILD_ID)
            main_guild = self.bot.get_guild(GUILD_ID)
            
            if not unban_guild or not main_guild:
                return

            staff_role = unban_guild.get_role(STAFF_ROLE_UNBAN)
            oracle_role = unban_guild.get_role(ORACLE_ROLE_UNBAN)
            
            members_to_remove = []

            for member in unban_guild.members:
                if member.bot:
                    continue

                try:
                    # Always double check main server permissions
                    has_mod_perms, has_oracle_role = await check_user_permissions(main_guild, member.id)
                    
                    # Check current roles in unban server
                    is_staff = staff_role and staff_role in member.roles
                    is_oracle_member = oracle_role and oracle_role in member.roles
                    
                    # Update roles if permissions changed
                    if is_staff and not has_mod_perms:
                        await member.remove_roles(staff_role, reason="Plus de permissions de modération sur le serveur principal")
                        is_staff = False
                    elif not is_staff and has_mod_perms:
                        await member.add_roles(staff_role, reason="Permissions de modération détectées sur le serveur principal")
                        is_staff = True
                    
                    if is_oracle_member and not has_oracle_role:
                        await member.remove_roles(oracle_role, reason="Plus le rôle Oracle sur le serveur principal")
                        is_oracle_member = False
                    elif not is_oracle_member and has_oracle_role:
                        await member.add_roles(oracle_role, reason="Rôle Oracle détecté sur le serveur principal")
                        is_oracle_member = True
                    
                    # If they have valid staff roles, they should stay
                    if is_staff or is_oracle_member:
                        continue
                    
                    # For non-staff members, check if they're still banned
                    is_banned = await check_user_banned(main_guild, member.id)
                    if not is_banned:
                        members_to_remove.append((member, "Plus banni du serveur principal"))
                    
                    # Rate limit to avoid hitting API limits
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    print(f"Erreur lors de la vérification de {member}: {e}")
                    continue

            # Remove members who shouldn't be there
            for member, reason in members_to_remove:
                await verify_and_remove_user(member, main_guild, reason, is_ban=False)
                await asyncio.sleep(1)  # Rate limit removals

        except Exception as e:
            print(f"Erreur dans la tâche de vérification des membres: {e}")

    @member_check_task.before_loop
    async def before_member_check_task(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        await setup_unban_system(self.bot)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle member join - check permissions and ban status"""
        # Only process joins in the unban server
        if member.guild.id != UNBAN_GUILD_ID:
            return
            
        # Skip bots
        if member.bot:
            return

        try:
            # Get main guild
            main_guild = self.bot.get_guild(GUILD_ID)
            if not main_guild:
                return

            # Check user permissions in main guild
            has_mod_perms, has_oracle_role = await check_user_permissions(main_guild, member.id)

            # Give appropriate roles
            if has_mod_perms:
                staff_role = member.guild.get_role(STAFF_ROLE_UNBAN)
                if staff_role:
                    await member.add_roles(staff_role, reason="Staff member from main guild")
                return

            if has_oracle_role:
                oracle_role = member.guild.get_role(ORACLE_ROLE_UNBAN)
                if oracle_role:
                    await member.add_roles(oracle_role, reason="Oracle from main guild")
                return

            # Check if user is banned from main guild
            is_banned = await check_user_banned(main_guild, member.id)
            
            if not is_banned:
                # User is not banned, remove with verification
                await verify_and_remove_user(
                    member, 
                    main_guild, 
                    "Pas banni du serveur principal", 
                    is_ban=False
                )

        except Exception as e:
            print(f"Erreur lors de la gestion de l'arrivée de {member}: {e}")

async def setup(bot):
    await bot.add_cog(UnbanCog(bot))
