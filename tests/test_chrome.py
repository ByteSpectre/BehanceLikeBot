import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from behancer_bot.chrome import ChromeController
from behancer_bot.config import AppConfig
from behancer_bot.control import RunControl


class ChromeControllerTests(unittest.TestCase):
    def test_each_profile_uses_its_own_debug_port(self):
        default = ChromeController(
            AppConfig(chrome_profile="Default"), RunControl(), Mock(), Mock()
        )
        profile_one = ChromeController(
            AppConfig(chrome_profile="Profile 1"), RunControl(), Mock(), Mock()
        )
        profile_three = ChromeController(
            AppConfig(chrome_profile="Profile 3"), RunControl(), Mock(), Mock()
        )
        self.assertEqual(default._debug_port(), 9222)
        self.assertEqual(profile_one._debug_port(), 9223)
        self.assertEqual(profile_three._debug_port(), 9225)

    def test_always_uses_separate_profile_copy(self):
        controller = ChromeController(AppConfig(), RunControl(), Mock(), Mock())
        automation_profile = Path("automation-profile")
        with (
            patch.object(controller, "_attach_to_debug_chrome", return_value=False),
            patch.object(
                controller,
                "_prepare_automation_profile",
                return_value=automation_profile,
            ),
            patch.object(controller, "_launch_with_profile") as launch,
        ):
            controller.start()
        launch.assert_called_once_with(automation_profile, cloned=True)

    def test_debuggable_chrome_is_attached_without_launching_another(self):
        controller = ChromeController(AppConfig(), RunControl(), Mock(), Mock())
        with (
            patch.object(controller, "_attach_to_debug_chrome", return_value=True),
            patch.object(controller, "_launch_with_profile") as launch,
        ):
            controller.start()
        launch.assert_not_called()

    def test_finds_project_inside_facebook_redirect(self):
        controller = ChromeController(AppConfig(), RunControl(), Mock(), Mock())
        controller.driver = Mock()
        controller.driver.execute_script.return_value = [
            (
                "https://l.facebook.com/l.php?"
                "u=https%3A%2F%2Fwww.behance.net%2Fgallery%2F777%2FProject"
                "&h=tracking"
            )
        ]
        self.assertEqual(
            controller._find_behance_link(),
            "https://www.behance.net/gallery/777/Project",
        )

    def test_waits_before_searching_external_page_for_behance_link(self):
        control = RunControl()
        control.sleep = Mock()
        controller = ChromeController(AppConfig(), control, Mock(), Mock())
        controller.driver = Mock()
        controller.driver.current_url = "https://www.facebook.com/share/p/example"
        project_url = "https://www.behance.net/gallery/888/Project"
        with (
            patch.object(controller, "_navigate"),
            patch.object(controller, "_find_behance_link", return_value=project_url),
        ):
            result = controller._resolve_to_project(controller.driver.current_url)
        self.assertEqual(result, project_url)
        control.sleep.assert_called_once_with(5)

    def test_checks_explicit_links_until_one_opens_behance_project(self):
        controller = ChromeController(AppConfig(), RunControl(), Mock(), Mock())
        links = [
            ("https://example.com/not-behance", "https://example.com/not-behance"),
            ("https://lnkd.in/project", "https://lnkd.in/project"),
        ]
        project_url = "https://www.behance.net/gallery/999/Project"
        with (
            patch.object(controller, "_collect_explicit_links", return_value=links),
            patch.object(
                controller,
                "_visit_explicit_link",
                side_effect=[None, project_url],
            ) as visit,
        ):
            result = controller._try_explicit_links()
        self.assertEqual(result, project_url)
        self.assertEqual(visit.call_count, 2)

    def test_comment_task_posts_comment_instead_of_like(self):
        control = RunControl()
        control.sleep = Mock()
        controller = ChromeController(AppConfig(), control, Mock(), Mock())
        controller.driver = Mock()
        project_url = "https://www.behance.net/gallery/1/Project"
        controller.driver.current_url = project_url
        with (
            patch.object(controller, "_resolve_to_project", return_value=project_url),
            patch.object(controller, "_navigate"),
            patch.object(controller, "_post_comment", return_value=True) as post,
            patch.object(controller, "_appreciate") as appreciate,
        ):
            self.assertTrue(controller.perform_comment_task([project_url]))
        post.assert_called_once_with()
        appreciate.assert_not_called()

    def test_like_task_still_uses_appreciate(self):
        control = RunControl()
        control.sleep = Mock()
        controller = ChromeController(AppConfig(), control, Mock(), Mock())
        controller.driver = Mock()
        project_url = "https://www.behance.net/gallery/1/Project"
        controller.driver.current_url = project_url
        with (
            patch.object(controller, "_resolve_to_project", return_value=project_url),
            patch.object(controller, "_navigate"),
            patch.object(controller, "_post_comment") as post,
            patch.object(controller, "_appreciate", return_value=True) as appreciate,
        ):
            self.assertTrue(controller.perform_task([project_url]))
        appreciate.assert_called_once_with()
        post.assert_not_called()

    def test_reloads_page_until_comment_field_appears(self):
        control = RunControl()
        control.sleep = Mock()
        control.checkpoint = Mock()
        controller = ChromeController(AppConfig(), control, Mock(), Mock())
        controller.driver = Mock()
        field = Mock()
        with (
            patch.object(controller, "_scroll_to_comments"),
            patch.object(
                controller, "_find_comment_field", side_effect=[None, None, field]
            ),
            patch.object(controller, "_click_comments_opener", return_value=False),
            patch.object(controller, "_reload_page") as reload_page,
        ):
            self.assertIs(controller._wait_for_comment_field(), field)
        self.assertEqual(reload_page.call_count, 2)

    def test_prefers_project_comment_textarea_over_other_textareas(self):
        other = Mock()
        other.get_attribute.return_value = "TextArea-input-AAA TextArea-textarea"
        other.is_displayed.return_value = True
        comment = Mock()
        comment.get_attribute.return_value = (
            "TextArea-input-GZ6 ProjectCommentInput-commentTextArea-Vcg "
            "TextArea-textarea"
        )
        comment.is_displayed.return_value = True
        hidden = Mock()
        hidden.get_attribute.return_value = (
            "ProjectCommentInput-commentTextArea-OLD TextArea-textarea"
        )
        hidden.is_displayed.return_value = False
        self.assertIs(
            ChromeController._prefer_comment_field([other, hidden, comment]),
            comment,
        )


if __name__ == "__main__":
    unittest.main()
