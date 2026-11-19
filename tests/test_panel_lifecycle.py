"""Regresión: envío real de discord.py con HTTP simulado, sin servidor/token."""
import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import discord
from discord import app_commands
from discord.webhook.async_ import async_context
from discord.ui.view import ViewStore
from simple_flow_ui import Home, SimpleUI

class PanelLifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client=discord.Client(intents=discord.Intents.none())
        self.client.tree=app_commands.CommandTree(self.client)
        self.ui=SimpleUI.__new__(SimpleUI)
        self.ui.bot=self.client
        self.ui.system=NS(guild=Mock(),staff=Mock())
        self.ui.begin=AsyncMock()
        self.ui.home=Home(self.ui)
        self.store=ViewStore(NS())
        self.store.add_view(self.ui.home)
        self.views=[]
        self.ident=100
        self.state=NS(allowed_mentions=None,http=NS(proxy=None,proxy_auth=None),store_view=self.save)
        self.adapter=NS(create_interaction_response=AsyncMock(return_value={}))
        self.token=async_context.set(self.adapter)
        SimpleUI.install(self.ui)
    def save(self,view,mid):
        self.views.append(view)
        self.store.add_view(view,mid)
    async def asyncTearDown(self):
        for v in [self.ui.home,*self.views]:v.stop()
        async_context.reset(self.token)
        await self.client.close()
    async def open(self):
        self.ident+=1
        i=NS(id=self.ident,token='offline',_session=None,_state=self.state,data={})
        i.response=discord.InteractionResponse(i)
        with patch('discord.interactions.InteractionCallbackResponse',return_value=NS(message_id=self.ident)):
            await self.client.tree.get_command('panel').get_command('abrir').callback(i)
        return i
    async def test_reopen_after_timeout_dispatches_button(self):
        await self.open()
        old=self.views[-1]
        self.assertEqual(old.timeout,900)
        old._dispatch_timeout()
        await asyncio.sleep(0)
        self.assertTrue(old.is_finished())
        i=await self.open()
        new=self.views[-1]
        self.assertIsNot(new,old)
        self.assertFalse(new.is_finished())
        button=next(b for b in new.children if b.custom_id=='wx:simple:start')
        await new._scheduled_task(button,i)
        self.ui.begin.assert_awaited_once_with(i)
    async def test_private_timeout_keeps_persistent_fallback(self):
        await self.open()
        self.views[-1]._dispatch_timeout()
        await asyncio.sleep(0)
        self.assertIsNone(self.ui.home.timeout)
        self.assertFalse(self.ui.home.is_finished())
        i=NS(message=None,data={},id=999)
        self.store.dispatch_view(2,'wx:simple:start',i)
        await asyncio.sleep(0)
        self.ui.begin.assert_awaited_once_with(i)
    async def test_original_shared_instance_reproduces_failure(self):
        v=Home(self.ui)
        i=await self.open()
        v.stop()
        i.response=discord.InteractionResponse(i)
        count=len(self.views)
        with patch('discord.interactions.InteractionCallbackResponse',return_value=NS(message_id=999)):
            await i.response.send_message('Panel',view=v,ephemeral=True)
        self.assertEqual(len(self.views),count)

if __name__=='__main__':unittest.main()
