import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import maple_bot


class ExpCouponViewTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self, user_id=123):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            client=SimpleNamespace(exp_coupon_burning_preferences={}, persist_state=Mock()),
            response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), send_modal=AsyncMock()),
            original_response=AsyncMock(),
        )

    async def test_no_arguments_opens_controls(self):
        interaction = self.interaction()
        await maple_bot.exp_coupon_command.callback(interaction)
        view = interaction.response.send_message.call_args.kwargs["view"]
        self.assertIsInstance(view, maple_bot.ExpCouponView)
        self.assertTrue(view.calculate.disabled)
        self.assertEqual({option.value for option in view.coupon_select.options}, set(maple_bot.EXP_COUPONS))
        self.assertEqual(maple_bot.exp_coupon_command.parameters, [])

    async def test_modal_selects_and_calculation_reuse_same_panel(self):
        interaction = self.interaction()
        view = maple_bot.ExpCouponView(123, "X")
        modal = maple_bot.ExpCouponModal(view)
        modal.level_input._value = "270"
        modal.percent_input._value = "15.5"
        modal.count_input._value = "3,000"
        await modal.on_submit(interaction)
        self.assertEqual(view.values, (270, 15.5, 3000))
        self.assertEqual(view.coupon, "상급 EXP 교환권")
        self.assertFalse(view.calculate.disabled)
        await view.calculate.callback(interaction)
        embed = interaction.response.edit_message.call_args.kwargs["embed"]
        self.assertIn("사용 후", embed.description)
        self.assertIn("3,000", embed.description)
        self.assertIs(interaction.response.edit_message.call_args.kwargs["view"], view)

    async def test_invalid_modal_does_not_replace_valid_values(self):
        view = maple_bot.ExpCouponView(123, "X")
        view.values = (260, 0, 1)
        for level, percent, count in [("199", "0", "1"), ("260", "nan", "1"), ("260", "100", "1"), ("260", "0", "100000001"), ("abc", "0", "1")]:
            modal = maple_bot.ExpCouponModal(view)
            modal.level_input._value, modal.percent_input._value, modal.count_input._value = level, percent, count
            interaction = self.interaction()
            await modal.on_submit(interaction)
            self.assertEqual(view.values, (260, 0, 1))
            self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])

    async def test_burning_dropdown_is_remembered_and_coupon_selection_updates(self):
        interaction = self.interaction()
        view = maple_bot.ExpCouponView(123, "X")
        view.burning_select._values = ["비욘드버닝"]
        await view.burning_select.callback(interaction)
        self.assertEqual(interaction.client.exp_coupon_burning_preferences["123"], "비욘드버닝")
        view.coupon_select._values = ["상급 EXP 교환권"]
        await view.coupon_select.callback(interaction)
        self.assertEqual(view.coupon, "상급 EXP 교환권")
        self.assertTrue(next(option for option in view.coupon_select.options if option.value == view.coupon).default)
        await maple_bot.exp_coupon_command.callback(interaction)
        reopened = interaction.response.send_message.call_args.kwargs["view"]
        self.assertEqual(reopened.burning, "비욘드버닝")

    async def test_owner_and_expiry_protect_controls_and_open_modal(self):
        view = maple_bot.ExpCouponView(123, "X")
        self.assertFalse(await view.interaction_check(self.interaction(456)))
        modal = maple_bot.ExpCouponModal(view)
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        self.assertTrue(all(child.disabled for child in view.children))
        interaction = self.interaction()
        await modal.on_submit(interaction)
        self.assertIn("만료", interaction.response.send_message.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
