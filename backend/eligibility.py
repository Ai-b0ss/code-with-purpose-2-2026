"""Calendar eligibility; Feb 29 birthdays reach age on March 1 in common years."""
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo


def moscow_today():
    return datetime.now(ZoneInfo('Europe/Moscow')).date()


def validate_birth_date(value: str, as_of: date | None = None) -> int:
    today = as_of or moscow_today()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Укажите дату рождения в формате ГГГГ-ММ-ДД.')
    try:
        born = date.fromisoformat(value)
    except ValueError:
        raise ValueError('Такой даты рождения не существует.') from None
    if born > today:
        raise ValueError('Дата рождения не может быть в будущем.')
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    if age < 18:
        raise ValueError('Участие доступно с 18 лет. Проверьте дату рождения.')
    return age
