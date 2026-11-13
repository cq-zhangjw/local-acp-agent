import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import quick_commands


class QuickCommandTests(unittest.TestCase):
    def test_home_visibility_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = Path(temp_dir) / "quick_commands.json"
            with patch.object(quick_commands, "_FILE", storage):
                created = asyncio.run(
                    quick_commands.create_command(
                        quick_commands.QuickCommandIn(
                            title="Review",
                            content="Review the current changes",
                        )
                    )
                )
                command_id = created["item"]["id"]
                self.assertTrue(created["item"]["showOnHome"])

                updated = asyncio.run(
                    quick_commands.update_command(
                        command_id,
                        quick_commands.QuickCommandIn(
                            title="Review",
                            content="Review the current changes",
                            showOnHome=False,
                        ),
                    )
                )
                self.assertFalse(updated["item"]["showOnHome"])
                items = asyncio.run(quick_commands.list_commands())["items"]
                self.assertFalse(items[0]["showOnHome"])


if __name__ == "__main__":
    unittest.main()
