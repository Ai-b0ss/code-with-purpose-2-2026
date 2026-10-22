"""Deny-by-default coordinator bearer auth and explicit model input boundary."""
import os
import secrets
import re
from fastapi import Header, HTTPException


def require_coordinator(authorization: str | None = Header(default=None)):
    expected = os.getenv('GIM_COORDINATOR_TOKEN', '')
    supplied = authorization[7:] if isinstance(authorization, str) and authorization.startswith('Bearer ') else ''
    if len(expected) < 32 or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, 'Требуется авторизация координатора.', headers={'WWW-Authenticate': 'Bearer'})


MODEL_FIELDS = frozenset('age_over_18 motivation experience expectations availability_hours_month other_commitments desired_direction followup_answer status university university_other faculty course scientific_interests foreign_languages hobbies availability_schedule_type availability_days other_volunteer_programs source followup_answers followup_questions clarification_round'.split())


def model_answers(data):
    # Names/contact values can also have been pasted into free text. Exact identity
    # values and common email/URL/phone patterns are masked recursively. This is
    # minimisation, not a guarantee of full free-text anonymisation.
    identities = [data.applicant_name, data.email, data.vk_url, data.birth_date]
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(v) for key, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if not isinstance(value, str):
            return value
        for identity in identities:
            if identity and len(identity) > 3:
                value = re.sub(re.escape(identity), '[скрыто]', value, flags=re.I)
        value = re.sub(r'[\w.+-]+@[\w.-]+\.[\w-]+', '[email скрыт]', value)
        value = re.sub(r'https?://\S+|(?:vk\.com|t\.me)/\S+', '[ссылка скрыта]', value)
        value = re.sub(r'(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)', '[номер скрыт]', value)
        return value
    answers = clean(data.model_dump(include=MODEL_FIELDS))
    answers.update(contact_available=bool(data.email.strip() or data.vk_url.strip()),
                   email_available=bool(data.email.strip()), vk_available=bool(data.vk_url.strip()))
    return answers


def public_receipt(application_id, created, data):
    from backend.candidate_feedback import feedback
    return {'application_id': application_id, 'status': 'registered', 'created': created,
            'message': 'Анкета зарегистрирована. Решение принимает координатор после собеседования.',
            'candidate_feedback': feedback(data)}
