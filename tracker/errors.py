"""Ошибки проекта."""


class VkError(Exception):
    """VK API вернул ошибку."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"VK error {code}: {message}")
        self.code = code
        self.message = message


class TargetError(Exception):
    """Не удалось выбрать страницу для слежения (текст для пользователя)."""
