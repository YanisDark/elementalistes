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
        
        self.data = {
            'last_sent': {'morning': None, 'evening': None, 'weekend': None},
            'current_message_ids': []
        }
        
        self.load_data()

    def load_data(self):
        try:
            os.makedirs('data', exist_ok=True)
            with open(self.data_file, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
                self.data['last_sent'] = loaded_data.get('last_sent', {'morning': None, 'evening': None, 'weekend': None})
                self.data['current_message_ids'] = loaded_data.get('current_message_ids', [])
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {
                'last_sent': {'morning': None, 'evening': None, 'weekend': None},
                'current_message_ids': []
            }

    def save_data(self):
        try:
            os.makedirs('data', exist_ok=True)
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print(f"Error saving daily messages data: {e}")

    async def cleanup_old_messages(self):
        """Delete all stored daily message IDs"""
        if not self.data['current_message_ids']:
            return
            
        channel = self.bot.get_channel(self.general_channel_id)
        if not channel:
            return
            
        messages_to_remove = []
        for message_id in self.data['current_message_ids']:
            try:
                old_message = await channel.fetch_message(message_id)
                await old_message.delete()
                print(f"Deleted old daily message: {message_id}")
            except (discord.NotFound, discord.HTTPException, discord.Forbidden):
                pass
            finally:
                messages_to_remove.append(message_id)
        
        # Clear the message IDs list
        for msg_id in messages_to_remove:
            if msg_id in self.data['current_message_ids']:
                self.data['current_message_ids'].remove(msg_id)

    async def send_message(self, messages, message_type):
        try:
            channel = self.bot.get_channel(self.general_channel_id)
            if not channel:
                print(f"Channel {self.general_channel_id} not found")
                return
            
            # Clean up old messages first
            await self.cleanup_old_messages()
            
            # Send new message
            new_message = await channel.send(random.choice(messages))
            
            # Store the new message ID
            self.data['current_message_ids'].append(new_message.id)
            
            # Update last sent time
            now = datetime.now(self.timezone)
            self.data['last_sent'][message_type] = now.strftime('%Y-%m-%d')
            
            # Save data to file
            self.save_data()
            
            print(f"Sent {message_type} message to channel {channel.name} at {now.strftime('%H:%M %Z')}")
            
        except Exception as e:
            print(f"Error sending daily message: {e}")

    @tasks.loop(minutes=3)
    async def scheduler(self):
        try:
            if not self.bot.is_ready():
                return
                
            now = datetime.now(self.timezone)
            today = now.strftime('%Y-%m-%d')
            hour = now.hour
            weekday = now.weekday()  # 0=Monday, 6=Sunday
            
            # Morning message (8 AM on weekdays only)
            if (hour >= 8 and hour < 9 and weekday <= 4 and 
                self.data['last_sent'].get('morning') != today):
                await self.send_message(self.morning_messages, 'morning')
            
            # Weekend message (10 AM on Saturday and Sunday)
            elif (hour >= 10 and hour < 11 and weekday in [5, 6] and
                  self.data['last_sent'].get('weekend') != today):
                await self.send_message(self.weekend_messages, 'weekend')
            
            # Evening message (10 PM on weekdays, midnight on weekends)
            elif weekday <= 4:  # Monday to Friday (0-4)
                if (hour >= 22 and hour < 23 and
                    self.data['last_sent'].get('evening') != today):
                    await self.send_message(self.evening_messages, 'evening')
            else:  # Saturday and Sunday (5-6)
                if (hour >= 0 and hour < 1 and
                    self.data['last_sent'].get('evening') != today):
                    await self.send_message(self.evening_messages, 'evening')
                
        except Exception as e:
            print(f"Scheduler error: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"DailyMessages loaded - Channel ID: {self.general_channel_id}")
        
        # Clean up any old messages on startup
        await asyncio.sleep(2)  # Wait for bot to be fully ready
        await self.cleanup_old_messages()
        
        if not self.scheduler.is_running():
            self.scheduler.start()
            print("Daily messages scheduler started")

    def cog_unload(self):
        self.scheduler.cancel()

async def setup(bot):
    await bot.add_cog(DailyMessages(bot))
