import discord
from discord.ext import commands
import os
import asyncio
import random
import time
from collections import defaultdict
import logging

class RateLimiter:
    def __init__(self):
        self.global_limit = 45  # Under Discord's 50/sec limit
        self.requests = []
        self.invalid_requests = defaultdict(list)  # Track invalid requests per 10min
        self.message_delete_bucket = []
        
    async def wait_if_needed(self, operation_type="general"):
        """Wait if we're approaching rate limits"""
        now = time.time()
        
        # Clean old requests (older than 1 second for global)
        self.requests = [req_time for req_time in self.requests if now - req_time < 1.0]
        
        # Global rate limit check
        if len(self.requests) >= self.global_limit:
            wait_time = 1.0 - (now - min(self.requests))
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                return await self.wait_if_needed(operation_type)
        
        # Special handling for message deletions (more conservative)
        if operation_type == "delete":
            self.message_delete_bucket = [req_time for req_time in self.message_delete_bucket if now - req_time < 1.0]
            if len(self.message_delete_bucket) >= 5:  # Max 5 deletes per second
                wait_time = 1.0 - (now - min(self.message_delete_bucket))
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                    return await self.wait_if_needed(operation_type)
            self.message_delete_bucket.append(now)
        
        self.requests.append(now)
    
    def track_invalid_request(self, error_code):
        """Track invalid requests to avoid Cloudflare bans"""
        now = time.time()
        # Clean requests older than 10 minutes
        self.invalid_requests[error_code] = [
            req_time for req_time in self.invalid_requests[error_code] 
            if now - req_time < 600
        ]
        self.invalid_requests[error_code].append(now)
        
        # Log warning if approaching limit
        total_invalid = sum(len(reqs) for reqs in self.invalid_requests.values())
        if total_invalid > 8000:  # 80% of 10k limit
            logging.warning(f"Approaching invalid request limit: {total_invalid}/10000")

class WelcomeView(discord.ui.View):
    def __init__(self, member_id, cog):
        super().__init__(timeout=None)
        self.member_id = member_id
        self.cog = cog
    
    @discord.ui.button(
        label="Bienvenue !",
        emoji="👋",
        style=discord.ButtonStyle.green,
        custom_id="welcome_button"
    )
    async def welcome_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Extract member ID from message content
            content = interaction.message.content
            member_mention_start = content.find('<@')
            member_mention_end = content.find('>', member_mention_start)
            
            if member_mention_start == -1 or member_mention_end == -1:
                await interaction.response.send_message("❌ Erreur: Impossible de trouver le nouveau membre.", ephemeral=True)
                return
            
            member_id = int(content[member_mention_start+2:member_mention_end])
            
            # Get cached member or fetch if needed
            member = self.cog.get_cached_member(interaction.guild.id, member_id)
            if not member:
                try:
                    member = interaction.guild.get_member(member_id)
                    if member:
                        self.cog.cache_member(member)
                except:
                    pass
                
            if not member:
                await interaction.response.send_message("❌ Erreur: Membre introuvable.", ephemeral=True)
                return
            
            # Check if user is trying to welcome themselves
            if interaction.user.id == member_id:
                await interaction.response.send_message("❌ Tu ne peux pas te souhaiter la bienvenue à toi-même !", ephemeral=True)
                return
            
            # Check if user has already welcomed this member
            if member_id not in self.cog.welcomed_by:
                self.cog.welcomed_by[member_id] = set()
            
            if interaction.user.id in self.cog.welcomed_by[member_id]:
                await interaction.response.send_message("❌ Tu as déjà souhaité la bienvenue à ce membre !", ephemeral=True)
                return
            
            # Add user to welcomed list
            self.cog.welcomed_by[member_id].add(interaction.user.id)
            
            welcome_responses = [
                f"**{interaction.user.display_name}** accueille **{member.display_name}** dans la famille ! <:greetingskonata:1377326152108212336>",
                f"**{interaction.user.display_name}** souhaite la bienvenue à **{member.display_name}** ! <:greetingskonata:1377326152108212336>",
                f"**{interaction.user.display_name}** dit salut à **{member.display_name}** ! <:greetingskonata:1377326152108212336>",
                f"**{interaction.user.display_name}** ouvre les bras pour **{member.display_name}** ! <:greetingskonata:1377326152108212336>",
                f"**{interaction.user.display_name}** célèbre l'arrivée de **{member.display_name}** ! <:greetingskonata:1377326152108212336>"
            ]
            
            welcome_message = random.choice(welcome_responses)
            
            await interaction.response.send_message(welcome_message)
            response_message = await interaction.original_response()
            
            # Add response message to tracking
            if member_id not in self.cog.welcome_messages:
                self.cog.welcome_messages[member_id] = []
            self.cog.welcome_messages[member_id].append(response_message)
            
        except discord.HTTPException as e:
            if e.status == 429:
                # Rate limited - log and retry after delay
                retry_after = getattr(e, 'retry_after', 1)
                await asyncio.sleep(retry_after)
                # Don't retry button interactions due to timeout
            else:
                self.cog.rate_limiter.track_invalid_request(e.status)
        except Exception as e:
            logging.error(f"Erreur dans welcome_button: {e}")

class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rate_limiter = RateLimiter()
        self.general_channel_id = os.getenv('GENERAL_CHANNEL_ID')
        self.presentations_channel_id = os.getenv('PRESENTATIONS_CHANNEL_ID')
        self.creer_vocal_channel_id = os.getenv('CREER_VOCAL_CHANNEL_ID')
        self.selfies_channel_id = os.getenv('SELFIES_CHANNEL_ID')
        self.welcome_messages = {}
        self.welcomed_by = {}
        
        # Cache for frequently accessed objects
        self.member_cache = {}  # {guild_id: {member_id: member}}
        self.channel_cache = {}  # {channel_id: channel}
        self.permission_cache = {}  # {(guild_id, user_id): permissions}
        
        # Welcome message templates
        self.welcome_templates = [
            "<a:konatahype:1377054145848279081> {name} vient de rejoindre **{guild}** ! Que l'aventure commence !",
            "<a:konatahype:1377054145848279081> Un nouvel élémentaliste apparaît ! Bienvenue à {name} chez **{guild}** !",
            "<a:konatahype:1377054145848279081> {name} a franchi les portes de **{guild}** ! Préparez-vous !",
            "<a:konatahype:1377054145848279081> Les éléments s'alignent... {name} rejoint **{guild}** !",
            "<a:konatahype:1377054145848279081> Le village s'endort... {name} se réveille ! Bienvenue parmi nous !",
            "<a:konatahype:1377054145848279081> Une nouvelle âme brave rejoint **{guild}** ! Salut {name} !",
            "<a:konatahype:1377054145848279081> {name} découvre **{guild}** ! Que ton voyage soit épique !",
            "<a:konatahype:1377054145848279081> Les portes s'ouvrent pour {name} ! Bienvenue chez **{guild}** !"
        ]
        
        self.welcome_patterns = [
            "vient de rejoindre", "élémentaliste apparaît", "franchi les portes",
            "éléments s'alignent", "village s'endort", "âme brave rejoint",
            "découvre", "portes s'ouvrent", "accueille", "souhaite la bienvenue",
            "dit salut", "ouvre les bras", "célèbre l'arrivée"
        ]
        
        self.bot.add_view(WelcomeView(None, self))
    
    def cache_member(self, member):
        """Cache member object to reduce API calls"""
        if member.guild.id not in self.member_cache:
            self.member_cache[member.guild.id] = {}
        self.member_cache[member.guild.id][member.id] = member
    
    def get_cached_member(self, guild_id, member_id):
        """Get cached member or None"""
        return self.member_cache.get(guild_id, {}).get(member_id)
    
    def get_cached_channel(self, channel_id):
        """Get cached channel or fetch and cache it"""
        if channel_id in self.channel_cache:
            return self.channel_cache[channel_id]
        
        channel = self.bot.get_channel(int(channel_id))
        if channel:
            self.channel_cache[channel_id] = channel
        return channel
    
    async def can_delete_messages(self, channel):
        """Check if bot can delete messages in channel"""
        cache_key = (channel.guild.id, self.bot.user.id)
        if cache_key in self.permission_cache:
            perms = self.permission_cache[cache_key]
        else:
            perms = channel.permissions_for(channel.guild.me)
            self.permission_cache[cache_key] = perms
        
        return perms.manage_messages
    
    async def safe_delete_message(self, message):
        """Safely delete a message with rate limiting and error handling"""
        try:
            await self.rate_limiter.wait_if_needed("delete")
            await message.delete()
            return True
        except discord.NotFound:
            return True  # Already deleted
        except discord.Forbidden:
            self.rate_limiter.track_invalid_request(403)
            return False
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, 'retry_after', 1)
                await asyncio.sleep(retry_after)
                return await self.safe_delete_message(message)
            else:
                self.rate_limiter.track_invalid_request(e.status)
                return False
        except Exception as e:
            logging.error(f"Erreur lors de la suppression de message: {e}")
            return False
    
    async def cleanup_old_welcome_messages(self):
        """Clean up old welcome messages with optimized rate limiting"""
        try:
            if not self.general_channel_id or self.general_channel_id == 'discussion_channel_id':
                return
            
            channel = self.get_cached_channel(self.general_channel_id)
            if not channel or not await self.can_delete_messages(channel):
                return
            
            print("Nettoyage des anciens messages de bienvenue...")
            deleted_count = 0
            messages_to_delete = []
            
            # Collect messages first
            try:
                await self.rate_limiter.wait_if_needed("general")
                async for message in channel.history(limit=100):
                    if message.author.id != self.bot.user.id:
                        continue
                    
                    should_delete = False
                    
                    # Check for welcome patterns
                    if "<a:konatahype:1377054145848279081>" in message.content:
                        for pattern in self.welcome_patterns:
                            if pattern in message.content.lower():
                                should_delete = True
                                break
                    
                    # Check for welcome button
                    if message.components:
                        for component in message.components:
                            if hasattr(component, 'children'):
                                for child in component.children:
                                    if hasattr(child, 'custom_id') and child.custom_id == "welcome_button":
                                        should_delete = True
                                        break
                    
                    # Check embeds
                    if message.embeds:
                        for embed in message.embeds:
                            if embed.title and "bienvenue" in embed.title.lower():
                                should_delete = True
                                break
                    
                    if should_delete:
                        messages_to_delete.append(message)
            
            except discord.Forbidden:
                self.rate_limiter.track_invalid_request(403)
                return
            except Exception as e:
                logging.error(f"Erreur lors de la collecte des messages: {e}")
                return
            
            # Delete messages with rate limiting
            for message in messages_to_delete:
                if await self.safe_delete_message(message):
                    deleted_count += 1
                    
                    # Additional delay between deletions
                    if deleted_count % 5 == 0:  # Every 5 deletions
                        await asyncio.sleep(1)
            
            if deleted_count > 0:
                print(f"✅ {deleted_count} anciens messages de bienvenue supprimés.")
            else:
                print("✅ Aucun ancien message de bienvenue trouvé.")
                
        except Exception as e:
            logging.error(f"Erreur lors du nettoyage des messages de bienvenue: {e}")
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Triggered when bot is ready"""
        if hasattr(self, '_cleanup_done'):
            return
        self._cleanup_done = True
        
        await asyncio.sleep(3)  # Wait for bot to be fully ready
        await self.cleanup_old_welcome_messages()
    
    async def send_welcome_dm(self, member, channel=None):
        """Send welcome DM with rate limiting"""
        try:
            await self.rate_limiter.wait_if_needed("general")
            
            # Cache member
            self.cache_member(member)
            
            # Get channel mentions with cached channels
            presentations_channel = f"<#{self.presentations_channel_id}>" if self.presentations_channel_id and self.presentations_channel_id != 'presentations_channel_id' else "#présentations"
            creer_vocal_channel = f"<#{self.creer_vocal_channel_id}>" if self.creer_vocal_channel_id and self.creer_vocal_channel_id != 'creer_vocal_channel_id' else "#créer-vocal"
            selfies_channel = f"<#{self.selfies_channel_id}>" if self.selfies_channel_id and self.selfies_channel_id != 'selfies_channel_id' else "#selfies"
            
            dm_message = f"""Salut **{member.display_name}** ! <a:konatahype:1377054145848279081>

Bienvenue sur **{member.guild.name}** ! Voici quelques informations pour bien commencer :

🌟 **Présente-toi** dans {presentations_channel} pour que la communauté apprenne à te connaître !

🎤 **Rejoins-nous en vocal** ! Utilise {creer_vocal_channel} pour créer ton propre salon vocal temporaire.

📸 **Partage tes selfies** dans {selfies_channel} si tu veux montrer ton visage à la communauté !

N'hésite pas à explorer les autres salons et à participer aux discussions. Si tu as des questions, l'équipe de modération est là pour t'aider !

Amusez-vous bien ! ✨"""

            await member.send(dm_message)
            return True
            
        except discord.Forbidden:
            self.rate_limiter.track_invalid_request(403)
            # Send as embed in channel if DM fails
            if channel:
                try:
                    await self.rate_limiter.wait_if_needed("general")
                    
                    embed = discord.Embed(
                        title=f"Bienvenue {member.display_name} ! <a:konatahype:1377054145848279081>",
                        description=f"Bienvenue sur **{member.guild.name}** ! Voici quelques informations pour bien commencer :",
                        color=0x7289da
                    )
                    
                    embed.add_field(
                        name="🌟 Présente-toi",
                        value=f"Va dans {presentations_channel} pour que la communauté apprenne à te connaître !",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="🎤 Rejoins-nous en vocal",
                        value=f"Utilise {creer_vocal_channel} pour créer ton propre salon vocal temporaire.",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="📸 Partage tes selfies",
                        value=f"Va dans {selfies_channel} si tu veux montrer ton visage à la communauté !",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="💬 Participe aux discussions",
                        value="N'hésite pas à explorer les autres salons et à participer aux discussions. Si tu as des questions, l'équipe de modération est là pour t'aider !",
                        inline=False
                    )
                    
                    embed.set_footer(text="Je n'ai pas pu t'envoyer un message en MP, alors je t'explique tout ici :)")
                    
                    message = await channel.send(content=member.mention, embed=embed)
                    
                    if member.id not in self.welcome_messages:
                        self.welcome_messages[member.id] = []
                    self.welcome_messages[member.id].append(message)
                    
                except discord.HTTPException as e:
                    if e.status == 429:
                        retry_after = getattr(e, 'retry_after', 1)
                        await asyncio.sleep(retry_after)
                    else:
                        self.rate_limiter.track_invalid_request(e.status)
                except Exception as e:
                    logging.error(f"Erreur lors de l'envoi de l'embed de bienvenue: {e}")
            return False
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, 'retry_after', 1)
                await asyncio.sleep(retry_after)
                return await self.send_welcome_dm(member, channel)
            else:
                self.rate_limiter.track_invalid_request(e.status)
            return False
        except Exception as e:
            logging.error(f"Erreur lors de l'envoi du MP de bienvenue: {e}")
            return False
    
    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Event triggered when a new member joins"""
        if member.bot:
            return
        
        # Cache member immediately
        self.cache_member(member)
        
        if not self.general_channel_id or self.general_channel_id == 'discussion_channel_id':
            return
        
        channel = self.get_cached_channel(self.general_channel_id)
        if not channel:
            return
        
        try:
            # Send welcome DM first (doesn't count against rate limit as much)
            await self.send_welcome_dm(member, channel)
            
            # Wait before sending public message
            await self.rate_limiter.wait_if_needed("general")
            
            # Create welcome message
            welcome_template = random.choice(self.welcome_templates)
            welcome_text = welcome_template.format(name=member.mention, guild=member.guild.name)
            
            view = WelcomeView(member.id, self)
            
            message = await channel.send(content=welcome_text, view=view)
            
            # Initialize tracking
            if member.id not in self.welcome_messages:
                self.welcome_messages[member.id] = []
            self.welcome_messages[member.id].append(message)
            self.welcomed_by[member.id] = set()
            
            # Schedule deletion
            asyncio.create_task(self._delete_messages_after_delay(member.id, 3600))
            
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, 'retry_after', 1)
                await asyncio.sleep(retry_after)
                # Try again after rate limit
                asyncio.create_task(self._retry_welcome_message(member))
            elif e.status in [401, 403]:
                self.rate_limiter.track_invalid_request(e.status)
        except Exception as e:
            logging.error(f"Erreur lors de l'envoi du message de bienvenue: {e}")
    
    async def _retry_welcome_message(self, member):
        """Retry welcome message after rate limit"""
        try:
            await self.rate_limiter.wait_if_needed("general")
            
            channel = self.get_cached_channel(self.general_channel_id)
            if not channel:
                return
            
            welcome_template = random.choice(self.welcome_templates)
            welcome_text = welcome_template.format(name=member.mention, guild=member.guild.name)
            
            view = WelcomeView(member.id, self)
            message = await channel.send(content=welcome_text, view=view)
            
            if member.id not in self.welcome_messages:
                self.welcome_messages[member.id] = []
            self.welcome_messages[member.id].append(message)
            
            if member.id not in self.welcomed_by:
                self.welcomed_by[member.id] = set()
            
            asyncio.create_task(self._delete_messages_after_delay(member.id, 3600))
            
        except Exception as e:
            logging.error(f"Erreur lors du retry du message de bienvenue: {e}")
    
    @commands.command(name='testwelcome')
    @commands.has_permissions(manage_messages=True)
    async def test_welcome(self, ctx, user_id: int = None):
        """Simulate welcome message for testing"""
        if user_id is None:
            await ctx.send("❌ Veuillez fournir un ID utilisateur. Usage: `!testwelcome <user_id>`")
            return
        
        try:
            await self.rate_limiter.wait_if_needed("general")
            
            member = self.get_cached_member(ctx.guild.id, user_id)
            if not member:
                member = ctx.guild.get_member(user_id)
                if member:
                    self.cache_member(member)
                else:
                    member = await ctx.guild.fetch_member(user_id)
                    if member:
                        self.cache_member(member)
            
            if not member:
                await ctx.send("❌ Utilisateur introuvable sur ce serveur.")
                return
            
            await self.send_welcome_dm(member, ctx.channel)
            
            await self.rate_limiter.wait_if_needed("general")
            
            welcome_template = random.choice(self.welcome_templates)
            welcome_text = welcome_template.format(name=member.mention, guild=member.guild.name)
            
            view = WelcomeView(member.id, self)
            message = await ctx.send(content=welcome_text, view=view)
            
            if member.id not in self.welcome_messages:
                self.welcome_messages[member.id] = []
            self.welcome_messages[member.id].append(message)
            
            if member.id not in self.welcomed_by:
                self.welcomed_by[member.id] = set()
            
            asyncio.create_task(self._delete_messages_after_delay(member.id, 3600))
            
            # Delete command message
            await self.safe_delete_message(ctx.message)
            
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = getattr(e, 'retry_after', 1)
                await ctx.send(f"⏳ Rate limité, réessayez dans {retry_after} secondes.")
            else:
                self.rate_limiter.track_invalid_request(e.status)
                await ctx.send(f"❌ Erreur HTTP {e.status}: {e}")
        except Exception as e:
            await ctx.send(f"❌ Erreur lors du test de bienvenue: {e}")
    
    @commands.command(name='cleanwelcome')
    @commands.has_permissions(manage_messages=True)
    async def clean_welcome_messages(self, ctx):
        """Manually clean up welcome messages"""
        await ctx.send("🧹 Nettoyage des messages de bienvenue en cours...")
        await self.cleanup_old_welcome_messages()
        await ctx.send("✅ Nettoyage terminé !")
    
    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Event triggered when a member leaves"""
        await self._cleanup_member_messages(member.id)
        
        # Remove from cache
        if member.guild.id in self.member_cache:
            self.member_cache[member.guild.id].pop(member.id, None)
    
    async def _cleanup_member_messages(self, member_id):
        """Delete all messages related to a member with rate limiting"""
        if member_id in self.welcome_messages:
            for message in self.welcome_messages[member_id]:
                await self.safe_delete_message(message)
            del self.welcome_messages[member_id]
        
        if member_id in self.welcomed_by:
            del self.welcomed_by[member_id]
    
    async def _delete_messages_after_delay(self, member_id, delay):
        """Delete welcome messages after specified delay"""
        await asyncio.sleep(delay)
        await self._cleanup_member_messages(member_id)

async def setup(bot):
    await bot.add_cog(Welcome(bot))
