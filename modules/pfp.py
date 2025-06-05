import discord
from discord.ext import commands
from discord import app_commands

class ProfilePicture(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="pfp", description="Affiche la photo de profil et la bannière d'un utilisateur")
    async def slash_pfp(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        await self._send_profile(interaction, utilisateur or interaction.user, True)

    @app_commands.command(name="pdp", description="Affiche la photo de profil et la bannière d'un utilisateur") 
    async def slash_pdp(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        await self._send_profile(interaction, utilisateur or interaction.user, True)

    @commands.command(name="pfp", aliases=["pdp"])
    async def text_pfp(self, ctx, utilisateur: discord.Member = None):
        await self._send_profile(ctx, utilisateur or ctx.author, False)

    async def _send_profile(self, ctx, utilisateur: discord.Member, is_slash: bool):
        try:
            user = await self.bot.fetch_user(utilisateur.id)
            embeds = []
            
            # Global avatar
            embeds.append(discord.Embed(title=f"Avatar de {utilisateur.display_name}").set_image(url=user.avatar.url if user.avatar else user.default_avatar.url))
            
            # Server avatar if different
            if utilisateur.guild_avatar and (not user.avatar or utilisateur.guild_avatar.url != user.avatar.url):
                embeds.append(discord.Embed(title=f"Avatar serveur de {utilisateur.display_name}").set_image(url=utilisateur.guild_avatar.url))
            
            # Global banner if exists
            if user.banner:
                embeds.append(discord.Embed(title=f"Bannière de {utilisateur.display_name}").set_image(url=user.banner.url))
            
            # Server banner if different
            if hasattr(utilisateur, 'guild_banner') and utilisateur.guild_banner and (not user.banner or utilisateur.guild_banner.url != user.banner.url):
                embeds.append(discord.Embed(title=f"Bannière serveur de {utilisateur.display_name}").set_image(url=utilisateur.guild_banner.url))
            
            if is_slash:
                await ctx.response.send_message(embeds=embeds)
            else:
                await ctx.send(embeds=embeds)
                
        except Exception:
            error_msg = "❌ Erreur lors de la récupération du profil."
            if is_slash:
                await ctx.response.send_message(error_msg, ephemeral=True)
            else:
                await ctx.send(error_msg)

async def setup(bot):
    await bot.add_cog(ProfilePicture(bot))
