from __future__ import annotations

import json
import os
import random
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime

import requests


API_VERSION = os.getenv("VK_API_VERSION", "5.199")
TOKEN = os.getenv("VK_ACCESS_TOKEN", "").strip()
GROUP_ID = int(os.getenv("VK_GROUP_ID", "0") or "0")
ADMIN_PEER_ID = int(os.getenv("ADMIN_PEER_ID", "0") or "0")

STATE_FILE = os.getenv("STATE_FILE", "state.json")
LEADS_FILE = os.getenv("LEADS_FILE", "leads.jsonl")


if not TOKEN:
    raise RuntimeError("VK_ACCESS_TOKEN is required")
if not GROUP_ID:
    raise RuntimeError("VK_GROUP_ID is required")


def log(message: str) -> None:
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


def vk_call(method: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["access_token"] = TOKEN
    params["v"] = API_VERSION
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request("https://api.vk.com/method/" + method, data=data)
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        err = payload["error"]
        raise RuntimeError(f"VK API {method}: {err.get('error_code')} {err.get('error_msg')}")
    return payload["response"]


def keyboard(buttons: list[list[tuple[str, str]]], one_time: bool = False) -> str:
    return json.dumps(
        {
            "one_time": one_time,
            "inline": False,
            "buttons": [
                [
                    {
                        "action": {
                            "type": "text",
                            "label": label,
                            "payload": json.dumps({"cmd": cmd}, ensure_ascii=False),
                        },
                        "color": "primary" if cmd in {"diagnostic", "start"} else "secondary",
                    }
                    for label, cmd in row
                ]
                for row in buttons
            ],
        },
        ensure_ascii=False,
    )


MAIN_KEYBOARD = keyboard(
    [
        [("Записаться на диагностику", "diagnostic")],
        [("Как всё работает", "how"), ("Уровни", "levels")],
        [("Цены", "price"), ("Адрес", "address")],
        [("Мини-группы", "groups"), ("Персонально", "personal")],
    ]
)


FAQ = {
    "how": (
        "Как работает re:старт:\n\n"
        "1. Бесплатная диагностика.\n"
        "2. Определение стартовой точки.\n"
        "3. Подбор уровня сопровождения.\n"
        "4. Маршрут на 4 недели.\n"
        "5. Контроль техники и прогресса.\n"
        "6. Следующий этап.\n\n"
        "Вы задаёте цель — мы создаём маршрут."
    ),
    "levels": (
        "У нас 3 уровня:\n\n"
        "Базовый — 12 мини-групп в месяц.\n"
        "Средний — 4 персональные + 8 мини-групп.\n"
        "Продвинутый — 12 персональных + доступ к мини-группам.\n\n"
        "Уровень лучше подбирать после диагностики."
    ),
    "price": (
        "Стоимость зависит от уровня сопровождения. "
        "Чтобы не предлагать неподходящий формат, мы начинаем с бесплатной диагностики и после неё рекомендуем маршрут.\n\n"
        "Напишите «Записаться» — соберу заявку."
    ),
    "address": (
        "Для теста здесь стоит заглушка. В боевом сообществе добавим адрес клуба, ориентиры, график и ссылку на карты.\n\n"
        "Пока можно проверить сценарий записи: напишите «Записаться»."
    ),
    "groups": (
        "Мини-группы — до 4 человек.\n\n"
        "Так тренер видит каждого, контролирует технику и адаптирует упражнения под уровень. "
        "Это камернее и спокойнее, чем обычные групповые тренировки."
    ),
    "personal": (
        "Персональный формат подходит, если нужна максимальная индивидуализация: конкретная цель, ограничения, приватность или быстрый старт.\n\n"
        "Можно выбрать персональный уровень или комбинировать персональные тренировки с мини-группами."
    ),
}


@dataclass
class Lead:
    created_at: str
    peer_id: int
    name: str
    goal: str
    limits: str
    time: str
    phone: str
    source: str = "VK cloud bot"


def normalize(text: str) -> str:
    return text.strip().lower().replace("ё", "е")


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send(peer_id: int, text: str, kb: str | None = MAIN_KEYBOARD) -> None:
    params = {
        "peer_id": peer_id,
        "random_id": random.randint(1, 2_000_000_000),
        "message": text,
    }
    if kb:
        params["keyboard"] = kb
    vk_call("messages.send", params)


def notify_admin(lead: Lead) -> None:
    if not ADMIN_PEER_ID:
        return
    text = (
        "Новая заявка на диагностику re:старт\n\n"
        f"Имя: {lead.name}\n"
        f"Цель: {lead.goal}\n"
        f"Ограничения: {lead.limits}\n"
        f"Удобное время: {lead.time}\n"
        f"Телефон: {lead.phone}\n"
        f"Источник: {lead.source}\n"
        f"VK peer_id: {lead.peer_id}"
    )
    try:
        send(ADMIN_PEER_ID, text, kb=None)
    except Exception as exc:
        log(f"admin_notify_failed {type(exc).__name__}: {exc}")


def start_lead(peer_id: int, state: dict) -> None:
    state[str(peer_id)] = {"flow": "lead", "step": "name", "data": {}}
    save_state(state)
    send(
        peer_id,
        "Отлично, запишем вас на бесплатную диагностику.\n\n"
        "Вопрос 1 из 5: как к вам обращаться?",
        kb=None,
    )


def handle_lead(peer_id: int, text: str, state: dict) -> None:
    user = state.get(str(peer_id), {})
    step = user.get("step")
    data = user.setdefault("data", {})

    if normalize(text) in {"отмена", "стоп", "меню"}:
        state.pop(str(peer_id), None)
        save_state(state)
        send(peer_id, "Ок, вернулись в меню. Чем помочь?")
        return

    if step == "name":
        data["name"] = text.strip()
        user["step"] = "goal"
        send(peer_id, "Вопрос 2 из 5: какая главная цель?\n\nНапример: вес, тонус, спина, энергия, регулярность.", kb=None)
    elif step == "goal":
        data["goal"] = text.strip()
        user["step"] = "limits"
        send(peer_id, "Вопрос 3 из 5: есть ли ограничения, боли или дискомфорт?", kb=None)
    elif step == "limits":
        data["limits"] = text.strip()
        user["step"] = "time"
        send(peer_id, "Вопрос 4 из 5: когда удобнее прийти — утро, день, вечер или выходные?", kb=None)
    elif step == "time":
        data["time"] = text.strip()
        user["step"] = "phone"
        send(peer_id, "Вопрос 5 из 5: оставьте телефон для подтверждения записи.", kb=None)
    elif step == "phone":
        data["phone"] = text.strip()
        lead = Lead(
            created_at=datetime.now().isoformat(timespec="seconds"),
            peer_id=peer_id,
            name=data.get("name", ""),
            goal=data.get("goal", ""),
            limits=data.get("limits", ""),
            time=data.get("time", ""),
            phone=data.get("phone", ""),
        )
        with open(LEADS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(lead), ensure_ascii=False) + "\n")
        notify_admin(lead)
        state.pop(str(peer_id), None)
        save_state(state)
        send(
            peer_id,
            "Спасибо. Заявка на диагностику сохранена.\n\n"
            "Администратор свяжется с вами, уточнит удобное время и ответит на вопросы.",
        )
        log(f"lead_saved peer_id={peer_id} name={lead.name!r} goal={lead.goal!r}")
    else:
        state.pop(str(peer_id), None)
        save_state(state)
        send(peer_id, "Сценарий сброшен. Напишите «Записаться», чтобы начать заново.")


def handle_message(peer_id: int, text: str, state: dict) -> None:
    if state.get(str(peer_id), {}).get("flow") == "lead":
        handle_lead(peer_id, text, state)
        return

    n = normalize(text)
    if any(word in n for word in ["запис", "диагностик", "старт"]):
        start_lead(peer_id, state)
    elif any(word in n for word in ["как", "работ", "маршрут"]):
        send(peer_id, FAQ["how"])
    elif any(word in n for word in ["уров", "тариф", "формат"]):
        send(peer_id, FAQ["levels"])
    elif any(word in n for word in ["цен", "стоим", "сколько"]):
        send(peer_id, FAQ["price"])
    elif any(word in n for word in ["адрес", "где", "карта"]):
        send(peer_id, FAQ["address"])
    elif any(word in n for word in ["мини", "групп"]):
        send(peer_id, FAQ["groups"])
    elif any(word in n for word in ["персон", "индивиду"]):
        send(peer_id, FAQ["personal"])
    elif any(word in n for word in ["привет", "здрав", "меню", "начать"]):
        send(
            peer_id,
            "Здравствуйте. Это re:старт.\n\n"
            "Мы помогаем женщинам 35–50+ возвращать форму, тонус и энергию через диагностику, маршрут на 4 недели и камерные тренировки без толпы.\n\n"
            "Выберите кнопку ниже или напишите «Записаться».",
        )
    else:
        send(
            peer_id,
            "Я могу помочь с записью на диагностику и ответить на ключевые вопросы.\n\n"
            "Напишите «Записаться», «Цены», «Уровни», «Мини-группы», «Персонально» или «Адрес».",
        )


def configure_long_poll() -> None:
    vk_call(
        "groups.setLongPollSettings",
        {
            "group_id": GROUP_ID,
            "enabled": 1,
            "message_new": 1,
            "api_version": API_VERSION,
        },
    )
    log("long_poll_settings=enabled")


def main() -> None:
    configure_long_poll()
    state = load_state()
    server = vk_call("groups.getLongPollServer", {"group_id": GROUP_ID})
    key = server["key"]
    server_url = server["server"]
    ts = server["ts"]
    log(f"bot_started group_id={GROUP_ID}")

    while True:
        try:
            response = requests.get(
                server_url,
                params={"act": "a_check", "key": key, "ts": ts, "wait": 25},
                timeout=35,
            ).json()
            if "failed" in response:
                log(f"longpoll_failed={response}")
                server = vk_call("groups.getLongPollServer", {"group_id": GROUP_ID})
                key = server["key"]
                server_url = server["server"]
                ts = server["ts"]
                continue
            ts = response["ts"]
            for update in response.get("updates", []):
                if update.get("type") != "message_new":
                    continue
                msg = update.get("object", {}).get("message", {})
                peer_id = int(msg.get("peer_id"))
                text = msg.get("text", "")
                if not text:
                    continue
                log(f"message peer_id={peer_id} text={text[:80]!r}")
                handle_message(peer_id, text, state)
                state = load_state()
        except KeyboardInterrupt:
            log("bot_stopped_keyboard")
            return
        except Exception as exc:
            log(f"bot_error {type(exc).__name__}: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
