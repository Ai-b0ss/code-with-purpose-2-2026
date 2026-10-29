from __future__ import annotations

from vk_api.keyboard import VkKeyboard, VkKeyboardColor


# -----------------------------------------------------------------------------
# 3. КЛАВИАТУРЫ ВКОНТАКТЕ
# -----------------------------------------------------------------------------

class VkKeyboards:
    """Генераторы клавиатур VkKeyboard."""

    @staticmethod
    def start() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Начать заполнение", color=VkKeyboardColor.PRIMARY)
        return kb.get_keyboard()

    @staticmethod
    def consent() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Согласен(на), мне есть 18 лет", color=VkKeyboardColor.POSITIVE)
        kb.add_line()
        kb.add_button("Отказаться", color=VkKeyboardColor.NEGATIVE)
        return kb.get_keyboard()

    @staticmethod
    def skip(label: str = "Пропустить") -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button(label, color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def course() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Выпускник / Не учусь", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def languages() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Английский (B1-B2)", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Английский (C1-C2)", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Немецкий (B1+)", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Только русский / Нет", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def experience() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Опыта нет, но хочу научиться!", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Пропустить", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def directions_inline() -> str:
        kb = VkKeyboard(inline=True)
        kb.add_button("🏛️ Фондовое", color=VkKeyboardColor.PRIMARY)
        kb.add_button("🎭 Проектное (квесты)", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("🎪 Организационное", color=VkKeyboardColor.PRIMARY)
        kb.add_button("🤝 Инклюзивное", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("📸 Медиа-волонтёрство", color=VkKeyboardColor.PRIMARY)
        kb.add_button("🤔 Пока не определился", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def hours() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("8", color=VkKeyboardColor.PRIMARY)
        kb.add_button("16", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("24", color=VkKeyboardColor.PRIMARY)
        kb.add_button("32", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def schedule() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Гибкий график", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Регулярный", color=VkKeyboardColor.PRIMARY)
        return kb.get_keyboard()

    @staticmethod
    def days() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Выходные (Сб, Вс)", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Будни", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Любые дни", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Плавающий график", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def commitments() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Только учёба", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Совмещаю с работой", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Свободный график", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def clarification_skip() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Пропустить вопрос", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def finished() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Заполнить новую анкету", color=VkKeyboardColor.PRIMARY)
        return kb.get_keyboard()

    @staticmethod
    def retry() -> str:
        kb = VkKeyboard(inline=True)
        kb.add_button("Повторить отправку", color=VkKeyboardColor.PRIMARY)
        return kb.get_keyboard()

    @staticmethod
    def empty() -> str:
        return VkKeyboard.get_empty_keyboard()


