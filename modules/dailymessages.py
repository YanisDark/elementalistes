# modules/dailymessages.py
import asyncio
import random
from datetime import datetime, time
import pytz
from discord.ext import commands, tasks
import discord
import os
import json

class DailyMessages(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.timezone = pytz.timezone('Europe/Paris')
        self.general_channel_id = int(os.getenv('GENERAL_CHANNEL_ID'))
        self.data_file = 'data/daily_messages.json'
        
        self.evening_messages = [
            "🌙 **Bonne nuit à la communauté !** Heure de recharger vos pouvoirs élémentaires. Retrouvez-nous demain, ou restez si vous êtes particulièrement puissants ! Repos bien mérité à tous. 💤",
            "🌙 **Les étoiles brillent sur les Élémentalistes !** Temps de repos pour régénérer votre magie. Que vos rêves soient emplis de fantaisie ! Bonne nuit ! ✨💤",
            "🌙 **La nuit tombe sur notre royaume !** Rechargez vos cristaux et vos énergies. Les plus courageux peuvent rester éveillés, les sages vont dormir ! Douce nuit ! 🔮💤",
            "🌙 **Extinction des feux !** Il est temps de laisser vos pouvoirs se reposer. Demain nous apportera de nouvelles aventures ! Bonne nuit les élémentalistes ! 🌟💤",
            "🌙 **Les éléments murmurent qu'il est l'heure de dormir !** Ressourcez-vous pour être au top demain. Les plus résistants peuvent défier le sommeil ! Bonne nuit ! 🌊🔥💨🌍💤"
        ]
        
        self.weekend_messages = [
            "🎉 **C'est le weekend !** Les éléments se déchaînent ! Temps libre pour tous les élémentalistes ! Prêts pour des discussions enflammées et des rencontres rafraîchissantes ? Le serveur est tout à vous !",
            "🎉 **Weekend élémentaire activé !** Libérez vos pouvoirs sans retenue ! Discussions, fun et bonne humeur au programme ! Que la magie opère tout le weekend ! ⚡🌟",
            "🎉 **C'est le weekend ! Pyromanciens, Aquamanciens, Géomanciens, Cryomanciens, Aéromanciens... Tous unis pour deux jours de pure magie ! 🔥🌊🌍💨",
            "🎉 **Signal weekend détecté !** Les éléments dansent de joie ! Profitez de ces 48h magiques pour créer des liens et partager vos passions ! Le serveur vous appartient ! ✨🎊",
            "🎉 **Portails du weekend ouverts !** Détente maximale autorisée ! Que vos éléments vous guident vers de super moments ensemble ! Prêts pour l'aventure ? 🌈⚡"
        ]
        
        self.morning_messages = [
            "🌅 **Bonjour à tous !** Nouvelle journée, nouvelles discussions ! Que votre élément vous donne l'énergie dont vous avez besoin pour cette journée. Café pour les Pyromancien(ne)s, thé pour les Aquamancien(ne)s ? 😉",
            "🌅 **Le soleil se lève sur les Élémentalistes !** Rechargés et prêts ? Que cette journée soit remplie de découvertes et d'échanges magiques ! Bon réveil ! ☕🫖",
            "🌅 **Réveillez-vous !** Une nouvelle aventure commence ! Bon matin à tous ! 🔥💧",
            "🌅 **Les cristaux brillent, c'est parti pour une nouvelle journée ! Grasse matinée pour certains chanceux ! ✨☕",
            "🌅 **Prêts à conquérir cette journée ? Que vos éléments vous accompagnent dans toutes vos discussions ! Bon matin ! 🌟🌱"
        ]
        
        self.load_data()

    def load_data(self):
        try:
            os.makedirs('data', exist_ok=True)
            with open(self.data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.last_daily_message_id = data.get('last_message_id')
        except FileNotFoundError:
            self.last_daily_message_id = None

    def save_data(self):
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump({'last_message_id': self.last_daily_message_id}, f)
        except Exception as e:
            print(f"Error saving daily messages data: {e}")

    async def send_message(self, messages):
        try:
            channel = self.bot.get_channel(self.general_channel_id)
            if not channel:
                return
            
            # Delete previous daily message if exists
            if self.last_daily_message_id:
                try:
                    old_message = await channel.fetch_message(self.last_daily_message_id)
                    await old_message.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass
            
            # Send new message
            new_message = await channel.send(random.choice(messages))
            self.last_daily_message_id = new_message.id
            self.save_data()
            
        except Exception as e:
            print(f"Error sending daily message: {e}")

    @tasks.loop(time=[time(8, 0), time(22, 0), time(0, 0)])
    async def scheduler(self):
        try:
            now = datetime.now(self.timezone)
            hour, weekday = now.hour, now.weekday()
            
            if hour == 8:  # Morning 8 AM
                await self.send_message(self.morning_messages)
            elif hour == 22 and weekday < 5:  # Evening 10 PM weekdays only
                await self.send_message(self.evening_messages)
            elif hour == 0 and weekday in [5, 6]:  # Midnight Saturday/Sunday only
                await self.send_message(self.weekend_messages)
        except Exception as e:
            print(f"Scheduler error: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.scheduler.is_running():
            self.scheduler.start()

async def setup(bot):
    await bot.add_cog(DailyMessages(bot))
