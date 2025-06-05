import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from modules.rate_limiter import get_rate_limiter, safe_api_call

class ProfilePicture(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rate_limiter = get_rate_limiter()

    @app_commands.command(name="pfp", description="Affiche la photo de profil et la bannière d'un utilisateur")
    @app_commands.describe(utilisateur="L'utilisateur dont afficher le profil")
    async def slash_pfp(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        await self._send_profile(interaction, utilisateur or interaction.user, True)

    @app_commands.command(name="pdp", description="Affiche la photo de profil et la bannière d'un utilisateur") 
    @app_commands.describe(utilisateur="L'utilisateur dont afficher le profil")
    async def slash_pdp(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        await self._send_profile(interaction, utilisateur or interaction.user, True)

    @commands.command(name="pfp", aliases=["pdp"])
    @commands.cooldown(2, 10, commands.BucketType.user)
    @commands.cooldown(5, 30, commands.BucketType.channel)
    async def text_pfp(self, ctx, utilisateur: discord.Member = None):
        await self._send_profile(ctx, utilisateur or ctx.author, False)

    async def _send_profile(self, ctx, utilisateur: discord.Member, is_slash: bool):
        try:
            # Defer response for slash commands to avoid timeout
            if is_slash and not ctx.response.is_done():
                await ctx.response.defer()

            # Rate limited API calls
            user = await safe_api_call(
                self.bot.fetch_user(utilisateur.id),
                route=f"GET /users/{utilisateur.id}",
                major_params={'user_id': utilisateur.id}
            )
            
            if hasattr(ctx, 'guild') and ctx.guild:
                try:
                    member = await safe_api_call(
                        ctx.guild.fetch_member(utilisateur.id),
                        route=f"GET /guilds/{ctx.guild.id}/members/{utilisateur.id}",
                        major_params={'guild_id': ctx.guild.id}
                    )
                except discord.NotFound:
                    member = utilisateur
            else:
                member = utilisateur
            
            embeds = []
            
            # Global avatar
            avatar_url = user.avatar.url if user.avatar else user.default_avatar.url
            embed_avatar = discord.Embed(
                title=f"🖼️ Avatar de {utilisateur.display_name}",
                color=discord.Color.blue()
            )
            embed_avatar.set_image(url=avatar_url)
            embed_avatar.set_footer(text=f"Demandé par {ctx.user.display_name if is_slash else ctx.author.display_name}")
            embeds.append(embed_avatar)
            
            # Server avatar if different
            if hasattr(member, 'guild_avatar') and member.guild_avatar:
                if not user.avatar or member.guild_avatar.url != user.avatar.url:
                    embed_server = discord.Embed(
                        title=f"🏰 Avatar serveur de {utilisateur.display_name}",
                        color=discord.Color.green()
                    )
                    embed_server.set_image(url=member.guild_avatar.url)
                    embed_server.set_footer(text=f"Demandé par {ctx.user.display_name if is_slash else ctx.author.display_name}")
                    embeds.append(embed_server)
            
            # Global banner if exists
            if user.banner:
                embed_banner = discord.Embed(
                    title=f"🎨 Bannière de {utilisateur.display_name}",
                    color=discord.Color.purple()
                )
                embed_banner.set_image(url=user.banner.url)
                embed_banner.set_footer(text=f"Demandé par {ctx.user.display_name if is_slash else ctx.author.display_name}")
                embeds.append(embed_banner)
            
            # Server banner if different  
            if hasattr(member, 'banner') and member.banner:
                if not user.banner or member.banner.url != user.banner.url:
                    embed_server_banner = discord.Embed(
                        title=f"🏰 Bannière serveur de {utilisateur.display_name}",
                        color=discord.Color.orange()
                    )
                    embed_server_banner.set_image(url=member.banner.url)
                    embed_server_banner.set_footer(text=f"Demandé par {ctx.user.display_name if is_slash else ctx.author.display_name}")
                    embeds.append(embed_server_banner)
            
            # Limit embeds to prevent spam
            if len(embeds) > 4:
                embeds = embeds[:4]
            
            # Send with rate limiting
            if is_slash:
                if ctx.response.is_done():
                    await self.rate_limiter.safe_send(ctx.followup, embeds=embeds)
                else:
                    await ctx.response.send_message(embeds=embeds)
            else:
                await self.rate_limiter.safe_send(ctx.channel, embeds=embeds)
                
        except commands.CommandOnCooldown:
            # Don't handle cooldown here, let the error handler deal with it
            raise
            
        except discord.HTTPException as e:
            if e.status == 429:
                error_msg = "⏰ Trop de requêtes, veuillez patienter quelques secondes."
            else:
                error_msg = "❌ Erreur lors de la récupération du profil."
            await self._send_error(ctx, error_msg, is_slash)
            
        except discord.NotFound:
            error_msg = "❌ Utilisateur introuvable."
            await self._send_error(ctx, error_msg, is_slash)
            
        except discord.Forbidden:
            error_msg = "❌ Permissions insuffisantes pour récupérer ce profil."
            await self._send_error(ctx, error_msg, is_slash)
            
        except asyncio.TimeoutError:
            error_msg = "⏰ Délai d'attente dépassé, réessayez plus tard."
            await self._send_error(ctx, error_msg, is_slash)

    async def _send_error(self, ctx, message: str, is_slash: bool):
        """Send error message with rate limiting"""
        try:
            if is_slash:
                if ctx.response.is_done():
                    await self.rate_limiter.safe_send(ctx.followup, message, ephemeral=True)
                else:
                    await ctx.response.send_message(message, ephemeral=True)
            else:
                await self.rate_limiter.safe_send(ctx.channel, message)
        except:
            pass  # Fail silently if we can't send error

    @text_pfp.error
    async def pfp_error(self, ctx, error):
        """Handle cooldown errors"""
        if isinstance(error, commands.CommandOnCooldown):
            retry_after = int(error.retry_after)
            await ctx.send(f"⏰ Commande en cooldown ! Réessayez dans {retry_after} seconde{'s' if retry_after > 1 else ''}.", delete_after=5)
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Vous n'avez pas les permissions nécessaires.", delete_after=5)

async def setup(bot):
    await bot.add_cog(ProfilePicture(bot))
