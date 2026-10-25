import os
import unittest
from unittest.mock import patch

import main


class MainNotificationConfigTests(unittest.TestCase):
    def test_pushplus_multiple_channels_are_built_once_each(self):
        with patch.dict(
            os.environ,
            {
                "NOTIFY_PROVIDER": "pushplus",
                "PUSHPLUS_TOKEN": "test-token",
                "PUSHPLUS_CHANNELS": "wechat,qq,wechat",
            },
            clear=True,
        ):
            notifiers = main.build_notifiers(required=True)

        self.assertEqual([item.notification_id for item in notifiers], ["pushplus:wechat", "pushplus:qq"])


if __name__ == "__main__":
    unittest.main()
