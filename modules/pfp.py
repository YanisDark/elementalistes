import discord
from discord.ext import commands
from discord import app_commands

class ProfilePicture(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="pfp", description="Affiche la photo de profil et la bannière d'un utilisateur")
    @app_commands.describe(utilisateur="L'utilisateur dont vous voulez voir la photo de profil")
    async def slash_pfp(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        if utilisateur is None:
            utilisateur = interaction.user
        await self._send_profile(interaction, utilisateur, is_slash=True)

    @app_commands.command(name="pdp", description="Affiche la photo de profil et la bannière d'un utilisateur")
    @app_commands.describe(utilisateur="L'utilisateur dont vous voulez voir la photo de profil")
    async def slash_pdp(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        if utilisateur is None:
            utilisateur = interaction.user
        await self._send_profile(interaction, utilisateur, is_slash=True)

    @commands.command(name="pfp", aliases=["pdp"])
    async def text_pfp(self, ctx, utilisateur: discord.Member = None):
        if utilisateur is None:
            utilisateur = ctx.author
        await self._send_profile(ctx, utilisateur, is_slash=False)

    async def _send_profile(self, ctx_or_interaction, utilisateur: discord.Member, is_slash: bool):
        try:
            # Fetch user to get banner info
            user = await self.bot.fetch_user(utilisateur.id)
            
            embed = discord.Embed(
                title=f"Profil de {utilisateur.display_name}",
                color=utilisateur.color if utilisateur.color != discord.Color.default() else discord.Color.blue()
            )
            
            # Add avatar
            avatar_url = utilisateur.display_avatar.url
            embed.set_image(url=avatar_url)
            embed.add_field(name="📸 Photo de profil", value=f"[Lien direct]({avatar_url})", inline=False)
            
            embeds = [embed]
            
            # Add banner if exists
            if user.banner:
                banner_url = user.banner.url
                embed.add_field(name="🎨 Bannière", value=f"[Lien direct]({banner_url})", inline=False)
                
                # Create second embed for banner
                banner_embed = discord.Embed(
                    title=f"Bannière de {utilisateur.display_name}",
                    color=utilisateur.color if utilisateur.color != discord.Color.default() else discord.Color.blue()
                )
                banner_embed.set_image(url=banner_url)
                embeds.append(banner_embed)
            else:
                embed.add_field(name="🎨 Bannière", value="Aucune bannière définie", inline=False)
            
            if is_slash:
                await ctx_or_interaction.response.send_message(embeds=embeds)
            else:
                await ctx_or_interaction.send(embeds=embeds)
                
        except Exception as e:
            error_msg = "❌ Erreur lors de la récupération du profil."
            if is_slash:
                await ctx_or_interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await ctx_or_interaction.send(error_msg)

async def setup(bot):
    await bot.add_cog(ProfilePicture(bot))
