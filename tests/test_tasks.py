import unittest

from behancer_bot.tasks import COMMENT_TASK, LIKE_TASK, classify_task

COMMENT_TASK_TEXT = (
    "💬 Перейдите по ссылке на BEHANCE ПРОЕКТ из этого поста "
    "(https://www.facebook.com/share/p/1DboHwTYPH/), ВОЙДИТЕ В СВОЙ АККАУНТ "
    "и напишите уникальный комментарий к этому проекту на английском "
    "(минимальная длина – 10 символов). Чтобы подтвердить выполнение, "
    "нажмите на кнопку ✅"
)


class TaskClassificationTests(unittest.TestCase):
    def test_comment_instruction_is_a_comment_task(self):
        self.assertEqual(classify_task(COMMENT_TASK_TEXT), COMMENT_TASK)

    def test_like_instruction_stays_a_like_task(self):
        text = (
            "👍 Перейдите по ссылке и поставьте лайк проекту "
            "https://www.behance.net/gallery/123/Poster"
        )
        self.assertEqual(classify_task(text), LIKE_TASK)

    def test_empty_message_is_a_like_task(self):
        self.assertEqual(classify_task(""), LIKE_TASK)


if __name__ == "__main__":
    unittest.main()
