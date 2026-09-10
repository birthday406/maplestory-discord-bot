import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from patch_revision_preview import page, PatchRevisionView


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_pages_and_persistent_buttons(self):
        view = PatchRevisionView()
        self.assertTrue(view.is_persistent())
        self.assertEqual([button.label for button in view.children], ['하야토', '칸나', '린', '묵현'])
        for index, expected in enumerate(('2,430', '3,525', '2,073', '1,620')):
            self.assertIn(f'900% → **{expected}%**', page(index).description)
            self.assertLess(len(page(index)), 6000)
            interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
            await view.children[index].callback(interaction)
            self.assertEqual(interaction.response.edit_message.await_args.kwargs['embed'].to_dict(), page(index).to_dict())
        self.assertNotIn('스틱스', page(0).description)
        self.assertIn('2,503% → **1,427%**', page(3).description)
        self.assertIn('1,298% → **586%**', page(3).description)
