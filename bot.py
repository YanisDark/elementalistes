import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
import logging
from pathlib import Path
import traceback

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Bot configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = int(os.getenv('GUILD_ID'))
PREFIX = os.getenv('PREFIX', '!')

# Bot intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

class ElementalistesBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=PREFIX,
            intents=intents,
            case_insensitive=True
        )
        
    async def setup_hook(self):
        """Load all cogs and sync commands"""
        modules_dir = Path(__file__).parent / "modules"
        
        if not modules_dir.exists():
            modules_dir.mkdir()
            logging.warning("Dossier modules créé")
            return
            
        # Create __init__.py if it doesn't exist
        init_file = modules_dir / "__init__.py"
        if not init_file.exists():
            init_file.touch()
        
        loaded_modules = []
        failed_modules = []
        
        for file in modules_dir.glob("*.py"):
            if file.name == "__init__.py":
                continue
                
            module_name = f"modules.{file.stem}"
            try:
                await self.load_extension(module_name)
                loaded_modules.append(module_name)
                logging.info(f"✅ Module {module_name} chargé")
            except Exception as e:
                failed_modules.append((module_name, str(e)))
                logging.error(f"❌ Erreur module {module_name}: {e}")
                logging.error(traceback.format_exc())
        
        logging.info(f"Modules chargés: {len(loaded_modules)}, Échecs: {len(failed_modules)}")
        
        # Sync application commands
        try:
            synced = await self.tree.sync()
            logging.info(f"{len(synced)} commandes slash synchronisées")
        except Exception as e:
            logging.error(f"Erreur synchronisation: {e}")

    async def on_ready(self):
        logging.info(f'{self.user} connecté!')
        logging.info(f'Serveurs: {len(self.guilds)}')
        
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Les Élémentalistes"
            )
        )

bot = ElementalistesBot()

@bot.event
async def on_error(event, *args, **kwargs):
    logging.error(f"Erreur événement {event}: {traceback.format_exc()}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Permissions insuffisantes.")
    else:
        logging.error(f"Erreur commande: {error}")
        await ctx.send("❌ Erreur interne.")

if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.error(f"Erreur fatale: {e}")
