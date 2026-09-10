import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import maple_bot as bot


class CalculatorPanelTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self, user_id=1):
        return SimpleNamespace(user=SimpleNamespace(id=user_id),
            client=SimpleNamespace(symbol_calculator_preferences={}, persist_state=Mock()),
            type=discord.InteractionType.component,
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock()),
            original_response=AsyncMock())

    async def test_public_commands_have_no_options_and_open_owned_panels(self):
        for command in (bot.hexa_command, bot.growth_potion_command, bot.epic_dungeon_command, bot.symbol_calculator_command):
            self.assertEqual(command.parameters, [])
            interaction = self.interaction()
            await command.callback(interaction)
            call = interaction.response.send_message.await_args
            panel = call.kwargs["view"]
            self.assertTrue(call.kwargs["ephemeral"])
            self.assertTrue(panel.calculate.disabled)
            self.assertTrue(any(isinstance(child, discord.ui.Select) for child in panel.children))
            self.assertFalse(await panel.interaction_check(self.interaction(2)))
            panel.stop()

    async def test_all_calculators_reuse_results_in_same_message(self):
        cases = (
            (bot.hexa_calculator, {"current_level":0,"target_level":10}),
            (bot.growth_potion_calculator, {"current_level":245,"current_exp_percent":0,"count":1}),
            (bot.epic_dungeon_calculator, {"current_level":290,"current_exp_percent":0}),
            (bot.symbol_growth_calculator, {"current_level":1,"current_growth":0,"target_level":2}),
        )
        for calculator, values in cases:
            panel = bot.CalculatorView(1, calculator)
            modal = bot.CalculatorNumbersModal(panel)
            for key, value in values.items():
                modal.inputs[key]._value = str(value)
            interaction = self.interaction()
            await modal.on_submit(interaction)
            self.assertEqual(panel.numbers, values)
            self.assertFalse(panel.calculate.disabled)
            interaction.response.edit_message.reset_mock()
            await panel.calculate.callback(interaction)
            interaction.response.edit_message.assert_awaited_once()
            interaction.response.send_message.assert_not_awaited()
            panel.stop()

    async def test_invalid_input_and_expiration_preserve_values(self):
        panel = bot.CalculatorView(1, bot.growth_potion_calculator)
        panel.numbers = {"current_level":245,"current_exp_percent":0,"count":1}
        for bad in ("nan", "inf", "100", "-1"):
            modal = bot.CalculatorNumbersModal(panel)
            for key, field in modal.inputs.items():
                field._value = str(panel.numbers[key])
            modal.inputs["current_exp_percent"]._value = bad
            interaction = self.interaction()
            await modal.on_submit(interaction)
            interaction.response.send_message.assert_awaited_once()
            self.assertEqual(panel.numbers["current_exp_percent"], 0)
        panel.message = SimpleNamespace(edit=AsyncMock())
        await panel.on_timeout()
        self.assertTrue(all(child.disabled for child in panel.children))
        self.assertFalse(await panel.interaction_check(self.interaction()))
        panel.stop()

    async def test_dropdown_changes_and_saved_symbol_settings(self):
        panel = bot.CalculatorView(1, bot.symbol_growth_calculator,
            {"potion_level": 6, "elanos": "적용"})
        self.assertEqual(panel.selections["potion_level"].value, 6)
        self.assertEqual(panel.selections["elanos"].value, "적용")
        select = next(child for child in panel.children if isinstance(child, discord.ui.Select))
        self.assertLessEqual(len(select.options), 25)
        select._values = ["1"]
        interaction = self.interaction()
        await select.callback(interaction)
        self.assertEqual(panel.selections["region"].name, select.options[1].label)
        self.assertTrue(select.options[1].default)
        interaction.response.edit_message.assert_awaited_once()
        panel.stop()
